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
#        {"c":"sched","i":0,"every":60,"label":"...","dose":1}   every N minutes
#        {"c":"pills","i":0,"n":42}        carer has refilled or recounted a tube
#        {"c":"dispense","i":0}            drop from that tube now, no waiting
#        {"c":"force"}                     start the next dose now (demo)
#        {"c":"present","by":"camera"}     somebody is at the box (the bridge's
#                                          camera); drops a waiting dose
#        {"c":"help"}                      patient pressed "I need help"
#        {"c":"snooze","m":10}             push the current reminder back
#        {"c":"chime","name":"bells"}       play a sound now (preview)
#        {"c":"servo","i":0,"a":90}         hold one servo at an angle
#        {"c":"sweep","i":0}               one gate cycle, no dose logged
#        {"c":"cfg", ...}                  volume, low_at, patient, near_cm, angles
#        {"c":"state"}                     send a state frame immediately
#
#   out  {"e":"state", ...}                the whole picture, about once a second
#          includes "sonar":{"cm":42.0,"near":true} when the HC-SR04 answers
#        {"e":"ack","c":"dispense","ok":true}
#        {"e":"hello", ...}
#
# A DOSE
# When a tube's interval comes round the box plays the chime and WAITS. The
# servo only moves once somebody is at the box - the ultrasonic sensor, or
# the camera via the bridge as a backup, whichever sees them first - so a
# pill never sits in the tray of an empty room. Nobody within missed_after
# and the dose is logged missed with the pill still in the tube.
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

FW = "kairo-1.2"
STATE_FILE = "/kairo.json"
SCHEMA = 2           # 2: tubes run on an interval ("every"), not clock times
TUBE_COUNT = 3

# How the box behaves. All overridable from the website with {"c":"cfg"}.
CONFIG = {
    "patient": "Patient",
    "low_at": 10,          # pills below this and the carer gets warned
    "remind_every": 30,    # seconds between repeat chimes while a dose waits
    "remind_limit": 3,     # how many repeat chimes before it stops chiming
    "missed_after": 120,   # seconds with nobody at the box: the dose is missed
    "volume": 0.35,
    "chime": "jingle",     # which sound plays for a due dose
    "near_cm": 80,         # ultrasonic: closer than this is somebody
}

# Short names and short intervals, so a demo sees doses come round in minutes.
DEFAULT_TUBES = [
    {"label": "Aspirin", "dose": 1, "every": 60, "count": 42},
    {"label": "Vitamin C", "dose": 1, "every": 30, "count": 30},
    {"label": "Iron", "dose": 1, "every": 5, "count": 12},
]

tubes = []
events = []          # newest last, capped at EVENT_CAP
EVENT_CAP = 40

mode = "idle"        # idle | due | dispensing | taken | empty
active = None        # which tube the current dose came from
due_at = 0           # ticks_ms when the dose came due
reminders = 0
taken_until = 0      # ticks_ms to hold the TAKE YOUR PILL screen until
next_due = [0] * TUBE_COUNT  # time.time() of each tube's next dose; 0 = none
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
    except Exception:
        saved = {}
    tubes = saved.get("tubes") or []
    events = saved.get("events") or []
    # A save from before intervals has clock times and quarter-hour timings,
    # neither of which means anything now. Those take the new defaults; what
    # still means the same (name, sound, stock, thresholds) carries over.
    old = saved.get("v") != SCHEMA
    for key, value in (saved.get("config") or {}).items():
        if key in CONFIG and not (old and key in ("remind_every", "missed_after")):
            CONFIG[key] = value
    # Normalise, so a hand-edited or truncated file cannot crash the loop.
    while len(tubes) < TUBE_COUNT:
        tubes.append(dict(DEFAULT_TUBES[len(tubes)]))
    tubes = tubes[:TUBE_COUNT]
    for i, t in enumerate(tubes):
        if old:
            t.pop("times", None)
            t.update({k: v for k, v in DEFAULT_TUBES[i].items() if k != "count"})
        t.setdefault("label", "Medicine")
        t.setdefault("dose", 1)
        t.setdefault("every", 0)
        t.setdefault("count", 0)


def apply_sound():
    """Push the saved volume and chime choice into the hardware layer."""
    hardware.VOLUME = float(CONFIG.get("volume", 0.35))
    CONFIG["chime"] = hardware.set_chime(CONFIG.get("chime", "jingle"))


def save_now():
    global _dirty
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"v": SCHEMA, "tubes": tubes, "events": events[-EVENT_CAP:],
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


def schedule(i, start=None):
    """Put tube i's next dose one interval after `start` (default: now)."""
    every = int(tubes[i].get("every", 0) or 0)
    next_due[i] = ((time.time() if start is None else start) + every * 60) if every > 0 else 0


def next_dose():
    """The soonest upcoming dose: (tube_index, 'HH:MM', seconds_until).

    Returns (None, None, None) if no tube is scheduled.
    """
    best = None
    for i, at in enumerate(next_due):
        if at and (best is None or at < next_due[best]):
            best = i
    if best is None:
        return None, None, None
    t = time.localtime(next_due[best])
    return best, "%02d:%02d" % (t[3], t[4]), max(0, next_due[best] - time.time())


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

