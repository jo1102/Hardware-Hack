# Second SD probe: can we point SDCard at Freenove's pins (CLK=39 CMD=38 D0=40)?
#
# Note: GPIO 33-37 are consumed by this module's Octal PSRAM, so they cannot be
# used as an SPI chip-select. Pin objects are built inside the try blocks so an
# invalid pin does not abort the whole run.

import os
from machine import SDCard, Pin

print("SDCard methods:", [d for d in dir(SDCard) if not d.startswith("_")])

CLK, CMD, D0 = 39, 38, 40


def attempt(label, build):
    print("\n---", label)
    sd = None
    try:
        sd = build()
    except TypeError as e:
        print("  rejected args:", e)
        return
    except Exception as e:
        print("  construct failed:", type(e).__name__, e)
        return
    try:
        os.mount(sd, "/sd")
        print("  MOUNTED:", os.listdir("/sd"))
        try:
            st = os.statvfs("/sd")
            print("  %.1f MB total" % (st[1] * st[2] / 1048576))
        except Exception as e:
            print("  statvfs:", e)
        os.umount("/sd")
        print("  unmounted")
    except Exception as e:
        print("  mount failed:", type(e).__name__, e)
        try:
            os.umount("/sd")
        except Exception:
            pass
    finally:
        try:
            sd.deinit()
        except Exception:
            pass


attempt("slot=1 width=1 + explicit sdmmc pins",
        lambda: SDCard(slot=1, width=1, sck=Pin(CLK), cmd=Pin(CMD), d0=Pin(D0)))

attempt("slot=1 width=1 + clk/cmd/d0 kwarg names",
        lambda: SDCard(slot=1, width=1, clk=Pin(CLK), cmd=Pin(CMD), d0=Pin(D0)))

attempt("slot=0 width=1", lambda: SDCard(slot=0, width=1))

attempt("slot=1 freq lowered", lambda: SDCard(slot=1, width=1, freq=400000))

# SPI-mode fallback using a free pin (48 is otherwise our audio DIN, but this
# probe runs standalone so it is safe to borrow it briefly for CS).
attempt("slot=2 spi with cs=GPIO21",
        lambda: SDCard(slot=2, sck=Pin(CLK), mosi=Pin(CMD), miso=Pin(D0), cs=Pin(21)))
