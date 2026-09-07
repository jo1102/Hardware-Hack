import serial, time, sys
s = serial.Serial("COM6", 115200, timeout=0.2)  # no reset - just listen
end = time.time() + 25
buf = b""
while time.time() < end:
    data = s.read(256)
    if data:
        buf += data
        sys.stdout.write(data.decode("utf-8", "replace")); sys.stdout.flush()
        if b"Camera Ready" in buf:
            time.sleep(0.4);
            sys.stdout.write(s.read(600).decode("utf-8","replace"))
            break
s.close()
print("\n----END----")
