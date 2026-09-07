# Full-range back-and-forth sweep - the SG90/SC90's complete 0-180 travel.
#
# STEP_MS is the dial that matters. The servo moves one degree per step, so
# smaller values mean a faster sweep and a bigger current draw. On USB power
# there is a threshold above which the motor pulls the 5V rail down far enough
# to reset the board mid-sweep; if that happens, raise STEP_MS.
#
#   STEP_MS = 15   fast, like the kit's original sketch (needs external power)
#   STEP_MS = 40   moderate
#   STEP_MS = 60   gentle, usually survives on USB alone
#
# Run with:  python -m mpremote connect COM3 run servo_sweep.py
# Stop with: Ctrl+C

from servo import Servo
import time

SERVO_PIN = 21
STEP_MS = 60
CYCLES = 2          # set to 0 to sweep forever

# Two dials control sweep speed, and they multiply:
#
#   STEP_MS   pause after each step - lower is faster
#   STEP_DEG  degrees moved per step - higher is faster
#
# Time for one full sweep is roughly (170 / STEP_DEG) * STEP_MS.
#   STEP_DEG=1, STEP_MS=60  -> ~10s   very smooth, gentlest on power
#   STEP_DEG=3, STEP_MS=20  -> ~1.1s  brisk, still visibly stepped
#   STEP_DEG=5, STEP_MS=10  -> ~0.3s  about as fast as an SG90 can travel
#
# Raising STEP_DEG makes each move a bigger jump, so the motor accelerates
# harder and draws more current - the same brownout tradeoff as lowering
# STEP_MS. An SG90 needs roughly 0.1s per 60 degrees, so below about 0.3s
# for a full sweep the servo simply cannot keep up and you gain nothing but
# current draw.
STEP_DEG = 1

# Trimming the ends helps on USB power: a servo commanded hard to 0 or 180
# can push against its internal stop and stall, drawing peak current for no
# extra movement. Set these to 0 and 180 once you have external power.
MIN_ANGLE = 5
MAX_ANGLE = 175

# Where the horn is assumed to be sitting when this starts. The very first
# move is the worst current spike of the run, because a servo commanded to a
# distant angle goes there at full speed. So we creep in from HOME instead of
# jumping, using an extra-slow step just for that leg.
HOME = 90
HOME_STEP_MS = 120

s = Servo(SERVO_PIN)


def sweep(start, end, step_ms, step_deg=None):
    step_deg = step_deg or STEP_DEG
    step = step_deg if end > start else -step_deg
    for angle in range(start, end, step):
        s.angle(angle)
        time.sleep_ms(step_ms)
    # range() can overshoot or stop short when step_deg doesn't divide the
    # span evenly, so land the exact endpoint explicitly.
    s.angle(end)
    time.sleep_ms(step_ms)


print("full sweep 0-180, %dms per degree" % STEP_MS)

# Creep from HOME rather than commanding the start angle outright.
#
# IMPORTANT: if a previous run crashed, the horn is left wherever it stopped,
# which may not be HOME. The first command then becomes a long fast jump -
# the exact spike that causes a brownout. Run servo_min.py first to park it
# near 90 before sweeping.
print("easing from %d to %d ..." % (HOME, MIN_ANGLE))
sweep(HOME, MIN_ANGLE, HOME_STEP_MS, 1)   # always creep in one degree at a time
time.sleep_ms(60)

try:
    cycle = 0
    while CYCLES == 0 or cycle < CYCLES:
        cycle += 1
        print("cycle %d: %d -> %d" % (cycle, MIN_ANGLE, MAX_ANGLE))
        sweep(MIN_ANGLE, MAX_ANGLE, STEP_MS)
        time.sleep_ms(30)
        print("cycle %d: %d -> %d" % (cycle, MAX_ANGLE, MIN_ANGLE))
        sweep(MAX_ANGLE, MIN_ANGLE, STEP_MS)
        time.sleep_ms(30)
    print("SUCCESS - %d full sweeps at %dms/deg" % (CYCLES, STEP_MS))
except KeyboardInterrupt:
    print("stopped")
finally:
    s.release()
    print("pin released, servo slack")
