"""
Hard-reset the ESP32 and print everything it says over serial.

This is the tool for seeing what boot.py and main.py print at startup --
the messages you'd otherwise miss because they happen before you can
attach a terminal.

Usage:
    python watch_boot.py                 # reset, then listen 20 seconds
    python watch_boot.py --seconds 60
    python watch_boot.py --no-reset      # just listen to a running board
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is not installed. Run:  pip install pyserial")

PORT = "COM3"
BAUD = 115200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as exc:
        sys.exit(f"could not open {args.port}: {exc}\n"
                 "Something else probably has the port open.")

    if not args.no_reset:
        # This board's DTR/RTS lines don't reliably reset it, so instead we
        # drive MicroPython's own REPL: Ctrl-C twice to break out of whatever
        # is running, then Ctrl-D to soft-reboot, which re-runs boot.py and
        # main.py with all their output coming back over this same port.
        ser.reset_input_buffer()
        ser.write(b"\x03\x03")
        time.sleep(0.3)
        ser.reset_input_buffer()
        ser.write(b"\x04")
        print(f"--- soft-rebooted {args.port}, listening {args.seconds:.0f}s ---")
    else:
        print(f"--- listening on {args.port} for {args.seconds:.0f}s ---")

    deadline = time.monotonic() + args.seconds
    buffer = bytearray()
    start = time.monotonic()
    got_anything = False

    while time.monotonic() < deadline:
        data = ser.read(ser.in_waiting or 1)
        if not data:
            continue
        got_anything = True
        buffer.extend(data)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            text = line.decode("utf-8", errors="replace").rstrip("\r")
            print(f"[{time.monotonic() - start:6.2f}s] {text}")

    # Flush any trailing partial line.
    if buffer:
        print(f"[{time.monotonic() - start:6.2f}s] "
              f"{buffer.decode('utf-8', errors='replace')}")

    ser.close()
    print("--- done ---")
    if not got_anything:
        print("Nothing was received. If the board is running main.py's server "
              "loop it may simply have nothing left to say -- that's normal.")


if __name__ == "__main__":
    main()
