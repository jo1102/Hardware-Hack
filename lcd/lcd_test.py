# LCD1602 test - writes two lines of text to the I2C screen.
#
# UNTESTED: written while the original LCD backpack was dead (silent on I2C
# after a reverse-polarity connection). It follows the kit's own driver API,
# so it should work as-is on a replacement, but expect to verify it.
#
# Needs these on the board first:
#   python -m mpremote connect COM3 cp LCD_API.py :LCD_API.py + cp I2C_LCD.py :I2C_LCD.py
#
# Run:
#   python -m mpremote connect COM3 run lcd_test.py
#
# WIRING - on this board 5V and GND are DIAGONALLY OPPOSITE corners, which is
# how the first module got connected backwards and destroyed. Check twice:
#   VCC -> 5V     bottom-most pin, LEFT column
#   GND -> GND    bottom-most pin, RIGHT column
#   SDA -> GPIO14 second from bottom, LEFT column
#   SCL -> GPIO42 fifth from top, RIGHT column
#
# GPIO42 is used instead of the manual's GPIO13 because GPIO13 is the
# camera's pixel clock (CAM_PCLK) and is unusable for I2C while the camera
# ribbon is attached.

from machine import I2C, Pin
from I2C_LCD import I2cLcd
import time

SCL_PIN = 42
SDA_PIN = 14
ADDR = 0x27      # PCF8574 default; some boards are 0x3F
ROWS, COLS = 2, 16

i2c = I2C(scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=100000)

found = i2c.scan()
print("I2C devices found:", [hex(a) for a in found] if found else "NONE")

if not found:
    print("Nothing on the bus. Check VCC/GND are not swapped, and that")
    print("SDA is on GPIO%d and SCL on GPIO%d." % (SDA_PIN, SCL_PIN))
else:
    addr = ADDR if ADDR in found else found[0]
    if addr != ADDR:
        print("using detected address", hex(addr), "instead of", hex(ADDR))

    lcd = I2cLcd(i2c, addr, ROWS, COLS)
    lcd.clear()
    lcd.putstr("Hello from the")
    lcd.move_to(0, 1)
    lcd.putstr("ESP32-S3!")
    print("text written - if the screen looks blank but is lit, turn the")
    print("contrast trimmer (small blue screw) on the back of the module")

    time.sleep(3)

    # A short counter, to show the screen updating rather than sitting static.
    for i in range(5, 0, -1):
        lcd.clear()
        lcd.putstr("Counting down")
        lcd.move_to(0, 1)
        lcd.putstr("%d ..." % i)
        time.sleep(1)

    lcd.clear()
    lcd.putstr("Done.")
    print("done")
