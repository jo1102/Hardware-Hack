"""Read boot output from any port, for when a board stops responding.

    python serial-tools/read_any.py COM5 [seconds] [baud]

Resets the board first (DTR/RTS toggle) so the whole boot log is captured,
including a firmware crash or reboot loop that happens before the REPL prompt.
"""

import sys, time
import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 12
baud = int(sys.argv[3]) if len(sys.argv) > 3 else 115200

try:
    s = serial.Serial(port, baud, timeout=0.2)
except Exception as e:
    print("could not open %s: %s" % (port, e))
    sys.exit(1)

try:
    s.setDTR(False)
    s.setRTS(True)
    time.sleep(0.1)
    s.setRTS(False)
except Exception:
    pass

# A bare newline nudges a live MicroPython REPL into printing its prompt.
time.sleep(0.5)
s.write(b"\r\n")

end = time.time() + secs
got = False
while time.time() < end:
    data = s.read(256)
    if data:
        got = True
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()
s.close()

if not got:
    print("(no output at %d baud in %.0fs - board is silent)" % (baud, secs))
print("\n----END----")
