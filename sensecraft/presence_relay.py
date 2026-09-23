"""SenseCraft camera - the backup presence sensor.

    python sensecraft/presence_relay.py --selftest     # parser check, no hardware

The dispenser answers "is somebody at the box" with the HC-SR04 wired to it.
The XIAO ESP32-S3 Sense backs it up with a SenseCraft model - Person
Classification, or any person detection model - and doubles as the carer's
check-in camera. Pick the model on the board in SenseCraft's own web page
(Models -> XIAO ESP32S3 Sense -> Select Model), then CLOSE that page - it
holds the USB port while it is open.

There is nothing to run for it. site/bridge.py finds the XIAO on USB, starts
the model, tells the box when somebody is there, and serves the frames to
the Check-in page. This file is the SenseCraft half of that: the command
that starts it, and the reading of what comes back.

WHAT THE BOARD SENDS
One JSON line per inference (SSCMA-Micro 2.x, the sscma_server_at firmware):

    {"type":1,"name":"INVOKE","code":0,"data":{"count":7,"classes":[[0.91,1]]}}
    {"type":1,"name":"INVOKE","code":0,"data":{"count":7,"boxes":[[x,y,w,h,87,0]]}}

"classes" rows are [score, class], from a classification model; "boxes" rows
are [x, y, w, h, score, class], from a detection model. Scores are 0-1 on
this firmware and 0-100 on the older 1.x one; both are read as 0-100 here.
"""

import base64
import binascii
import json
import sys

# Stop any run already going, then N_TIMES,RESULTS_ONLY: run forever, WITH
# the JPEG in every line - those frames are the Check-in camera. (The 2.x
# server on this XIAO takes these two arguments; the old 1.x firmware took
# three.) The frame rate is the model's pace, about 2 fps: every frame is one
# inference. The picture is 240x240 and no command changes that: SenseCraft's
# ESP32 camera driver has that one preset compiled in (sensor 0).
INVOKE = b"AT+BREAK\rAT+INVOKE=-1,0\r"
# The same camera with the model off: pictures only, as fast as the board can
# send them. The bridge uses this while somebody watches Check-in.
SAMPLE = b"AT+BREAK\rAT+SAMPLE=-1\r"
BAUD = 921600          # SenseCraft's default; the XIAO's USB serial ignores it

# SenseCraft's Person Classification (MobileNetV2 on Visual Wake Words) has
# two classes: 0 "Not a person", 1 "Person". A classifier always names SOME
# class, so "any class" would count an empty room as somebody.
PERSON_CLASS = 1
CONFIDENCE = 70        # % sure of a person before a pill drops. 40 fired on an
                       # empty room: two-class models sit near 50/50 on clutter.


def parse_result(line):
    """(kind, [(score, class), ...]) from one INVOKE line, or None.

    kind is "classes" or "boxes"; score is 0-100. None means the line is not
    a result at all - boot chatter, a command's reply, half a line.
    """
    line = (line or "").strip()
    if not line.startswith("{"):
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    data = msg.get("data") if isinstance(msg, dict) else None
    if not isinstance(data, dict):
        return None
    for kind, at in (("classes", 0), ("boxes", 4)):
        rows = data.get(kind)
        if not isinstance(rows, list):
            continue
        out = []
        for row in rows:
            try:
                score, cls = row[at], int(row[at + 1])
                score = float(score) * (100 if isinstance(score, float) and score <= 1 else 1)
            except (TypeError, ValueError, IndexError, KeyError):
                continue            # a junk row is skipped, not fatal
            out.append((min(100.0, score), cls))
        return kind, out
    return None


def person(kind, rows, person_class=None, confidence=CONFIDENCE):
    """Is somebody there? (near, person_score_or_None, "what it saw")."""
    if person_class is None and kind == "classes":
        person_class = PERSON_CLASS
    scores = [s for s, c in rows if person_class is None or c == person_class]
    best = max(scores) if scores else None
    top = max(rows) if rows else None
    seen = "class %d at %d%%" % (top[1], top[0]) if top else "nobody in frame"
    return best is not None and best >= confidence, best, seen


