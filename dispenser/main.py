# Kairo dispenser agent - by Access Granted
#
# This is the program that lives on the Freenove ESP32-S3 and actually runs
# the box. Copy it to the board as main.py and it starts on power-up, with
# no laptop involved: the schedule and the pill counts are kept in flash, so
# an unplugged box that gets plugged back in carries on where it left off.
#
# WHY A SERIAL AGENT INSTEAD OF A WEB SERVER ON THE BOARD
# The website needs to send a command and see the servo move a fraction of a
# second later, at a venue whose WiFi cannot be trusted and often blocks one
# device from reaching another. So the board speaks a line-delimited JSON
# protocol over the USB serial port it is already plugged into, and
# site/bridge.py on the laptop translates that to HTTP for the browser.
# Nothing has to be re-flashed, reset or unplugged between commands.
#
# THE PROTOCOL
# One JSON object per line, both directions. Anything the board prints that
# is not a JSON object is treated as a console line by the bridge and shown
# in the website debug panel, so ordinary print() debugging still works.
#
#   in   {"c":"hello"}                     identify, report which pins answered
#        {"c":"time","t":[Y,M,D,h,m,s]}    set the RTC (no battery on this board)
#        {"c":"sched","i":0,"times":["08:00","20:00"],"label":"...","dose":1}
#        {"c":"pills","i":0,"n":42}        carer has refilled or recounted a tube
#        {"c":"dispense","i":0}            dispense that tube right now
#        {"c":"force"}                     fire the next scheduled dose now (demo)
#        {"c":"taken","i":0}               patient acknowledged the dose
#        {"c":"help"}                      patient pressed "I need help"
#        {"c":"snooze","m":10}             push the current reminder back
#        {"c":"chime","name":"bells"}       play a sound now (preview)
#        {"c":"servo","i":0,"a":90}         hold one servo at an angle
#        {"c":"sweep","i":0}               one gate cycle, no dose logged
#        {"c":"cfg", ...}                  volume, low_at, patient, gate angles
#        {"c":"state"}                     send a state frame immediately
#
#   out  {"e":"state", ...}                the whole picture, about once a second
#          includes "sonar":{"cm":42.0,"near":true} when the HC-SR04 answers
#        {"e":"ack","c":"dispense","ok":true}
#        {"e":"hello", ...}
#
# Ctrl-C at any time drops to the REPL for development; Ctrl-D then restarts
# this cleanly. The bridge sends exactly that pair when it connects, which is
# why a stale or crashed agent does not need a power cycle.

import json
import sys
import select
import time

import hardware
import lcdview

FW = "kairo-1.0"
STATE_FILE = "/kairo.json"
TUBE_COUNT = 3

# How the box behaves. All overridable from the website with {"c":"cfg"}.
CONFIG = {
    "patient": "Patient",
    "low_at": 10,          # pills below this and the carer gets warned
    "remind_every": 180,   # seconds between repeat chimes while a dose waits
    "remind_limit": 3,     # how many repeats before it is called missed
    "missed_after": 900,   # seconds after which an untaken dose is missed
    "volume": 0.35,
    "chime": "jingle",     # which sound plays for a due dose
    "catch_up": 3600,      # see fire_due() - the safety window, in seconds
}

DEFAULT_TUBES = [
    {"label": "Metformin", "dose": 1, "times": ["08:00", "20:00"], "count": 42},
    {"label": "Ramipril", "dose": 1, "times": ["08:00"], "count": 18},
    {"label": "Atorvastatin", "dose": 1, "times": ["20:00"], "count": 7},
]

tubes = []
events = []          # newest last, capped at EVENT_CAP
EVENT_CAP = 40

mode = "idle"        # idle | dispensing | due | taken
active = None        # which tube the current dose came from
due_at = 0           # ticks_ms when the dose fired
reminders = 0
taken_until = 0      # ticks_ms to hold the THANK YOU screen until
fired = {}           # "tube|YYYY-MM-DD HH:MM" -> True, cleared at midnight
_fired_day = None    # which date the keys in `fired` belong to
seq = 0              # event counter, lets the bridge spot dropped frames

