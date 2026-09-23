"""The real agent, run on the PC against a fake board. No hardware needed.

    python dispenser/selfcheck.py

main.py, hardware.py and servo.py run unchanged. `machine` is faked, and the
fake PWM copies the ESP32 behaviour behind the "every gate moves" bug:
MicroPython hands a freed LEDC channel to the next PWM on ANY pin, and
deinit() leaves the old pin wired to that channel. So this checks both the
dose flow and that one dose drives one servo.
"""

import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "servo")]

# --- a fake board ---------------------------------------------------------
ledc = {"owner": {}, "route": {}, "duty": {}}   # channel->pin, pin->channel, channel->ns
echo_us = [-1]                                  # what the HC-SR04 answers; -1 = nothing


class PWM:
    def __init__(self, pin, freq=50, duty_ns=None):
        mine = [c for c, p in ledc["owner"].items() if p == pin.n]
        self.ch = mine[0] if mine else min(set(range(8)) - set(ledc["owner"]))
        ledc["owner"][self.ch], ledc["route"][pin.n] = pin.n, self.ch
        if duty_ns is not None:
            ledc["duty"][self.ch] = duty_ns

    def duty_ns(self, ns=None):
        if ns is None:
            return ledc["duty"].get(self.ch, 0)
        ledc["duty"][self.ch] = ns

    def deinit(self):                    # the pin stays routed, as on the chip
        ledc["owner"].pop(self.ch, None)
        ledc["duty"][self.ch] = 0


class Pin:
    OUT, IN = 1, 0

    def __init__(self, n, mode=None, value=None):
        self.n = n
        if mode is not None:             # gpio_set_direction resets the route
            ledc["route"].pop(n, None)

    def value(self, v=None):
        return 0


def pulsing():
    """Which servo pins are seeing pulses right now."""
    return {p for p, c in ledc["route"].items() if ledc["duty"].get(c, 0) > 0}


sys.modules["machine"] = types.SimpleNamespace(
    Pin=Pin, PWM=PWM, RTC=lambda: types.SimpleNamespace(datetime=lambda t: None),
    I2C=lambda **k: types.SimpleNamespace(scan=lambda: []),
    time_pulse_us=lambda pin, level, timeout: echo_us[0])
sys.modules["select"] = types.SimpleNamespace(
    POLLIN=1, poll=lambda: types.SimpleNamespace(register=lambda *a: None, poll=lambda t: []))

# MicroPython's time API on a clock this script moves by hand.
clock = {"s": time.time(), "ms": 0}
seen = set()                            # pins that pulsed during a movement
_localtime = time.localtime
time.time = lambda: int(clock["s"])
time.localtime = lambda s=None: _localtime(clock["s"] if s is None else s)
time.ticks_ms = lambda: clock["ms"]
time.ticks_diff = lambda a, b: a - b
time.ticks_add = lambda a, b: a + b
time.sleep_ms = lambda ms: seen.update(pulsing())
time.sleep_us = lambda us: None


def wait(seconds):
    clock["s"] += seconds
    clock["ms"] += int(seconds * 1000)


import main  # noqa: E402

main.print = lambda *a, **k: None       # emit() prints JSON for the bridge
main.STATE_FILE = os.path.join(tempfile.mkdtemp(), "kairo.json")


def sample(cm):
    echo_us[0] = -1 if cm is None else int(cm * 58)
    main.sample_presence()


def last(kind):
    return [e for e in main.events if e["k"] == kind][-1]


# --- an old save migrates: new names and intervals, the counts kept -------
with open(main.STATE_FILE, "w") as f:
    f.write('{"tubes":[{"label":"Metformin","times":["08:00"],"count":33}],'
            '"config":{"patient":"Mum","remind_every":180}}')
main.load()
main.hardware.probe()
assert [t["label"] for t in main.tubes] == ["Aspirin", "Vitamin C", "Iron"]
assert main.tubes[0]["every"] == 60 and main.tubes[0]["count"] == 33
assert "times" not in main.tubes[0]
assert main.CONFIG["patient"] == "Mum" and main.CONFIG["remind_every"] == 30
assert pulsing() == set(), "claiming the servos must not move them"

# --- the flow: chime, wait, drop only once somebody is there --------------
for i in range(3):
    main.schedule(i)
wait(300)                               # Iron is every 5 minutes
main.fire_due()
assert main.mode == "due" and main.active == 2
assert main.lcdview.render(main.lcd_view())[0].strip() == "COME TO THE BOX"
sample(None)
sample(250)                             # somebody across the room
assert main.mode == "due", "nobody at the box yet, so nothing drops"
sample(40)
assert main.mode == "due", "one reading is not enough"
seen.clear()
sample(38)
assert main.mode == "taken" and main.tubes[2]["count"] == 11
assert last("taken")["why"] == "at the box, 38 cm"
assert seen == {39}, "tube 3's dose drove %s" % sorted(seen)
assert pulsing() == set(), "the servo is left slack"
wait(7)
main.tick_due()
assert main.mode == "idle"

# --- nobody comes: missed, and the pill stays in the tube ------------------
sample(None)
wait(300)
main.fire_due()
assert main.mode == "due"
wait(main.CONFIG["missed_after"])
main.tick_due()
assert main.mode == "idle" and last("missed")["i"] == 2
assert main.tubes[2]["count"] == 11

# --- the camera backs up the sonar: it can drop a dose the sonar missed ----
sample(None)
main.handle({"c": "force"})
assert main.mode == "due" and main.active == 2
seen.clear()
main.handle({"c": "present", "by": "camera"})
assert main.mode == "taken" and seen == {39}
assert last("taken")["why"] == "at the box, seen by the camera"

# --- every tube drives only its own gate -----------------------------------
for tube, pin in enumerate(main.hardware.SERVO_PINS):
    seen.clear()
    main.hardware.sweep(tube)
    assert seen == {pin}, "tube %d moved %s" % (tube + 1, sorted(seen))

print("selfcheck: dose flow and servo isolation OK")
