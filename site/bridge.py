"""Kairo bridge - the laptop half of the demo.

    python site/bridge.py                    # find the board, serve the site
    python site/bridge.py --port COM4        # if you already know the port
    python site/bridge.py --no-serial        # UI only, no hardware attached

It does two jobs:

  1. holds ONE persistent USB serial connection to the dispenser and keeps
     it alive - re-detecting the port, re-opening it and re-syncing the clock
     on its own, so nothing has to be unplugged or reset between commands;
  2. serves site/index.html and a small JSON API over HTTP, with CORS wide
     open so the page also works when opened straight off disk or from a
     phone on the same network.

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

Only the Python standard library plus pyserial, which is already installed
here as a dependency of mpremote.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
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


class Device:
    """The serial half. One thread owns the port; everyone else uses send()."""

    def __init__(self, port=None, enabled=True):
        self.wanted_port = port
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
        # Most likely first, so auto-detect picks well.
        found.sort(key=lambda item: (not item["likely"], item["port"]))
        return found

    def _pick_port(self):
        if self.wanted_port:
            return self.wanted_port
        options = self.candidates()
        return options[0]["port"] if options else None

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
            self.error = ("%s answered, but no Kairo agent. Run "
                          "python dispenser/deploy.py" % port)
            self.log("bridge: %s" % self.error)
            self._close()
            return False

        self.error = ""          # cleared only now that an agent has answered
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
        self.console.append({"t": time.strftime("%H:%M:%S"), "line": text})
        print("  " + text, flush=True)

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
        if path == "/api/ports":
            return self._json({"ports": Device.candidates(),
                               "current": self.device.port,
                               "pyserial": serial is not None})
        if path == "/api/hello":
            return self._json({"ok": True, "service": "kairo-bridge"})
        if path.startswith("/api/"):
            return self._json({"ok": False, "error": "no such endpoint"}, 404)

        # Two front doors, one per audience.
        if path in ("/", "/carer", "/dashboard", "/camera", "/settings"):
            return self._file("index.html")
        if path in ("/patient", "/kiosk"):
            return self._file("patient.html")

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

        if path == "/api/port":
            self.device.use_port(body.get("port"))
            return self._json({"ok": True, "port": body.get("port")})

        return self._json({"ok": False, "error": "no such endpoint"}, 404)


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
    ap.add_argument("--http", type=int, default=8000, help="HTTP port")
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address; 0.0.0.0 lets phones on the LAN in")
    ap.add_argument("--no-serial", action="store_true",
                    help="serve the UI without touching any hardware")
    args = ap.parse_args()

    if serial is None and not args.no_serial:
        print("pyserial is missing. Install it with:")
        print("    python -m pip install pyserial")
        return 1

    device = Device(port=args.port, enabled=not args.no_serial)
    Handler.device = device
    device.start()

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
    if args.no_serial:
        print("  hardware    disabled (--no-serial), UI runs in demo mode")
    print("  ---------------------------------------------")
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