_dirty = False
_dirty_at = 0
_last_state_ms = 0
_blink = 0

near = False         # is somebody in front of the box right now?
distance_cm = None   # last good ultrasonic reading
_last_sonar_ms = 0


# --- storage -----------------------------------------------------------

def load():
    """Read the saved schedule, or fall back to a sensible demo one."""
    global tubes, events
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
        tubes = saved.get("tubes") or []
        events = saved.get("events") or []
        for key, value in (saved.get("config") or {}).items():
            if key in CONFIG:
                CONFIG[key] = value
    except Exception:
        tubes = []
    # Normalise, so a hand-edited or truncated file cannot crash the loop.
    while len(tubes) < TUBE_COUNT:
        tubes.append(dict(DEFAULT_TUBES[len(tubes)]))
    tubes = tubes[:TUBE_COUNT]
    for t in tubes:
        t.setdefault("label", "Medicine")
        t.setdefault("dose", 1)
        t.setdefault("times", [])
        t.setdefault("count", 0)


def apply_sound():
    """Push the saved volume and chime choice into the hardware layer."""
    hardware.VOLUME = float(CONFIG.get("volume", 0.35))
    CONFIG["chime"] = hardware.set_chime(CONFIG.get("chime", "jingle"))


def save_now():
    global _dirty
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"tubes": tubes, "events": events[-EVENT_CAP:],
                       "config": CONFIG}, f)
        _dirty = False
    except Exception as e:
        log("save failed: %r" % e)


def touch():
    """Mark state changed. The actual write is debounced by two seconds.

    Flash has a finite number of erase cycles and the carer editing a
    schedule generates a burst of changes, so writing on every keystroke
    would be wasteful for no benefit.
    """
    global _dirty, _dirty_at
    _dirty = True
    _dirty_at = time.ticks_ms()


# --- output ------------------------------------------------------------

def emit(obj):
    global seq
    seq += 1
    obj["n"] = seq
    try:
        print(json.dumps(obj))
    except Exception:
        pass


def log(message):
    emit({"e": "log", "msg": str(message)})


def add_event(kind, tube=None, extra=None):
    ev = {"k": kind, "at": stamp(), "ms": time.ticks_ms()}
    if tube is not None:
        ev["i"] = tube
        ev["label"] = tubes[tube].get("label", "")
    if extra:
        ev.update(extra)
    events.append(ev)
    del events[:-EVENT_CAP]
    emit({"e": "event", "ev": ev})
    touch()
    return ev


# --- clock -------------------------------------------------------------

def clock_set():
    """True once a real date has been pushed in.

    The ESP32-S3 has no battery-backed RTC, so out of a power cut it thinks
    it is 2000-01-01. Firing doses off that would be dangerous, so the
    scheduler refuses to run until the bridge sets the time.
    """
    return time.localtime()[0] >= 2024


def stamp():
    t = time.localtime()
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (t[0], t[1], t[2], t[3], t[4], t[5])


def today():
    t = time.localtime()
    return "%04d-%02d-%02d" % (t[0], t[1], t[2])


def secs_of_day():
    t = time.localtime()
    return t[3] * 3600 + t[4] * 60 + t[5]


def parse_hhmm(text):
    """'08:00' -> 28800 seconds. Returns None on anything malformed."""
    try:
        hh, mm = str(text).split(":")
        hh, mm = int(hh), int(mm)
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh * 3600 + mm * 60
    except Exception:
        pass
    return None


