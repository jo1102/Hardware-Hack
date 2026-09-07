# Tones on an 8 ohm 2W speaker, driven through the kit's NPN transistor.
#
# WHY A TRANSISTOR IS NOT OPTIONAL
# An ESP32-S3 GPIO can source about 40mA. An 8 ohm speaker on 3.3V would draw
# 3.3/8 = 412mA, roughly ten times the pin's rating - it would destroy the
# pin. The GPIO switches the transistor; the transistor switches the speaker.
#
# The ESP32-S3 also has NO DAC (the original ESP32 had one on GPIO25/26,
# Espressif removed it on the S3). So this produces PWM square-wave tones -
# beeps, alarms, simple melodies. Not speech or music. For real audio you
# need an I2S amplifier such as a MAX98357A.
#
# CIRCUIT  (NPN 8050: flat marked face toward you, legs down -> E B C)
#
#   speaker RED (+)   ---------------------------------> 5V
#   speaker BLACK (-) --- Rseries --- C  (right leg)
#                                     B  (middle leg) --- 1k --- GPIO47
#                                     E  (left leg)   ---------> GND
#
# Rseries LIMITS CURRENT AND IS REQUIRED. Without it, 5V across 8 ohms is
# 625mA and 3W - over the speaker's 2W rating and hard on the transistor.
#   220 ohm (kit value)      -> ~22mA, safe, quiet
#   two 220 in parallel(110) -> ~45mA, louder
#   three in parallel (73)   -> ~68mA, louder still
# Do not go below about 50 ohms.
#
# Run:  python -m mpremote connect COM3 run speaker_tone.py
# Stop: python -m mpremote connect COM3 soft-reset

from machine import Pin, PWM
import time

SPEAKER_PIN = 47      # free pin: 21 is the servo, 14/42 were the LCD,
                      # 13 is the camera's pixel clock
DUTY = 32768          # 50% of 65535 - square wave, loudest for a given pitch

# Note frequencies in Hz.
NOTES = {
    "C4": 262, "D4": 294, "E4": 330, "F4": 349, "G4": 392, "A4": 440, "B4": 494,
    "C5": 523, "D5": 587, "E5": 659, "F5": 698, "G5": 784, "A5": 880,
    "REST": 0,
}

pwm = PWM(Pin(SPEAKER_PIN))


def tone(freq, ms):
    """Play one frequency for ms milliseconds."""
    if freq <= 0:
        pwm.duty_u16(0)
    else:
        pwm.freq(freq)
        pwm.duty_u16(DUTY)
    time.sleep_ms(ms)
    pwm.duty_u16(0)     # silence between notes


def play(sequence, gap_ms=40):
    for name, ms in sequence:
        tone(NOTES.get(name, 0), ms)
        time.sleep_ms(gap_ms)


try:
    print("beep test")
    for _ in range(3):
        tone(1000, 150)
        time.sleep_ms(150)

    print("rising scale")
    for name in ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"):
        print("  ", name, NOTES[name], "Hz")
        tone(NOTES[name], 250)
        time.sleep_ms(50)

    print("sweep 200Hz -> 2000Hz")
    for f in range(200, 2000, 25):
        pwm.freq(f)
        pwm.duty_u16(DUTY)
        time.sleep_ms(12)
    pwm.duty_u16(0)

    print("short melody")
    play([("E5", 200), ("E5", 200), ("REST", 120), ("E5", 200),
          ("REST", 120), ("C5", 200), ("E5", 200), ("G5", 400),
          ("REST", 200), ("G4", 400)])

    print("done - if it is too quiet, lower Rseries (parallel more resistors)")
except KeyboardInterrupt:
    print("stopped")
finally:
    # Always release, or the pin keeps driving the transistor and the
    # speaker sits there buzzing.
    pwm.duty_u16(0)
    pwm.deinit()
    print("pin released")
