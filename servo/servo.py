# A servo driver that works on MicroPython v1.29.
#
# The Freenove kit's myservo.py drives the pin with PWM.duty(), a deprecated
# 10-bit API that no longer honours writes on this firmware - asking for
# duty(77) reads back as 26, so every angle collapsed to roughly 0 degrees.
# This version uses duty_ns(), which sets the pulse width directly in
# nanoseconds and was verified accurate to within 0.06%.
#
# Servo timing: 50Hz (20ms period), with the high pulse setting the angle.
#   0.5ms -> 0 deg      1.5ms -> 90 deg      2.5ms -> 180 deg

from machine import Pin, PWM

MIN_NS = 500_000    # 0.5ms -> 0 degrees
MAX_NS = 2_500_000  # 2.5ms -> 180 degrees


class Servo:
    def __init__(self, pin, freq=50):
        # Silent from the start: no pulses until angle() says where to go,
        # so claiming the pin never twitches the arm.
        self._pwm = PWM(Pin(pin), freq=freq)
        self._pwm.duty_ns(0)

    def angle(self, degrees):
        """Move to an angle between 0 and 180."""
        if degrees < 0:
            degrees = 0
        elif degrees > 180:
            degrees = 180
        ns = MIN_NS + (MAX_NS - MIN_NS) * degrees // 180
        self._pwm.duty_ns(int(ns))
        return degrees

    def pulse_us(self, microseconds):
        """Set the pulse width directly, for calibrating an unusual servo."""
        ns = max(MIN_NS, min(MAX_NS, int(microseconds) * 1000))
        self._pwm.duty_ns(ns)

    def read_pulse_ns(self):
        """What the hardware is actually outputting - useful for debugging."""
        return self._pwm.duty_ns()

    def release(self):
        """Stop the pulses so the servo goes slack. Can move again any time.

        Deliberately not PWM.deinit(). That frees the LEDC channel but leaves
        the pin wired to it, the next servo - on ANY pin - is handed the same
        channel, and every "released" pin then copies its pulses: one
        dispense swung all three gates. A zero duty is just as slack (no
        pulses, no holding current) and this pin keeps its own channel.
        """
        self._pwm.duty_ns(0)