def next_dose():
    """The soonest upcoming dose: (tube_index, 'HH:MM', seconds_until).

    Returns (None, None, None) if no times are set at all.
    """
    now = secs_of_day()
    best = None
    for i, tube in enumerate(tubes):
        for hhmm in tube.get("times", []):
            target = parse_hhmm(hhmm)
            if target is None:
                continue
            delta = target - now
            if delta <= 0:
                delta += 86400          # already gone today, so tomorrow
            if best is None or delta < best[2]:
                best = (i, hhmm, delta)
    return best if best else (None, None, None)


def low_tube():
    """The tube in the worst shape, if any is below its threshold."""
    worst = None
    for i, tube in enumerate(tubes):
        count = int(tube.get("count", 0))
        if count <= CONFIG["low_at"]:
            if worst is None or count < int(tubes[worst].get("count", 0)):
                worst = i
    return worst


# --- the dose itself ---------------------------------------------------

def do_dispense(tube, reason="scheduled"):
    """Turn the dial, drop a dose, sound the chime, wait to be acknowledged."""
    global mode, active, due_at, reminders

    if not (0 <= tube < len(tubes)):
        return False

    entry = tubes[tube]
    dose = max(1, int(entry.get("dose", 1)))

    # Nothing to give. Turning the servo would look like a dose was
    # delivered, log one, and leave the patient waiting for a pill that is
    # not coming - so the machine says so instead and raises it with the
    # carer. The count is not touched.
    if int(entry.get("count", 0)) <= 0:
        mode, active = "empty", tube
        due_at = time.ticks_ms()
        push_lcd(force=True)
        add_event("empty", tube, {"why": reason})
        emit(state_frame())
        hardware.chime("alert")
        return False

    mode, active = "dispensing", tube
    push_lcd(force=True)

    moved = hardware.dispense(tube, dose)

    # The count is decremented whether or not the servo answered. If the
    # motor is unplugged during a build the schedule still has to stay
    # honest about what the carer loaded, and an over-count is the more
    # dangerous error of the two.
    entry["count"] = max(0, int(entry.get("count", 0)) - dose)

    mode = "due"
    active = tube
    due_at = time.ticks_ms()
    reminders = 0
    push_lcd(force=True)

    # Publish BEFORE the chime. play_wav() blocks for the length of the file
    # (jingle5.wav is five seconds), and during that the loop sends nothing -
    # so without this the website would keep showing "dispensing" until the
    # tune finished.
    emit(state_frame())

    hardware.chime("dose")

    add_event("dispensed", tube, {"dose": dose, "why": reason,
                                  "servo": moved,
                                  "left": entry["count"]})
    if entry["count"] <= CONFIG["low_at"]:
        add_event("low", tube, {"left": entry["count"]})
    touch()
    return True


def mark_taken(tube=None, why="button"):
    global mode, active, taken_until
    tube = active if tube is None else tube
    if tube is None:
        return False
    add_event("taken", tube, {"why": why})
    mode = "taken"
    taken_until = time.ticks_ms() + 4000
    push_lcd(force=True)
    return True


def fire_due():
    """Fire any dose whose time has arrived and which has not fired yet.

    The catch_up window is a safety rule, not an optimisation. If the box
    was off at 08:00 and comes back at 14:00, dispensing the morning dose
    six hours late is worse than skipping it - so anything older than the
    window is recorded as missed and the pills stay in the tube.
    """
    global _fired_day

    if not clock_set() or mode in ("dispensing", "due"):
        return
    now = secs_of_day()
    day = today()

    # Housekeeping at midnight. The keys carry their own date so a stale one
    # can never match, but on a box that runs for weeks the dict would grow
    # without this.
    if _fired_day != day:
        fired.clear()
        _fired_day = day

    for i, tube in enumerate(tubes):
        for hhmm in tube.get("times", []):
            target = parse_hhmm(hhmm)
            if target is None:
                continue
            key = "%s %s" % (day, hhmm)
            key = "%d|%s" % (i, key)
            if key in fired:
                continue
            if now < target:
                continue
            if now - target > CONFIG["catch_up"]:
                fired[key] = True
                add_event("skipped", i, {"time": hhmm, "why": "outside catch-up"})
                continue
            fired[key] = True
            do_dispense(i, "scheduled %s" % hhmm)
            return          # one dose at a time, so the chimes never overlap


