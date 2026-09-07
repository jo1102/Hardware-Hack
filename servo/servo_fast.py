# Uniformly fast full-range sweep - no gentle homing ramp, no pauses between
# legs. Everything runs at the same speed, so it looks like one continuous
# motion rather than speeding up after a slow start.
#
# Speed dials (they multiply):
#   STEP_MS   pause per step, lower is faster
#   STEP_DEG  degrees per step, higher is faster
#
# One sweep takes roughly (MAX-MIN)/STEP_DEG * STEP_MS.
#   STEP_DEG=3, STEP_MS=20 -> ~1.1s   verified working
#   STEP_DEG=5, STEP_MS=10 -> ~0.35s  near the SG90's mechanical limit
#
# An SG90 travels about 60 degrees per 0.1s, so a full 180-degree sweep can
# not physically go below roughly 0.3s. Asking for less just means the horn
# lags behind the commands while drawing peak current.
#
# Run:  python -m mpremote connect COM3 run servo_fast.py
# Stop: python -m mpremote connect COM3 soft-reset
#       (Ctrl+C only kills mpremote on the laptop, not the board)

from servo import Servo
import time

SERVO_PIN = 21
STEP_MS = 10
STEP_DEG = 5
MIN_ANGLE = 0
MAX_ANGLE = 180
CYCLES = 3          # 0 = sweep forever

s = Servo(SERVO_PIN)


def sweep(start, end):
    step = STEP_DEG if end > start else -STEP_DEG
    for angle in range(start, end, step):
        s.angle(angle)
        time.sleep_ms(STEP_MS)
    # range() can stop short when STEP_DEG doesn't divide the span evenly,
    # so land the exact endpoint.
    s.angle(end)
    time.sleep_ms(STEP_MS)


span = MAX_ANGLE - MIN_ANGLE
print("fast sweep %d-%d, %d deg per %dms (~%.1fs per sweep)"
      % (MIN_ANGLE, MAX_ANGLE, STEP_DEG, STEP_MS, span / STEP_DEG * STEP_MS / 1000))

try:
    # Go straight to the start at full speed - no slow ramp.
    sweep(MAX_ANGLE, MIN_ANGLE)
    cycle = 0
    while CYCLES == 0 or cycle < CYCLES:
        cycle += 1
        sweep(MIN_ANGLE, MAX_ANGLE)
        sweep(MAX_ANGLE, MIN_ANGLE)
        print("cycle %d done" % cycle)
    print("SUCCESS - %d sweeps" % CYCLES)
except KeyboardInterrupt:
    print("stopped")
finally:
    s.release()
    print("pin released, servo slack")
