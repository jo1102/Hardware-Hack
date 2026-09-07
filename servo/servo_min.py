# Absolute minimum-current servo test, for running on USB power alone.
#
# Three things keep the current low here:
#   - a narrow 20-degree range, so the motor never travels far
#   - 150ms between single-degree steps, so it creeps
#   - the PWM is released between moves, so the servo isn't burning
#     holding torque while nothing is happening
#
# If this survives, the servo and wiring are proven and the only issue is
# power headroom for larger/faster movements.

from servo import Servo
import time

SERVO_PIN = 21
STEP_MS = 150

s = Servo(SERVO_PIN)

print("centring slowly at 90 deg")
s.angle(90)
time.sleep_ms(1200)

try:
    for target in (100, 80, 90):
        print("  creeping to %d ..." % target)
        # Work out which way to go from the last commanded angle.
        current = 90 if target == 100 else (100 if target == 80 else 80)
        step = 1 if target > current else -1
        for a in range(current, target + step, step):
            s.angle(a)
            time.sleep_ms(STEP_MS)
        time.sleep_ms(500)
    print("SUCCESS - completed without dropping the connection")
finally:
    s.release()
    print("pin released, servo now slack")
