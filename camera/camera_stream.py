# Live camera preview in a browser - nothing is photographed or recorded.
#
# The GC0308 sensor has no hardware JPEG encoder (PixelFormat.JPEG fails with
# ESP_ERR_NOT_SUPPORTED), so raw frames are sent and converted to RGBA in the
# browser onto a <canvas>. Frames are streamed and discarded.
#
# Needs wifi_config.py on the board with your SSID and PASSWORD.
#
# Run:  python -m mpremote connect COM3 run camera_stream.py
# Stop: python -m mpremote connect COM3 soft-reset

import gc
import network
import socket
import time
from camera import Camera, FrameSize, PixelFormat

# Measured sensor-side capture rates at 10MHz XCLK:
#   QQVGA 160x120   9.5 fps    38KB/frame
#   QVGA  320x240  10.0 fps   154KB/frame
#   HVGA  480x320  12.8 fps   307KB/frame
#   VGA   640x480   4.7 fps   614KB/frame - frames arrive truncated, avoid
# The sensor is not the bottleneck; moving bytes over WiFi is.
FRAME_SIZE = FrameSize.QVGA
WIDTH, HEIGHT = 320, 240

# Grayscale is 1 byte per pixel instead of 2, so roughly double the frame
# rate at the same resolution. Set True to trade colour for smoothness.
GRAYSCALE = False

XCLK_FREQ = 10_000_000
PORT = 80

CRLF = "\r\n"

# Exact size of a good frame. The driver occasionally returns a short buffer
# (it logs "FB-SIZE: a != b"); sending one of those would leave the browser
# decoding a partial image, so they get skipped instead.
EXPECTED_BYTES = WIDTH * HEIGHT * (1 if GRAYSCALE else 2)


def connect_wifi(timeout_seconds=20):
    try:
        import wifi_config
    except ImportError:
        print("!! wifi_config.py is not on the board.")
        return None

    ssid = getattr(wifi_config, "SSID", "")
    password = getattr(wifi_config, "PASSWORD", "")
    if not ssid or ssid == "YOUR_WIFI_NAME":
        print("!! wifi_config.py still has placeholder values - edit it first.")
        return None

    wlan = network.WLAN(network.STA_IF)
    # Cycle the interface first. Calling connect() while the driver holds a
    # stale association raises "Wifi Internal State Error".
    try:
        wlan.active(False)
        time.sleep_ms(300)
    except OSError:
        pass
    wlan.active(True)
    time.sleep_ms(500)

    if not wlan.isconnected():
        print("connecting to '%s' ..." % ssid)
        try:
            wlan.connect(ssid, password)
        except OSError as exc:
            print("!! connect failed:", exc)
            return None
        deadline = time.ticks_add(time.ticks_ms(), timeout_seconds * 1000)
        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("!! wifi timed out - check name/password, and 2.4GHz")
                return None
            time.sleep_ms(500)
    return wlan


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32-S3 Camera</title>
<style>
 body{margin:0;padding:1.5rem;background:#101215;color:#e6e6e6;
      font-family:system-ui,-apple-system,sans-serif;text-align:center}
 h1{font-size:1.15rem;margin:0 0 1rem;font-weight:600}
 canvas{width:__DISPW__px;max-width:100%;border-radius:8px;background:#000}
 #status{color:#8b9199;font-size:.85rem;margin-top:.9rem;
         font-variant-numeric:tabular-nums}
</style></head>
<body>
<h1>ESP32-S3 live camera</h1>
<canvas id="c" width="__W__" height="__H__"></canvas>
<div id="status">connecting ...</div>
<script>
const W = __W__, H = __H__, BPP = __BPP__;
const ctx = document.getElementById('c').getContext('2d');
const status = document.getElementById('status');
const rgba = new Uint8ClampedArray(W * H * 4);
const img = new ImageData(rgba, W, H);
let frames = 0, last = Date.now();

