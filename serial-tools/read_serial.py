import serial, time, sys

port = "COM6"
baud = 115200
try:
    s = serial.Serial(port, baud, timeout=0.2)
except Exception as e:
    print("could not open", port, ":", e)
    sys.exit(1)

# Reset the ESP32-S3 (USB-JTAG) so we capture the full boot log.
try:
    s.setDTR(False); s.setRTS(True); time.sleep(0.1)
    s.setRTS(False); time.sleep(0.1)
except Exception:
    pass

end = time.time() + 15
buf = b""
while time.time() < end:
    data = s.read(256)
    if data:
        buf += data
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()
        if b"Camera Ready" in buf:
            time.sleep(0.3)
            data = s.read(512)
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
            break
s.close()
print("\n----END OF CAPTURE----")
