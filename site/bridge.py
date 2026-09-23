"""Kairo bridge - the laptop half of the demo.

    python site/bridge.py                    # find the board, serve the site
    python site/bridge.py --port COM4        # if you already know the port
    python site/bridge.py --no-serial        # UI only, no hardware attached

It does three jobs:

  1. holds ONE persistent USB serial connection to the dispenser and keeps
     it alive - re-detecting the port, re-opening it and re-syncing the clock
     on its own, so nothing has to be unplugged or reset between commands;
  2. serves site/index.html and a small JSON API over HTTP, with CORS wide
     open so the page also works when opened straight off disk or from a
     phone on the same network;
  3. reads the SenseCraft camera on the XIAO over its own USB cable: it
     backs up the ultrasonic sensor and feeds the Check-in page.

WHY SERIAL AND NOT WIFI
Venue networks routinely put clients on isolated subnets, so a browser often
cannot reach an ESP32 on the same SSID at all. The USB cable is already
there, always works, and adds no latency worth measuring.

HTTP API
    GET  /                  the carer console
    GET  /patient           the patient kiosk
    GET  /api/state         everything the UI needs, one object
    POST /api/command       {"c":"dispense","i":0} - forwarded verbatim
    GET  /api/ports         serial ports the bridge can see
    POST /api/port          {"port":"COM4"} - switch without restarting
    GET  /api/camera        where the XIAO camera was last seen
    POST /api/camera/find   sweep the local subnet looking for it
    GET  /api/camera/stream the SenseCraft camera's frames, live, as MJPEG
    GET  /test              the hardware testing page

Pass --log FILE to append everything the board says to a file as well as the
in-memory console, which only holds the last 250 lines and dies with the
process.

Only the Python standard library plus pyserial, which is already installed
here as a dependency of mpremote.
"""

import argparse
import concurrent.futures
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

HERE = os.path.dirname(os.path.abspath(__file__))

BAUD = 115200
RESYNC_SECONDS = 900        # push the time back down every 15 minutes
HELLO_TIMEOUT = 4.0         # how long to wait for the agent to announce itself
SILENCE_LIMIT = 15          # seconds of no state frames before a restart
ESPRESSIF_VID = 0x303A      # native USB on both ESP32-S3 boards here
MICROPYTHON_PID = 0x4001    # the dispenser: MicroPython's own USB serial
XIAO_PID = 0x1001           # the camera: the chip's built-in USB serial


# ---------------------------------------------------------------------------
# Finding the camera
# ---------------------------------------------------------------------------
# The XIAO gets its address from DHCP and it changes every boot, which is only
# a nuisance until you power the board from the breadboard instead of USB -
# then there is no serial log to read it off at all.
#
# A phone hotspot makes the usual answers unreliable: a static IP needs a
# subnet that varies by phone vendor and OS version, and mDNS depends on
# multicast reaching between hotspot clients, which many hotspots drop. So the
# bridge finds the camera instead. It is already Python on the same network
# with no browser sandbox in the way.
#
# Deliberately ON DEMAND, never automatic: sweeping a subnet you happen to be
# joined to is not something a program should do on startup uninvited,
# especially on a university network.

CAMERA_STREAM_PORT = 81     # the MJPEG server - almost nothing else uses it
SCAN_CONNECT_TIMEOUT = 0.6
SCAN_CONFIRM_TIMEOUT = 1.5
SCAN_WORKERS = 128