def tick_due():
    """Chase an outstanding dose: repeat the chime, then give up on it."""
    global mode, active, reminders

    if mode == "taken" and time.ticks_diff(time.ticks_ms(), taken_until) > 0:
        mode, active = "idle", None
        return

    # The EMPTY screen stays up for a minute - long enough for whoever is
    # standing there to read it - then the display goes back to normal.
    if mode == "empty":
        if time.ticks_diff(time.ticks_ms(), due_at) > 60000:
            mode, active = "idle", None
        return

    if mode != "due" or active is None:
        return

    waited = time.ticks_diff(time.ticks_ms(), due_at) // 1000

    if waited >= CONFIG["missed_after"]:
        add_event("missed", active, {"waited": waited})
        mode, active = "idle", None
        return

    if reminders < CONFIG["remind_limit"] and \
            waited >= CONFIG["remind_every"] * (reminders + 1):
        reminders += 1
        hardware.chime("alert")
        add_event("reminded", active, {"n": reminders})


# --- presence ----------------------------------------------------------

def sample_presence():
    """Read the ultrasonic sensor and notice somebody arriving.

    This is presence, not motion: the sensor reports distance, and a person
    standing at the box is simply "something close". That is enough for the
    one question worth answering - did they come to the box after it
    chimed? - and it is honest about what the hardware can actually tell us.
    """
    global near, distance_cm

    cm = hardware.read_distance()
    was = near
    distance_cm = cm
    near = cm is not None and cm <= hardware.SONAR_NEAR_CM

    # Only the arrival is worth recording, and only when the box is waiting
    # for somebody. Logging every approach all day would bury the events
    # that matter in noise.
    if near and not was and mode == "due":
        add_event("approached", active, {"cm": int(cm)})


# --- screens -----------------------------------------------------------

def lcd_view():
    """The facts the patient's screen needs - and only those.

    low_tube deliberately does not appear here. Running low is the carer's
    problem and shows up on the carer site; the box does not nag the person
    taking the medicine about restocking it.
    """
    tube, hhmm, gap = next_dose()
    shown = active if active is not None else tube
    entry = tubes[shown] if shown is not None and shown < len(tubes) else {}
    return {
        "clock_set": clock_set(),
        "mode": mode,
        "tube": shown,
        "label": entry.get("label", "Medicine"),
        "dose": entry.get("dose", 1),
        "next_hhmm": hhmm,
        "next_in": gap,
    }


_lcd_last = (None, None)


def push_lcd(force=False):
    global _lcd_last
    line0, line1 = lcdview.render(lcd_view())
    if force or (line0, line1) != _lcd_last:
        _lcd_last = (line0, line1)
        hardware.lcd_write(line0, line1)


