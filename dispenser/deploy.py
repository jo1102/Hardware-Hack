"""Put the Kairo agent on the Freenove board in one command.

    python dispenser/deploy.py                 # find the port, copy, restart
    python dispenser/deploy.py --port COM4     # if you already know it
    python dispenser/deploy.py --no-wav        # skip the 160KB chime upload

The agent needs the existing drivers from this repo alongside it, so this
copies all of them to the board root and leaves main.py last - main.py is
what MicroPython auto-runs, and a half-uploaded set of imports underneath it
would send the board into a boot loop.

The COM port number changes between reboots on this board (it exposes both a
native USB and a WCH UART bridge), which is why the default is to go and
look rather than hard-code one.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (source path, name on the board). Order matters: dependencies first.
FILES = [
    ("servo/servo.py", "servo.py"),
    ("audio/audio_amp.py", "audio_amp.py"),
    ("lcd/LCD_API.py", "LCD_API.py"),
    ("lcd/I2C_LCD.py", "I2C_LCD.py"),
    ("dispenser/lcdview.py", "lcdview.py"),
    ("dispenser/hardware.py", "hardware.py"),
    ("dispenser/main.py", "main.py"),
]

WAV = ("audio/jingle5.wav", "jingle5.wav")


def mpremote(*args, **kwargs):
    cmd = [sys.executable, "-m", "mpremote"] + list(args)
    return subprocess.run(cmd, cwd=ROOT, **kwargs)


def find_port():
    out = subprocess.run([sys.executable, "-m", "mpremote", "connect", "list"],
                         capture_output=True, text=True).stdout
    ports = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].upper().startswith(("COM", "/DEV/")):
            ports.append((parts[0], line.strip()))
    if not ports:
        return None
    # A board running MicroPython reports Espressif's USB vendor id, 303a.
    for port, line in ports:
        if "303a" in line.lower():
            return port
    return ports[0][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="COM port; auto-detected if omitted")
    ap.add_argument("--no-wav", action="store_true",
                    help="do not upload the chime WAV (slow over the REPL)")
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        print("No board found. Plug it in, then:")
        print("  python -m mpremote connect list")
        return 1
    print("board on %s" % port)

    files = list(FILES)
    if not args.no_wav and os.path.exists(os.path.join(ROOT, WAV[0])):
        # Slipped in before main.py so the agent never starts up looking for
        # a file that is still uploading.
        files.insert(-1, WAV)

    for src, dest in files:
        path = os.path.join(ROOT, src)
        if not os.path.exists(path):
            print("  MISSING  %s" % src)
            continue
        size = os.path.getsize(path)
        print("  copy     %-22s -> :%s  (%d bytes)" % (src, dest, size))
        result = mpremote("connect", port, "fs", "cp", src, ":" + dest)
        if result.returncode != 0:
            print("\nUpload of %s failed." % src)
            print("If the board just vanished from the port list, unplug the")
            print("servo and try again - see the Power section of README.md.")
            return result.returncode

    print("\nrestarting the board so main.py runs...")
    mpremote("connect", port, "soft-reset")
    print("done. Now start the website:")
    print("  python site/bridge.py --port %s" % port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