def drop(tube, kind, why):
    """Swing the gate once per pill, log it, and show TAKE YOUR PILL.

    kind is "taken" for a dose somebody came to the box for, "dispensed"
    for the carer's button. The count comes down whether or not the servo
    answered: if the motor is unplugged during a build the count still has
    to stay honest about what the carer loaded, and an over-count is the
    more dangerous error of the two.
    """
    global mode, active, taken_until
    entry = tubes[tube]
    dose = max(1, int(entry.get("dose", 1)))
    mode, active = "dispensing", tube
    push_lcd(force=True)
    emit(state_frame())
    moved = hardware.dispense(tube, dose)
    entry["count"] = max(0, int(entry.get("count", 0)) - dose)
    mode = "taken"
    taken_until = time.ticks_add(time.ticks_ms(), 6000)
    push_lcd(force=True)
    add_event(kind, tube, {"why": why, "dose": dose, "servo": moved,
                           "left": entry["count"]})
    if entry["count"] <= CONFIG["low_at"]:
        add_event("low", tube, {"left": entry["count"]})
    return True


def start_due(tube, why):
    """A tube's interval has come round: chime, then wait for somebody."""
    global mode, active, due_at, reminders
    active, due_at, reminders = tube, time.ticks_ms(), 0

    # Nothing to give. Turning the servo would look like a dose was
    # delivered, log one, and leave the patient waiting for a pill that is
    # not coming - so the machine says so instead and raises it with the
    # carer. The count is not touched.
    if int(tubes[tube].get("count", 0)) <= 0:
        mode = "empty"
        push_lcd(force=True)
        add_event("empty", tube, {"why": why})
        emit(state_frame())
        hardware.chime("alert")
        return False

    mode = "due"
    push_lcd(force=True)
    # Publish BEFORE the chime. play_wav() blocks for the length of the file
    # (jingle5.wav is five seconds), and during that the loop sends nothing.
    emit(state_frame())
    hardware.chime("dose")
    return True


def fire_due():
    """Start the first tube whose interval has come round.

    One at a time, so two chimes never overlap and each pill waits for its
    own person. A tube late by a whole interval or more (the box was busy,
    or off) gets one dose now and carries on from now - never a burst of
    the doses it missed.
    """
    if not clock_set() or mode != "idle":
        return
    now = time.time()
    for i in range(len(tubes)):
        if next_due[i] and now >= next_due[i]:
            schedule(i, next_due[i])
            if next_due[i] <= now:
                schedule(i)
            start_due(i, "every %d min" % int(tubes[i].get("every", 0)))
            return


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

    # Nobody came. Nothing was dropped, so the pill is still in the tube.
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
    """Read the ultrasonic sensor, and drop a waiting dose for whoever came.

    This is presence, not motion: the sensor reports distance, and a person
    standing at the box is simply "something close". It takes two readings
    in a row, half a second apart, before a pill drops - one stray echo off
    a wall must not empty a tube into the tray of an empty room.
    """
    global near, distance_cm

    cm = hardware.read_distance()
    was = near
    distance_cm = cm
    near = cm is not None and cm <= CONFIG["near_cm"]
    if near and was and mode == "due":
        drop(active, "taken", "at the box, %d cm" % cm)


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
    now = time.time()
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
                   "every": t.get("every", 0), "count": t.get("count", 0),
                   "in": max(0, next_due[i] - now) if next_due[i] else None}
                  for i, t in enumerate(tubes)],
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
                # Start the intervals on the FIRST sync only, when the clock
                # jumps from 2000-01-01 to now. The bridge re-syncs every 15
                # minutes to stop drift, and restarting on those would keep
                # pushing a 60-minute tube's dose back forever.
                if was_unset:
                    for i in range(len(tubes)):
                        schedule(i)
                push_lcd(force=True)
            except Exception as e:
                ok, extra = False, {"err": repr(e)}
        else:
            ok = False

    elif name == "sched":
        i = int(cmd.get("i", -1))
        if 0 <= i < len(tubes):
            if "label" in cmd:
                tubes[i]["label"] = str(cmd["label"])[:24]
            if "dose" in cmd:
                tubes[i]["dose"] = max(1, min(9, int(cmd["dose"])))
            # A new interval counts from now. Only a changed one, though: a
            # carer fixing a typo in the name must not move the next dose.
            if "every" in cmd:
                every = max(0, min(1440, int(cmd["every"])))
                if every != tubes[i].get("every"):
                    tubes[i]["every"] = every
                    schedule(i)
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
        # The carer's button: drop now, no waiting. On the tube that is
        # waiting it releases that dose; otherwise only while idle, so it can
        # never cut across somebody else's dose.
        i = int(cmd.get("i", 0))
        if not (0 <= i < len(tubes)) or int(tubes[i].get("count", 0)) <= 0:
            ok = False
        elif mode == "due" and i == active:
            drop(i, "taken", "released by the carer")
        elif mode == "idle":
            drop(i, "dispensed", "by the carer")
        else:
            ok, extra = False, {"err": "busy"}

    elif name == "force":
        tube, _hhmm, _gap = next_dose()
        if mode != "idle" or tube is None:
            ok = False
        else:
            schedule(tube)          # the one after counts from this one
            ok = start_due(tube, "started by the carer")

    elif name == "present":
        # The bridge's camera saw somebody - the backup for when the
        # ultrasonic misses them. Only a waiting dose cares.
        if mode == "due":
            drop(active, "taken", "at the box, seen by the %s" % cmd.get("by", "camera"))
        else:
            ok = False

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
                    "missed_after"):
            if key in cmd:
                CONFIG[key] = cmd[key]
        if "near_cm" in cmd:
            CONFIG["near_cm"] = max(10, min(400, int(cmd["near_cm"])))
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
    # A soft reset keeps the RTC, so the clock may already be right; then the
    # intervals start from now. Otherwise they start on the bridge's first
    # time sync - see handle("time").
    if clock_set():
        for i in range(len(tubes)):
            schedule(i)
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