def local_ipv4s():
    """Every IPv4 address this machine has, minus loopback."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass
    # The routed address - on a hotspot this is the one that matters. No
    # packets are sent; connect() on a UDP socket just picks a route.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    return sorted(ip for ip in found if not ip.startswith("127."))


def probe_camera(host):
    """Is a CameraWebServer answering on this address?

    Two stages on purpose. Port 81 is a cheap, highly specific filter - it is
    the stream server and little else listens there - and only the handful of
    hosts that pass get the slower HTTP confirmation. Checking /status rather
    than trusting port 81 alone is what stops the bridge reporting some
    unrelated device as the camera.
    """
    try:
        with socket.create_connection((host, CAMERA_STREAM_PORT),
                                      timeout=SCAN_CONNECT_TIMEOUT):
            pass
    except Exception:
        return False
    try:
        conn = http.client.HTTPConnection(host, 80, timeout=SCAN_CONFIRM_TIMEOUT)
        conn.request("GET", "/status")
        body = conn.getresponse().read(1200).decode("utf-8", "replace")
        conn.close()
        return '"framesize"' in body and '"xclk"' in body
    except Exception:
        return False


class CameraFinder:
    """Remembers where the camera was, and goes looking when asked."""

    def __init__(self):
        self.ip = None
        self.checked = 0.0
        self.scanning = False
        self.note = ""
        self.scanned = 0
        self._lock = threading.Lock()

    def snapshot(self):
        return {"ip": self.ip, "scanning": self.scanning, "note": self.note,
                "scanned": self.scanned,
                "age": round(time.time() - self.checked, 1) if self.checked else None,
                "subnets": ["%s.0/24" % ip.rsplit(".", 1)[0] for ip in local_ipv4s()]}

    def find(self, hint=None):
        with self._lock:
            if self.scanning:
                return False
            self.scanning = True
        threading.Thread(target=self._run, args=(hint,), daemon=True).start()
        return True

    def _hosts(self):
        seen, hosts = set(), []
        for mine in local_ipv4s():
            # ponytail: assumes /24. True for every phone hotspot and home
            # router, which is where this is used. On a larger subnet (a
            # university /17, say) it scans only the local /24 and will miss
            # the camera - but such networks isolate clients anyway, so the
            # stream would not work even if it were found. Upgrade path: read
            # the real prefix length per adapter and cap the sweep at ~1024
            # hosts.
            try:
                network = ipaddress.ip_interface(mine + "/24").network
            except ValueError:
                continue
            for host in network.hosts():
                text = str(host)
                if text != mine and text not in seen:
                    seen.add(text)
                    hosts.append(text)
        return hosts

    def _run(self, hint):
        try:
            self.scanned = 0
            # Somewhere it used to be, or something the carer typed in: worth
            # one cheap check before sweeping 250 addresses.
            for candidate in [hint, self.ip]:
                if candidate and probe_camera(candidate):
                    self.ip, self.checked = candidate, time.time()
                    self.note = "still at %s" % candidate
                    return

            hosts = self._hosts()
            if not hosts:
                self.note = "no network to scan - is the laptop on the hotspot?"
                self.checked = time.time()
                return

            with concurrent.futures.ThreadPoolExecutor(SCAN_WORKERS) as pool:
                futures = {pool.submit(probe_camera, h): h for h in hosts}
                for future in concurrent.futures.as_completed(futures):
                    self.scanned += 1
                    if future.result():
                        self.ip = futures[future]
                        self.checked = time.time()
                        self.note = "found at %s" % self.ip
                        for other in futures:
                            other.cancel()
                        return
            self.ip = None
            self.checked = time.time()
            self.note = ("nothing answering on %d addresses - check the camera "
                         "has power and joined the hotspot" % len(hosts))
        except Exception as e:
            self.note = "scan failed: %s" % e
        finally:
            self.scanning = False
            print("  camera: %s" % self.note, flush=True)


camera = CameraFinder()


# ---------------------------------------------------------------------------
# The camera - backup presence sensor and check-in feed, off one USB cable
# ---------------------------------------------------------------------------
# The box drops a waiting dose once somebody is at it. The HC-SR04 on the
# dispenser answers that itself; the camera backs it up and sends the box
# {"c":"present"} on any frame sure enough of a person. With camera/XiaoCam
# on the XIAO, the USB cable only says where its WiFi stream is and watch()
# runs YOLOX on the frames here. With SenseCraft on it instead, its own model's
# results come over USB, each with the JPEG it was made from for Check-in.
# No second program to run either way.
#
# It starts straight away, dispenser or not - Check-in should work either
# way - and tells the boards apart by their USB ids, so it never opens the
# dispenser's port. If the camera goes quiet it asks again, then reconnects.
# The SenseCraft web page cannot share the port - stop the bridge while that
# page is open.

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sensecraft"))
import presence_relay as sensecraft  # noqa: E402  the SenseCraft parsing

# Person detection for the WiFi camera (camera/XiaoCam) runs here, on the
# laptop. Optional: without OpenCV or the model, the ultrasonic sensor
# decides alone and Check-in still streams.
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
# OpenCV's model zoo, Apache-2.0: huggingface.co/opencv/object_detection_yolox.
# The 9MB int8 copy scores every frame 0 on this OpenCV - use this one.
YOLOX = os.path.join(HERE, "models", "object_detection_yolox_2022nov.onnx")
SEEN = 60       # % sure of a person before a pill drops. Measured: somebody
                # at the desk 91, empty rooms 1 and 33.


def person_score(net, jpg):
    """YOLOX's confidence, 0-1, that some box in this JPEG is a person.

    The model zoo's own preprocessing: RGB, letterboxed into 640x640 on grey
    114, values left as 0-255. Each of the 8400 rows is [box, objectness,
    80 class scores]; person is COCO class 0. Presence needs no boxes.
    """
    bgr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    h, w = bgr.shape[:2]
    r = min(640 / h, 640 / w)
    pad = np.full((640, 640, 3), 114, np.float32)
    pad[:int(h * r), :int(w * r)] = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                                               (int(w * r), int(h * r)))
    net.setInput(pad.transpose(2, 0, 1)[None])
    rows = net.forward()[0]
    return float((rows[:, 4] * rows[:, 5]).max())


class Vision:
    """What the camera last saw, its latest frame, and its state in words."""

    def __init__(self):
        self.port = None
        self.note = "looking for the camera"
        self.near = False
        self.score = None       # 0-100 for the person class, or None
        self.seen = ""          # "class 1 at 91%" - how the carer checks it
        self.at = 0.0           # when the last result arrived
        self.frame = None       # the latest JPEG, for the Check-in page
        self.frame_at = 0.0
        self._asked = 0.0       # when INVOKE was last sent
        self._told = 0.0        # when the box was last told somebody is here
        self.viewers = set()    # Check-in streams open right now, by thread
        self._sent = None       # INVOKE or SAMPLE, whichever the board is running
        self._heard = 0.0       # when the board last sent any JSON line

    def snapshot(self):
        now = time.time()
        return {"port": self.port, "note": self.note, "near": self.near,
                "score": self.score, "seen": self.seen,
                "age": round(now - self.at, 1) if self.at else None,
                "frame_age": round(now - self.frame_at, 1) if self.frame else None}

    QUIET_ASK = 5       # seconds without a result before asking again
    QUIET_DROP = 15     # ...and before closing the port and finding it anew

    def run(self, device):
        ser, buf, since = None, b"", 0.0
        while True:
            if ser is None:
                ser, buf, since = self._find(device), b"", time.time()
                if ser is None:
                    time.sleep(3)
                    continue
            try:
                buf += ser.read(ser.in_waiting or 1)
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self._line(raw.decode("utf-8", "replace"), ser, device)
                # While somebody watches Check-in the model is paused and the
                # board just sends pictures (SAMPLE) - far faster than one per
                # inference. The ultrasonic sensor still covers presence.
                want = sensecraft.SAMPLE if self.viewers else sensecraft.INVOKE
                if want is not self._sent:
                    self._sent, self._asked, since = want, time.time(), time.time()
                    ser.write(want)
                    self.near = False
                    self.note = ("model paused for check-in on %s" % self.port if self.viewers
                                 else "resuming the model on %s" % self.port)
                    device.log("camera: %s" % self.note)
                # A camera that stops talking - rebooted, or the SenseCraft
                # page stopped its model - gets asked again, then reconnected,
                # instead of sitting "connected" to silence forever.
                quiet = time.time() - max(self.at, self.frame_at, self._heard, since)
                if quiet > self.QUIET_DROP:
                    raise OSError("nothing for %ds" % self.QUIET_DROP)
                if quiet > self.QUIET_ASK and time.time() - self._asked > self.QUIET_ASK:
                    self._asked = time.time()
                    ser.write(self._sent)
            except Exception as e:
                device.log("bridge: lost the camera on %s (%s), looking again" % (self.port, e))
                self._close(ser)
                ser = None
                continue
            if len(buf) > 262144:
                buf = b""       # a line with no end is noise, not a frame

    def _close(self, ser):
        try:
            ser.close()
        except Exception:
            pass
        self.port, self.near, self.at, self.frame, self._sent = None, False, 0.0, None, None

    def _find(self, device):
        """Open the Espressif port that is not the dispenser and speaks SSCMA.

        The XIAO's own USB id first; never MicroPython's (that is the
        dispenser, and an AT command sent to it is noise it has to reject).
        """
        busy = []
        ports = sorted(Device.candidates(), key=lambda info: info["pid"] != XIAO_PID)
        for info in ports:
            port = info["port"]
            if port == device.port or not info["likely"] or info["pid"] == MICROPYTHON_PID:
                continue
            ser = serial.Serial()
            ser.port, ser.baudrate, ser.timeout = port, sensecraft.BAUD, 0.3
            ser.dtr = ser.rts = False   # asserted on open, these reboot the XIAO
            try:
                ser.open()
            except Exception:
                busy.append(port)
                continue
            try:
                ser.write(sensecraft.INVOKE)
                deadline = time.time() + 6      # room for a reboot anyway
                while time.time() < deadline:
                    # SenseCraft starts every line with "\r", so strip first.
                    line = ser.readline().strip()
                    if line.startswith(b"{") and b'"name"' in line:
                        self.port, self._asked, self._sent = port, time.time(), sensecraft.INVOKE
                        self.note = "connected on %s, waiting for a reading" % port
                        device.log("bridge: camera on %s" % port)
                        return ser
            except Exception:
                pass
            ser.close()
        self.note = ("%s is in use by another program - the SenseCraft page, or an older "
                     "bridge still running" % busy[0]
                     if busy else "no SenseCraft camera on USB - plug the XIAO into this laptop")
        return None

    def _line(self, text, ser, device):
        now = time.time()
        if text.lstrip().startswith("{"):
            self._heard = now               # alive, whether or not it is a result
        # A reboot (INIT@...) loses the request: ask again, not in a loop.
        if '"INIT@' in text and now - self._asked > 5:
            self._asked = now
            ser.write(self._sent)
        # camera/XiaoCam says where its WiFi stream is every two seconds, so
        # Check-in never has to search for it. SenseCraft sends no "ip".
        ip = re.search(r'"ip":"([\d.]+)"', text)
        if ip and ip.group(1) != camera.ip:
            camera.ip, camera.checked = ip.group(1), now
            camera.note = "on WiFi at %s (it said so over USB)" % camera.ip
            device.log("camera: WiFi stream at http://%s:81/stream" % camera.ip)
        frame = sensecraft.jpeg(text)       # INVOKE and SAMPLE both carry one
        if frame:
            self.frame, self.frame_at = frame, now
        got = sensecraft.parse_result(text)
        if got is not None:
            self._reading(*sensecraft.person(*got), "on %s" % self.port, device)

    def _reading(self, near, score, seen, where, device):
        """One presence reading, from the USB model or from watch()."""
        now = time.time()
        if near != self.near:
            device.log("presence: %s (camera: %s)" % (
                "somebody at the box" if near else "nobody at the box", seen))
        self.near, self.score, self.seen, self.at = near, score, seen, now
        self.note = "reading " + where
        # The camera is the backup: the sonar needs two readings, but one
        # frame sure enough of a person drops the pill on its own.
        if near and (device.state or {}).get("mode") == "due" and now - self._told > 2:
            self._told = now
            device.send({"c": "present", "by": "camera"})

    def watch(self, device):
        """Person detection on the laptop, from the WiFi camera's stills.

        The model small enough to run on the XIAO scored an empty room as
        high as a person in it. YOLOX here draws boxes around people and does
        not: 91% for somebody at the desk, 1-33% for the empty scenes it was
        tried on. It reads /capture on port 80 - the Check-in stream on port
        81 is a separate server, so the two do not fight over it.
        """
        if cv2 is None or not os.path.exists(YOLOX):
            self.note = ("camera detection off - " + (
                "python -m pip install opencv-python-headless" if cv2 is None
                else "download the model, see README step 6"))
            device.log("camera: %s" % self.note)
            return
        net = cv2.dnn.readNet(YOLOX)
        while True:
            ip = camera.ip
            try:
                jpg = urllib.request.urlopen("http://%s/capture" % ip, timeout=3).read() if ip else None
            except OSError:
                jpg = None
            if jpg is None:
                time.sleep(2)
                continue
            score = round(person_score(net, jpg) * 100)
            self._reading(score >= SEEN, score, "a person, %d%% sure" % score if score >= SEEN
                          else "nobody (%d%%)" % score, "from %s over WiFi" % ip, device)
            time.sleep(0.1)


vision = Vision()


class Device:
    """The serial half. One thread owns the port; everyone else uses send()."""

    def __init__(self, port=None, enabled=True, logfile=None):
        self.wanted_port = port
        self.logfile = logfile
        self.enabled = enabled
        self.lock = threading.Lock()
        self.ser = None

        self.port = None
        self.connected = False
        self.agent = False          # port open AND a Kairo agent answered
        self.error = ""
        self.state = {}
        self.console = deque(maxlen=250)
        self.last_frame = 0.0
        self.last_sync = 0.0
        self.opened_at = 0.0
        self.tx = 0
        self.rx = 0
        self._buf = b""
        self._stop = False
        # Ports that opened but had no Kairo agent behind them. Both
        # boards in this project report Espressif's USB vendor id, so
        # auto-detect cannot tell the dispenser from the XIAO camera by
        # identifier alone - it has to try one, and remember.
        self._rejected = set()

    # --- lifecycle -----------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop = True
        self._close()

    def _close(self):
        with self.lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
        self.connected = False
        self.agent = False

    def use_port(self, port):
        """Switch ports live. The loop notices and re-opens."""
        self.wanted_port = port or None
        self._close()
        self.log("bridge: switching to %s" % (port or "auto"))

    # --- discovery -----------------------------------------------------

    @staticmethod
    def candidates():
        if list_ports is None:
            return []
        found = []
        for info in list_ports.comports():
            found.append({
                "port": info.device,
                "description": info.description or "",
                "vid": info.vid,
                "pid": info.pid,
                # An ESP32-S3 on native USB is the likely board; a CH340
                # UART bridge is the same board's other port and also works.
                "likely": info.vid == ESPRESSIF_VID,
            })
        # Most likely first, so auto-detect picks well. The two boards say
        # which is which in their USB ids: MicroPython on the dispenser is
        # 303a:4001, the SenseCraft XIAO's built-in USB serial is 303a:1001.
        # So the dispenser search tries the XIAO last - opening it resets it.
        found.sort(key=lambda item: (not item["likely"], item["pid"] == XIAO_PID, item["port"]))
        return found

    def _pick_port(self):
        """Choose a port to try, skipping ones already found to be wrong.

        Without the skip this loops on the first candidate forever. With the
        XIAO camera plugged in too - same vendor id, sorts the same way - it
        would sit there soft-resetting the camera and never reach the
        dispenser.
        """
        if self.wanted_port:
            return self.wanted_port
        options = [o["port"] for o in self.candidates() if o["port"] != vision.port]
        if not options:
            return None
        fresh = [p for p in options if p not in self._rejected]
        if not fresh:
            # All tried. Start over rather than give up - deploy.py may have
            # just put the agent onto one of them.
            self._rejected.clear()
            fresh = options
        return fresh[0]

    # --- the connection loop -------------------------------------------

    def _loop(self):
        while not self._stop:
            if not self.enabled or serial is None:
                time.sleep(1.0)
                continue

            if self.ser is None:
                port = self._pick_port()
                if not port:
                    self.error = "no serial ports found"
                    time.sleep(1.5)
                    continue
                if not self._open(port):
                    time.sleep(4.0)
                    continue

            try:
                self._read_available()
            except Exception as e:
                self.error = "read failed: %s" % e
                self.log("bridge: lost %s (%s)" % (self.port, e))
                self._close()
                time.sleep(1.0)
                continue

            now = time.time()
            if self.agent and now - self.last_sync > RESYNC_SECONDS:
                self._sync_clock()

            # The board sends a state frame every second, so silence means
            # the agent is wedged or was Ctrl-C'd and is worth kicking. The
            # threshold has to clear the longest legitimate blocking stretch:
            # a servo creep plus a five-second WAV is about eight seconds, so
            # anything under ~10s here would reset the board mid-chime.
            if self.agent and self.last_frame and now - self.last_frame > SILENCE_LIMIT:
                self.log("bridge: silent for %ds, restarting the agent" % SILENCE_LIMIT)
                self._close()
                continue

            time.sleep(0.02)

    def _open(self, port):
        try:
            self.ser = serial.Serial(port, BAUD, timeout=0, write_timeout=2)
        except Exception as e:
            self.error = "%s: %s" % (port, e)
            self.ser = None
            return False

        self.port = port
        self.connected = True
        self.opened_at = time.time()
        self._buf = b""
        self.log("bridge: opened %s" % port)

        # Ctrl-C, Ctrl-C, Ctrl-D. The first two interrupt whatever is running
        # (a live agent, a stuck script, or nothing) and leave a REPL; the
        # Ctrl-D soft-resets, which re-runs main.py from the top. That single
        # sequence is why a crashed or stale agent never needs a power cycle.
        try:
            self.ser.write(b"\x03\x03")
            time.sleep(0.25)
            self.ser.reset_input_buffer()
            self.ser.write(b"\x04")
        except Exception as e:
            self.error = "handshake failed: %s" % e
            self._close()
            return False

        # Wait for the agent to announce itself before claiming success -
        # this is also how the wrong port (the XIAO camera board, say) gets
        # rejected instead of silently sitting there connected to nothing.
        deadline = time.time() + HELLO_TIMEOUT
        while time.time() < deadline and not self.agent:
            try:
                self._read_available()
            except Exception:
                break
            time.sleep(0.05)

        if not self.agent:
            self._rejected.add(port)
            others = [p for p in (o["port"] for o in self.candidates())
                      if p not in self._rejected]
            self.error = ("%s answered, but no Kairo agent%s. Run "
                          "python dispenser/deploy.py"
                          % (port, (", trying " + others[0]) if others else ""))
            self.log("bridge: %s" % self.error)
            self._close()
            return False

        self.error = ""          # cleared only now that an agent has answered
        self._rejected.discard(port)
        self._sync_clock()
        self.send({"c": "state"})
        return True

    def _sync_clock(self):
        """Give the board the wall clock. It has no battery-backed RTC."""
        t = time.localtime()
        self.send({"c": "time", "t": [t.tm_year, t.tm_mon, t.tm_mday,
                                      t.tm_hour, t.tm_min, t.tm_sec]})
        self.last_sync = time.time()

    # --- reading -------------------------------------------------------

    def _read_available(self):
        if self.ser is None:
            return
        waiting = self.ser.in_waiting
        chunk = self.ser.read(waiting if waiting else 1)
        if not chunk:
            return
        self.rx += len(chunk)
        self._buf += chunk
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            self._handle_line(raw.decode("utf-8", "replace").strip("\r\n "))
        if len(self._buf) > 8192:
            self._buf = b""      # never let a missing newline eat memory

    def _handle_line(self, line):
        if not line:
            return
        if line.startswith("{"):
            try:
                msg = json.loads(line)
            except ValueError:
                self.log(line)
                return
            kind = msg.get("e")
            if kind == "state":
                self.state = msg
                self.last_frame = time.time()
            elif kind == "hello":
                self.agent = True
                # Restart the silence clock. Without this it still holds the
                # time of the last frame before an unplug or a restart, so the
                # watchdog below fires within a second of every hello - before
                # the agent has sent its first state frame - and kicks the
                # board into a reboot loop that never ends.
                self.last_frame = time.time()
                self.log("device: %s, hardware %s" %
                         (msg.get("fw"), msg.get("hw")))
            elif kind == "log":
                self.log("device: %s" % msg.get("msg"))
            elif kind == "ack" and not msg.get("ok", True):
                self.log("device: command rejected %s" % msg.get("err", ""))
            # 'event' frames arrive inside the next state frame too, so
            # there is nothing extra to do with them here.
            return
        # Anything not JSON is ordinary board output: boot messages, a
        # traceback, a stray print(). Worth showing, so it goes to the
        # console panel in the UI rather than being thrown away.
        self.log(line)
        if "MPY: soft reboot" in line:
            self.agent = False

    def log(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.console.append({"t": stamp, "line": text})
        print("  " + text, flush=True)
        # The in-memory console is a 250-line ring buffer that dies with the
        # process, which is no use for working out what happened before a
        # crash. Appending is best-effort: a logging failure must never take
        # the serial link down with it.
        if self.logfile:
            try:
                with open(self.logfile, "a", encoding="utf-8") as f:
                    f.write("%s %s %s\n" % (time.strftime("%Y-%m-%d"), stamp, text))
            except Exception:
                pass

    # --- writing -------------------------------------------------------

    def send(self, command):
        payload = (json.dumps(command, separators=(",", ":")) + "\r\n").encode()
        with self.lock:
            if self.ser is None:
                return False, "not connected"
            try:
                self.ser.write(payload)
                self.tx += len(payload)
                return True, ""
            except Exception as e:
                return False, str(e)

    # --- what the UI reads --------------------------------------------

    def snapshot(self):
        age = (time.time() - self.last_frame) if self.last_frame else None
        return {
            "bridge": {
                "connected": bool(self.connected and self.agent),
                "port": self.port,
                "error": self.error,
                "enabled": self.enabled,
                "pyserial": serial is not None,
                "frame_age": round(age, 2) if age is not None else None,
                "uptime": round(time.time() - self.opened_at, 1)
                          if self.opened_at else 0,
                "tx": self.tx,
                "rx": self.rx,
                "host_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            "device": self.state,
            "console": list(self.console)[-60:],
            "camera": camera.snapshot(),
            "vision": vision.snapshot(),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "KairoBridge/1.0"
    device = None

    # --- helpers -------------------------------------------------------

    def _cors(self):
        # Wide open on purpose: the page is also opened from file:// and from
        # phones on the LAN, and this serves one local demo on one laptop.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    TYPES = {
        ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml", ".webp": "image/webp", ".ico": "image/x-icon",
        ".woff2": "font/woff2", ".woff": "font/woff",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".txt": "text/plain",
        ".md": "text/markdown; charset=utf-8",
    }

    def _safe_path(self, rel):
        """Resolve a request path inside site/, or return None.

        The site is split across site/app/, so this has to serve
        subdirectories - which means it also has to refuse to walk out of
        the folder. Comparing realpaths is what does that; checking for
        ".." in the string is not enough, because symlinks and Windows
        short names get around it.
        """
        rel = rel.lstrip("/")
        if not rel:
            return None
        target = os.path.realpath(os.path.join(HERE, rel))
        root = os.path.realpath(HERE)
        if target != root and not target.startswith(root + os.sep):
            return None
        if not os.path.isfile(target):
            return None
        return target

    def _file(self, rel):
        path = self._safe_path(rel)
        if path is None:
            self.send_error(404, "%s not found under site/" % rel)
            return
        ctype = self.TYPES.get(os.path.splitext(path)[1].lower(),
                               "application/octet-stream")
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # no-store because the whole point is editing a file and reloading.
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        """The camera's frames as MJPEG, so an <img> plays them live.

        While any viewer is open the model is paused, so frames come as fast
        as the board can send pictures rather than one per detection. Any
        number of viewers can watch; they all read the same latest frame.
        Ends once the camera has sent nothing for ten seconds, or the viewer
        goes away - and the model resumes once the last one has.
        """
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        sent, me = 0.0, threading.get_ident()
        vision.viewers.add(me)          # pauses the model while this is open
        try:
            while time.time() - vision.frame_at < 10:
                if vision.frame and vision.frame_at != sent:
                    sent, frame = vision.frame_at, vision.frame
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                     % len(frame) + frame + b"\r\n")
                time.sleep(0.05)
        except OSError:
            pass            # the viewer closed the page
        finally:
            vision.viewers.discard(me)

    def log_message(self, fmt, *args):
        pass        # the serial console is the interesting output, not this

    # --- routes --------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"

        if path == "/api/state":
            return self._json(self.device.snapshot())
        if path == "/api/camera":
            return self._json(camera.snapshot())
        if path == "/api/camera/stream":
            return self._stream()
        if path == "/api/ports":
            return self._json({"ports": Device.candidates(),
                               "current": self.device.port,
                               "pyserial": serial is not None})
        if path == "/api/hello":
            return self._json({"ok": True, "service": "kairo-bridge"})
        if path.startswith("/api/"):
            return self._json({"ok": False, "error": "no such endpoint"}, 404)

        # A front door per audience, and one for testing the hardware.
        if path in ("/", "/carer", "/dashboard", "/camera", "/settings"):
            return self._file("index.html")
        if path in ("/patient", "/kiosk"):
            return self._file("patient.html")
        if path == "/test":
            return self._file("test.html")

        # Everything else comes off disk, so dropping a font or a logo into
        # site/ (or site/app/) just works.
        return self._file(path)

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"ok": False, "error": "body is not JSON"}, 400)

        if path == "/api/command":
            if not isinstance(body, dict) or "c" not in body:
                return self._json({"ok": False,
                                   "error": "expected {\"c\": ...}"}, 400)
            ok, error = self.device.send(body)
            return self._json({"ok": ok, "error": error, "sent": body})

        if path == "/api/camera/find":
            started = camera.find(body.get("hint"))
            return self._json({"ok": True, "started": started,
                               "camera": camera.snapshot()})
        if path == "/api/port":
            self.device.use_port(body.get("port"))
            return self._json({"ok": True, "port": body.get("port")})

        return self._json({"ok": False, "error": "no such endpoint"}, 404)


def bridge_running(port):
    """Is a Kairo bridge already answering on this port?"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        conn.request("GET", "/api/hello")
        return b"kairo-bridge" in conn.getresponse().read(200)
    except Exception:
        return False


