# Tries several SDA/SCL pin combinations and reports which one sees a device.
# Safe to run - it only reads the bus. Backlight already confirmed on, so this
# is just hunting for which pins SDA/SCL are actually landing on.
from machine import I2C, Pin
import time

# (sda, scl) pairs to try. First is the intended wiring, second is it swapped,
# then the manual's default and a couple of nearby safe pins.
combos = [
    (14, 42),   # intended
    (42, 14),   # swapped
    (13, 14),   # manual default (only works if camera ribbon is off)
    (8, 9),     # common S3 default I2C
    (1, 2),     # Freenove tutorial pins on some sketches
]

found_any = False
for sda, scl in combos:
    try:
        i2c = I2C(scl=Pin(scl), sda=Pin(sda), freq=100000)
        time.sleep_ms(50)
        devs = i2c.scan()
    except Exception as e:
        print("SDA=%2d SCL=%2d -> error: %s" % (sda, scl, e))
        continue
    if devs:
        found_any = True
        print("SDA=%2d SCL=%2d -> FOUND %s" % (sda, scl, [hex(a) for a in devs]))
    else:
        print("SDA=%2d SCL=%2d -> nothing" % (sda, scl))

print("----")
if found_any:
    print("Use the SDA/SCL numbers from the line that says FOUND.")
else:
    print("No device on any combo. A data wire is loose or in the wrong")
    print("breadboard row, or SDA and SCL share a row. Reseat both jumpers.")
