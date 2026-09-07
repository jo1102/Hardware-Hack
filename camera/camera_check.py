"""
Camera health check. Runs on your LAPTOP (not the board).

Captures a frame on the ESP32, copies it over USB, decodes it to a PNG you
can open, and reports whether it looks like a real image or corruption.

    python camera_check.py

Use this after reseating the camera ribbon cable. No WiFi involved - it
isolates the camera itself from any networking issues.
"""

import subprocess
import sys
import os

PORT = "COM3"
W, H = 320, 240
OUT_PNG = "camera_check.png"

BOARD_CODE = """
from camera import Camera, FrameSize, PixelFormat
import time
cam = Camera(frame_size=FrameSize.QVGA, pixel_format=PixelFormat.GRAYSCALE,
             xclk_freq=10000000, init=False)
cam.init()
time.sleep_ms(800)
# Discard early frames - exposure and gain are still settling.
for _ in range(8):
    buf = cam.capture()
    time.sleep_ms(120)
f = open('/check.bin', 'wb'); f.write(buf); f.close()
print('CAPTURED', len(buf))
cam.deinit()
"""


def mpremote(*args, **kw):
    return subprocess.run([sys.executable, "-m", "mpremote", "connect", PORT] +
                          list(args), capture_output=True, text=True, **kw)


def main():
    print("capturing on the board ...")
    r = mpremote("exec", BOARD_CODE)
    out = (r.stdout or "") + (r.stderr or "")
    if "CAPTURED" not in out:
        print("capture failed:")
        print(out.strip()[:400])
        print("\nIf this says the port is busy, close Thonny or any terminal "
              "running a camera script.")
        return 1
    print([l for l in out.splitlines() if "CAPTURED" in l][0])

    print("copying over USB ...")
    mpremote("cp", ":check.bin", "./check.bin")
    if not os.path.exists("check.bin"):
        print("copy failed")
        return 1

    data = open("check.bin", "rb").read()
    expected = W * H
    print("received %d bytes (expected %d)" % (len(data), expected))
    if len(data) < expected:
        print("frame is short - the driver returned a truncated buffer")
        return 1

    # A real image has most neighbouring pixels close in value, but also a
    # decent spread of values overall. Corruption fails one or both: either
    # wild pixel-to-pixel jumps, or almost no distinct values at all.
    diffs = [abs(data[i] - data[i + 1]) for i in range(0, expected - 1, 7)]
    mean_diff = sum(diffs) / len(diffs)
    distinct = len(set(data[:20000]))
    dark = sum(1 for b in data[:20000] if b < 16) * 100.0 / 20000

    print("\n  mean neighbour difference : %.1f   (real image: under ~15)" % mean_diff)
    print("  distinct grey levels     : %d / 256  (real image: over ~60)" % distinct)
    print("  near-black pixels        : %.0f%%      (real image: usually under 60%%)" % dark)

    try:
        from PIL import Image
        Image.frombytes("L", (W, H), data[:expected]).save(OUT_PNG)
        print("\nwrote %s - open it and look" % OUT_PNG)
    except ImportError:
        print("\n(install pillow to save a PNG:  pip install pillow)")

    # No automatic verdict. These statistics were tried and they cannot tell
    # a real photo from horizontal streaking on a dark field - both give a low
    # neighbour difference and plenty of distinct values. Open the PNG and
    # judge with your eyes; that is the only reliable test.
    print("\nOpen %s and look at it." % OUT_PNG)
    print("  A real image  -> recognisable shapes, even if dim or noisy.")
    print("  Still broken  -> horizontal streaks, banding, or coloured noise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
