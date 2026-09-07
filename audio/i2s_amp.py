# Tones through the Freenove ES7148 + PAM8403 I2S amplifier.
#
# CONFIRMED WORKING without an external master clock - the module generates
# its own, so MicroPython's lack of an `mck=` parameter does not matter and
# GPIO1 (the module's SCK pin) can be left unconnected.
#
# WIRING - pins chosen to avoid the camera ribbon, LCD (14/42) and servo (21):
#   VCC -> 5V        BCK -> GPIO2    (bit clock)
#   GND -> GND       LCK -> GPIO41   (word select)
#   SCK -> unused    DIN -> GPIO48   (data)
#   speaker (8 ohm 2W) -> module's L+ / L- output terminals
#
# VOLUME is the only thing you normally need to touch. 0.9 was painfully loud;
# 0.15 is comfortable indoors. If the module has a trimmer pot, that stacks on
# top of this, so back the pot off too rather than fighting it in software.
#
# Run: python -m mpremote connect COM4 run i2s_amp.py

from machine import I2S, Pin
import math, struct, time

BCK, LCK, DIN = 2, 41, 48
RATE = 16000
VOLUME = 0.05          # 0.0 - 1.0

NOTES = {
    "C4": 262, "D4": 294, "E4": 330, "F4": 349, "G4": 392, "A4": 440, "B4": 494,
    "C5": 523, "D5": 587, "E5": 659, "F5": 698, "G5": 784, "A5": 880,
    "REST": 0,
}

audio = I2S(0, sck=Pin(BCK), ws=Pin(LCK), sd=Pin(DIN),
            mode=I2S.TX, bits=16, format=I2S.MONO, rate=RATE, ibuf=20000)


def tone(freq, ms, vol=VOLUME):
    """Play one frequency for ms milliseconds."""
    n = int(RATE * ms / 1000)
    if freq <= 0:
        audio.write(bytearray(n * 2))      # silence
        return
    buf = bytearray()
    for i in range(n):
        v = int(math.sin(2 * math.pi * freq * i / RATE) * vol * 32767)
        buf += struct.pack("<h", v)
    audio.write(buf)


def play(sequence, gap_ms=40):
    for name, ms in sequence:
        tone(NOTES.get(name, 0), ms)
        time.sleep_ms(gap_ms)


try:
    print("volume", VOLUME, "- edit VOLUME at the top to change")

    print("two beeps")
    for _ in range(2):
        tone(1000, 200)
        time.sleep_ms(200)

    print("rising scale")
    for name in ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"):
        tone(NOTES[name], 250)
        time.sleep_ms(40)

    print("short melody")
    play([("E5", 200), ("E5", 200), ("REST", 120), ("E5", 200),
          ("REST", 120), ("C5", 200), ("E5", 200), ("G5", 400),
          ("REST", 200), ("G4", 400)])

    print("done")

finally:
    audio.deinit()
    print("I2S released")