def lan_ip():
    """Best guess at the address a phone on the same WiFi should open."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))       # no packets sent, just picks a route
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description="Kairo bridge")
    ap.add_argument("--port", help="serial port; auto-detected if omitted")
    ap.add_argument("--http", type=int, default=9000, help="HTTP port")
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address; 0.0.0.0 lets phones on the LAN in")
    ap.add_argument("--log", metavar="FILE",
                    help="also append every serial console line to this file")
    ap.add_argument("--no-serial", action="store_true",
                    help="serve the UI without touching any hardware")
    args = ap.parse_args()

    if serial is None and not args.no_serial:
        print("pyserial is missing. Install it with:")
        print("    python -m pip install pyserial")
        return 1

    # Before touching any port: another bridge already running is the most
    # common reason nothing works - it holds both boards' ports, and on
    # Windows this one could even bind 9000 alongside it and split the page.
    if bridge_running(args.http):
        print("A Kairo bridge is already running on port %d." % args.http)
        print("Close it first (Ctrl-C in its window), or if you cannot find it:")
        print("  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bridge.py*' }"
              " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        return 1

    device = Device(port=args.port, enabled=not args.no_serial,
                    logfile=args.log)
    Handler.device = device
    device.start()
    if device.enabled and serial is not None:
        threading.Thread(target=vision.run, args=(device,), daemon=True).start()
    threading.Thread(target=vision.watch, args=(device,), daemon=True).start()

    # Binding 0.0.0.0 succeeds even when something else already holds
    # 127.0.0.1 on this port - and on Windows the specific bind wins, so
    # localhost would silently go to that other program while the printed
    # URL looks fine. Docker Desktop squats on 8000, which is why the
    # default moved to 9000. Check anyway and say so.
    squatter = socket.socket()
    squatter.settimeout(0.3)
    taken = squatter.connect_ex(("127.0.0.1", args.http)) == 0
    squatter.close()

    httpd = ThreadingHTTPServer((args.host, args.http), Handler)
    ip = lan_ip()
    print("")
    print("  Kairo bridge - Access Granted")
    print("  ---------------------------------------------")
    print("  carer       http://localhost:%d" % args.http)
    print("  patient     http://localhost:%d/patient" % args.http)
    print("  on the LAN  http://%s:%d   (add /patient for the tablet)"
          % (ip, args.http))
    print("  serial      %s" % (args.port or "auto-detect"))
    if args.log:
        print("  log         %s" % os.path.abspath(args.log))
    if args.no_serial:
        print("  hardware    disabled (--no-serial), UI runs in demo mode")
    print("  ---------------------------------------------")
    if taken:
        print("  !! something else already answers on localhost:%d" % args.http)
        print("  !! use the LAN address above, or restart with --http 9001")
    print("  Ctrl-C to stop")
    print("")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        device.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