def jpeg(line):
    """The camera frame carried in an INVOKE line, as JPEG bytes, or None."""
    try:
        data = json.loads(line.strip()).get("data")
        return base64.b64decode(data["image"]) if isinstance(data, dict) and data.get("image") else None
    except (ValueError, TypeError, AttributeError, binascii.Error):
        return None


def selftest():
    """Real SenseCraft output shapes through the parser. No hardware needed."""
    # Person Classification on the 2.x firmware: [score 0-1, class].
    line = json.dumps({"type": 1, "name": "INVOKE", "code": 0,
                       "data": {"count": 7, "classes": [[0.91, 1]]}})
    assert parse_result(line) == ("classes", [(91.0, 1)])
    assert person(*parse_result(line)) == (True, 91.0, "class 1 at 91%")
    # The same model sure it is NOT a person: class 0 must not count.
    kind, rows = parse_result('{"type":1,"name":"INVOKE","data":{"classes":[[0.88,0]]}}')
    assert person(kind, rows) == (False, None, "class 0 at 88%")
    # A person, but not sure enough.
    assert person("classes", [(53.0, 1)])[0] is False      # an empty room scored this
    assert person("classes", [(70.0, 1)])[0] is True       # 70% is enough
    # With the JPEG in the line, as INVOKE asks for it.
    assert parse_result('{"type":1,"name":"INVOKE","data":{"count":2,"image":"/9j/4AAQ",'
                        '"classes":[[0.6,1]]}}') == ("classes", [(60.0, 1)])
    # Detection on the 1.x firmware: integer scores, any class by default.
    kind, rows = parse_result('{"type":1,"name":"INVOKE","data":{"boxes":'
                              '[[120,110,90,160,84,0],[1,2,3,4,30,2]]}}')
    assert (kind, rows) == ("boxes", [(84.0, 0), (30.0, 2)])
    assert person(kind, rows) == (True, 84.0, "class 0 at 84%")
    assert person(kind, rows, 2, 50) == (False, 30.0, "class 0 at 84%")
    # Nothing in frame is an empty list - a reading, not a missing one.
    assert parse_result('{"type":1,"name":"INVOKE","data":{"boxes":[]}}') == ("boxes", [])
    assert person("boxes", []) == (False, None, "nobody in frame")
    # Not results: the boot sequence, the command's own reply, noise.
    assert parse_result('{"type":0,"name":"INIT@MODEL","code":0,"data":{"model":{"id":1}}}') is None
    assert parse_result('{"type":0,"name":"INVOKE","code":0,"data":{"sensors":[]}}') is None
    assert parse_result("I (381) esp_image: segment 4") is None
    assert parse_result('{"type":1,"name":"INVOKE","data":{"count":3,"image":"/9j/4A') is None
    assert parse_result("[1, 2]") is None and parse_result("") is None
    # Junk rows are skipped rather than crashing the reader.
    assert parse_result('{"data":{"classes":[[0.7],"x",[0.9,1]]}}') == ("classes", [(90.0, 1)])
    # Lines start with a carriage return on this firmware, and the frame
    # rides along with the result.
    line = '\r{"type":1,"name":"INVOKE","data":{"image":"/9j/4A==","classes":[[62,1]]}}'
    assert parse_result(line) == ("classes", [(62.0, 1)])
    assert jpeg(line) == b"\xff\xd8\xff\xe0" and jpeg('{"data":{"classes":[]}}') is None
    # camera/XiaoCam's own line: both classes, and its WiFi address.
    line = '{"type":1,"name":"INVOKE","code":0,"data":{"classes":[[0.09,0],[0.91,1]],"ip":"10.1.2.3"}}'
    assert person(*parse_result(line)) == (True, 91.0, "class 1 at 91%")
    line = '{"type":1,"name":"INVOKE","code":0,"data":{"classes":[[1.00,0],[0.00,1]],"ip":""}}'
    assert person(*parse_result(line)) == (False, 0.0, "class 0 at 100%")
    # SAMPLE (model paused for check-in): a frame, but no reading.
    line = '\r{"type":1,"name":"SAMPLE","code":0,"data":{"count":8,"image":"/9j/4A=="}}'
    assert jpeg(line) == b"\xff\xd8\xff\xe0" and parse_result(line) is None
    print("sensecraft selftest: all checks passed")
    return 0


if __name__ == "__main__":
    if "--selftest" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    sys.exit(selftest())