function next() {
  fetch('/frame', {cache: 'no-store'})
    .then(r => r.arrayBuffer())
    .then(buf => {
      if (!buf || buf.byteLength < W * H * BPP) { setTimeout(next, 100); return; }
      const b = new Uint8Array(buf);
      if (BPP === 1) {
        for (let i = 0, p = 0; i < W * H; i++, p += 4) {
          rgba[p] = rgba[p+1] = rgba[p+2] = b[i];
          rgba[p+3] = 255;
        }
      } else {
        // RGB565 arrives with the two bytes of each pixel swapped.
        for (let i = 0, p = 0; i < W * H * 2; i += 2, p += 4) {
          const v = (b[i+1] << 8) | b[i];
          rgba[p]   = ((v >> 11) & 0x1F) << 3;
          rgba[p+1] = ((v >> 5)  & 0x3F) << 2;
          rgba[p+2] = (v & 0x1F) << 3;
          rgba[p+3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
      frames++;
      const now = Date.now();
      if (now - last >= 1000) {
        status.textContent = W + 'x' + H + '  -  ' + frames + ' fps';
        frames = 0; last = now;
      }
      next();
    })
    .catch(() => { status.textContent = 'connection lost'; setTimeout(next, 1000); });
}
next();
</script>
</body></html>
"""


def build_page():
    return (PAGE.replace("__W__", str(WIDTH))
                .replace("__H__", str(HEIGHT))
                .replace("__DISPW__", str(WIDTH * 2))
                .replace("__BPP__", "1" if GRAYSCALE else "2"))


def headers(content_type, length):
    return ("HTTP/1.1 200 OK" + CRLF +
            "Content-Type: " + content_type + CRLF +
            "Content-Length: " + str(length) + CRLF +
            "Cache-Control: no-store" + CRLF +
            "Connection: keep-alive" + CRLF + CRLF)


def serve(cam, wlan):
    page = build_page()
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(socket.getaddrinfo("0.0.0.0", PORT)[0][-1])
    server.listen(3)
    print("open  http://%s/  in a browser on the same network"
          % wlan.ifconfig()[0])

    while True:
        conn = None
        try:
            conn, remote = server.accept()
            conn.settimeout(8)
            served = 0
            # Serve many requests down one socket. A fresh TCP connection per
            # frame costs a handshake each time, which was capping frame rate.
            while True:
                request = conn.recv(512)
                if not request:
                    break
                parts = request.split(b" ")
                path = parts[1] if len(parts) > 1 else b"/"

                if path.startswith(b"/frame"):
                    buf = cam.capture()
                    # sendall(), never send(). send() may write fewer bytes
                    # than asked; a short write leaves the promised
                    # Content-Length unfulfilled, so the browser reads the
                    # next response as pixel data and the picture turns into
                    # coloured noise. sendall() loops until everything is out.
                    if buf and len(buf) == EXPECTED_BYTES:
                        conn.sendall(headers("application/octet-stream",
                                             len(buf)))
                        conn.sendall(buf)
                    else:
                        conn.sendall("HTTP/1.1 503 Service Unavailable" + CRLF +
                                     "Content-Length: 0" + CRLF +
                                     "Connection: keep-alive" + CRLF + CRLF)
                else:
                    conn.sendall(headers("text/html; charset=utf-8", len(page)))
                    conn.sendall(page)
                    print("served page to", remote[0])

                served += 1
                if served % 40 == 0:
                    gc.collect()

        except OSError:
            # Browsers drop keep-alive sockets routinely; expected.
            pass
        finally:
            if conn is not None:
                conn.close()
            gc.collect()


wlan = connect_wifi()
if wlan is None:
    print("no wifi - cannot stream. Fix wifi_config.py and re-run.")
else:
    print("initialising camera ...")
    cam = Camera(frame_size=FRAME_SIZE,
                 pixel_format=(PixelFormat.GRAYSCALE if GRAYSCALE
                               else PixelFormat.RGB565),
                 xclk_freq=XCLK_FREQ,
                 init=False)
    cam.init()
    time.sleep_ms(300)
    test = cam.capture()
    expected = WIDTH * HEIGHT * (1 if GRAYSCALE else 2)
    print("camera ready - test frame %s bytes (expect %d)"
          % (len(test) if test else 0, expected))
    try:
        serve(cam, wlan)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        cam.deinit()
        print("camera released")
