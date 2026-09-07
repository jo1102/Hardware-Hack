# Can MicroPython see the SD card on this board?
#
# Freenove's own sketches use the SDMMC peripheral on:
#   CLK=39  CMD=38  D0=40
# so that is what we try here. Prints what it finds, changes nothing.

import os, sys

print("MicroPython:", sys.implementation, sys.platform)

print("\nflash root:", os.listdir("/"))

try:
    from machine import SDCard
    print("\nmachine.SDCard available")
except ImportError:
    SDCard = None
    print("\nmachine.SDCard NOT available in this build")

if SDCard:
    # slot=1 is the SDMMC controller; width=1 matches Freenove's D0-only wiring
    for kw in ({"slot": 1, "width": 1}, {"slot": 1}, {}):
        try:
            print("\ntrying SDCard(%s)" % kw)
            sd = SDCard(**kw)
            os.mount(sd, "/sd")
            print("  MOUNTED. /sd contains:", os.listdir("/sd"))
            try:
                st = os.statvfs("/sd")
                print("  size: %.1f MB free" % (st[0] * st[3] / 1048576))
            except Exception as e:
                print("  statvfs failed:", e)
            os.umount("/sd")
            sd.deinit()
            print("  unmounted cleanly")
            break
        except Exception as e:
            print("  failed:", type(e).__name__, e)
            try:
                os.umount("/sd")
            except Exception:
                pass