def state_frame():
    tube, hhmm, gap = next_dose()
    return {
        "e": "state",
        "fw": FW,
        "now": stamp() if clock_set() else None,
        "clock_set": clock_set(),
        "mode": mode,
        "active": active,
        "waited": (time.ticks_diff(time.ticks_ms(), due_at) // 1000)
                  if mode == "due" else 0,
        "next": {"tube": tube, "at": hhmm, "in": gap},
        "low": low_tube(),
        "sonar": {"cm": round(distance_cm, 1) if distance_cm is not None else None,
                  "near": near, "present": hardware.present.get("sonar", False)},
        "tubes": [{"label": t.get("label", ""), "dose": t.get("dose", 1),
                   "times": t.get("times", []), "count": t.get("count", 0)}
                  for t in tubes],
        "hw": {"present": hardware.present, "detail": hardware.detail,
               "servos": list(hardware.servos),
               "pins": list(hardware.SERVO_PINS),
               "closed": list(hardware.SERVO_CLOSED),
               "open": list(hardware.SERVO_OPEN)},
        "lcd": list(_lcd_last),
        "cfg": CONFIG,
        "chimes": hardware.chime_names(),
        "events": events[-20:],
    }


# --- commands ----------------------------------------------------------

def handle(cmd):
    global mode, active, due_at

    name = cmd.get("c")
    ok = True
    extra = {}

    if name == "hello":
        emit({"e": "hello", "fw": FW, "tubes": TUBE_COUNT,
              "hw": hardware.present, "detail": hardware.detail,
              "clock_set": clock_set()})
        return

    elif name == "time":
        t = cmd.get("t") or []
        if len(t) >= 6:
            try:
                from machine import RTC
                was_unset = not clock_set()
                # MicroPython RTC wants (year, month, day, weekday, h, m, s, us).
                # The weekday field is ignored on the ESP32 port, so a zero
                # here is harmless and saves the bridge computing it.
                RTC().datetime((t[0], t[1], t[2], 0, t[3], t[4], t[5], 0))
                # Only wipe the fired record on the FIRST sync, when the clock
                # was still at 2000-01-01 and every key in there is nonsense.
                # The bridge re-syncs periodically to stop the RTC drifting,
                # and clearing on those would let a dose that already fired
                # inside the catch-up window fire a second time.
                if was_unset:
                    fired.clear()
                push_lcd(force=True)
            except Exception as e:
                ok, extra = False, {"err": repr(e)}
        else:
            ok = False

    elif name == "sched":
        i = int(cmd.get("i", -1))
        if 0 <= i < len(tubes):
            if "times" in cmd:
                clean = []
                for value in cmd["times"]:
                    if parse_hhmm(value) is not None and value not in clean:
                        clean.append(value)
                clean.sort()
                tubes[i]["times"] = clean
            if "label" in cmd:
                tubes[i]["label"] = str(cmd["label"])[:24]
            if "dose" in cmd:
                tubes[i]["dose"] = max(1, min(9, int(cmd["dose"])))
            # Note what is NOT here: the fired record is left alone. Clearing
            # it would re-arm times that have already gone off today, so a
            # carer fixing a typo in a label at 08:30 would trigger a second
            # 08:00 dose. Newly added times have no key yet, so they arm on
            # their own.
            touch()
            push_lcd(force=True)
        else:
            ok = False

    elif name == "pills":
        i = int(cmd.get("i", -1))
        if 0 <= i < len(tubes):
            tubes[i]["count"] = max(0, min(999, int(cmd.get("n", 0))))
            add_event("refill", i, {"to": tubes[i]["count"]})
            touch()
            push_lcd(force=True)
        else:
            ok = False

    elif name == "dispense":
        ok = do_dispense(int(cmd.get("i", 0)), "manual")

    elif name == "force":
        tube, hhmm, _gap = next_dose()
        ok = do_dispense(tube if tube is not None else 0, "forced %s" % hhmm)

    elif name == "taken":
        ok = mark_taken(cmd.get("i"), cmd.get("why", "button"))

    elif name == "snooze":
        minutes = max(1, min(60, int(cmd.get("m", 10))))
        if mode == "due":
            # Rewinding the clock on the reminder is the whole snooze: the
            # missed timer and the repeat chimes both measure from due_at.
            due_at = time.ticks_add(time.ticks_ms(), minutes * 60000)
            add_event("snoozed", active, {"mins": minutes})
        else:
            ok = False

    elif name == "help":
        # The patient pressed "I need help" on the kiosk. There is no phone
        # network here, so this does the honest thing: records it and lets
        # the carer console raise it. Nothing is claimed that is not true.
        add_event("help", None, {"from": cmd.get("from", "kiosk")})

    elif name == "chime":
        # `name` previews one sound without changing the saved choice, which
        # is what the Preview button on the carer site uses.
        ok = hardware.chime(cmd.get("kind", "dose"), cmd.get("name"))

    elif name == "servo":
        ok = hardware.servo_to(cmd.get("i", 0), cmd.get("a", 90))

    elif name == "sweep":
        # One full open-and-close of a tube's gate. Deliberately does NOT
        # touch the pill count or log a dose - it is for checking that the
        # mechanism works, not for giving somebody medicine.
        i = int(cmd.get("i", 0))
        ok = hardware.sweep(i)
        if ok:
            add_event("tested", i, {"what": "gate sweep"})

    elif name == "cfg":
        for key in ("patient", "low_at", "remind_every", "remind_limit",
                    "missed_after", "catch_up"):
            if key in cmd:
                CONFIG[key] = cmd[key]
        if "volume" in cmd:
            CONFIG["volume"] = max(0.0, min(1.0, float(cmd["volume"])))
        if "chime" in cmd:
            CONFIG["chime"] = str(cmd["chime"])
        apply_sound()
        if "closed" in cmd or "open" in cmd:
            hardware.set_angles(cmd.get("closed"), cmd.get("open"))
        touch()
        push_lcd(force=True)

    elif name == "probe":
        hardware.probe()

    elif name == "state":
        pass

    else:
        ok, extra = False, {"err": "unknown command"}

    ack = {"e": "ack", "c": name, "ok": ok}
    ack.update(extra)
    emit(ack)
    emit(state_frame())


# --- serial plumbing ---------------------------------------------------

_poll = select.poll()
_poll.register(sys.stdin, select.POLLIN)
_inbuf = ""


def pump():
    """Drain whatever the laptop has sent without ever blocking.

    sys.stdin.readline() would block the scheduler until a newline arrived,
    so this reads one character at a time and only while poll() says there
    is something waiting.
    """
    global _inbuf
    lines = []
    while _poll.poll(0):
        try:
            ch = sys.stdin.read(1)
        except Exception:
            break
        if not ch:
            break
        if ch == "\n":
            line = _inbuf.strip()
            _inbuf = ""
            if line:
                lines.append(line)
        elif ch != "\r":
            _inbuf += ch
            if len(_inbuf) > 900:      # runaway noise, not a command
                _inbuf = ""
    return lines


# --- main loop ---------------------------------------------------------

def run():
    global _blink, _last_state_ms, _last_sonar_ms

    load()
    hardware.probe()
    apply_sound()
    push_lcd(force=True)
    emit({"e": "hello", "fw": FW, "tubes": TUBE_COUNT,
          "hw": hardware.present, "detail": hardware.detail,
          "clock_set": clock_set()})

    while True:
        for line in pump():
            try:
                handle(json.loads(line))
            except Exception as e:
                emit({"e": "ack", "ok": False, "err": repr(e),
                      "line": line[:80]})

        fire_due()
        tick_due()

        now = time.ticks_ms()

        # Twice a second is plenty for "has somebody walked up to the box",
        # and keeps the reads well clear of the 50ms command loop - a sonar
        # ping at an empty room blocks for up to ~24ms waiting for an echo
        # that never comes.
        if time.ticks_diff(now, _last_sonar_ms) >= 500:
            _last_sonar_ms = now
            sample_presence()

        if time.ticks_diff(now, _last_state_ms) >= 1000:
            _last_state_ms = now
            _blink += 1
            push_lcd()
            emit(state_frame())

        if _dirty and time.ticks_diff(now, _dirty_at) > 2000:
            save_now()

        # 50ms keeps a button press or a website command feeling instant
        # while leaving the CPU almost entirely idle.
        time.sleep_ms(50)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        # Deliberate: Ctrl-C is how you get a REPL to work on the board.
        hardware.shutdown()
        print("kairo: stopped, REPL is yours (Ctrl-D to restart)")
    except Exception as e:
        hardware.shutdown()
        if _dirty:
            save_now()
        print("kairo: crashed %r" % e)
        raise
