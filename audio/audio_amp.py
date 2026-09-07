"""Reusable audio driver for the Freenove ES7148 + PAM8403 I2S amplifier.

Import this from any script rather than copying tone code around:

    import audio_amp
    audio_amp.begin()
    audio_amp.tone(1000, 200)
    audio_amp.melody([("E5", 200), ("C5", 200), ("G5", 400)])
    audio_amp.play_wav("/music.wav")
    audio_amp.end()

or let the context manager handle begin/end:

    import audio_amp
    with audio_amp.open_amp():
        audio_amp.tone(440, 500)

WIRING - these pins avoid the camera ribbon, the LCD (14/42) and the servo (21),
so audio can run at the same time as all of them:

    VCC -> 5V        BCK -> GPIO2    (bit clock)
    GND -> GND       LCK -> GPIO41   (word select)
    SCK -> unused    DIN -> GPIO48   (data)
    speaker (8 ohm 2W) -> module's L+ / L- terminals

The module generates its own master clock, so its SCK pin needs no connection.
That matters because MicroPython's I2S has no `mck=` parameter - if the DAC had
required an external MCLK, none of this would work without an Arduino reflash.

WAV FILES must be 16-bit signed mono. Anything else is rejected rather than
played as noise. Use convert_audio.py on the PC to produce a suitable file -
MicroPython cannot decode MP3, so conversion happens off-board.
"""

from machine import I2S, Pin
import math
import struct

BCK, LCK, DIN = 2, 41, 48
RATE = 16000
VOLUME = 0.05  # 0.0 - 1.0; 0.9 is painfully loud, 0.15 is comfortable

NOTES = {
    "C4": 262, "D4": 294, "E4": 330, "F4": 349, "G4": 392, "A4": 440, "B4": 494,
    "C5": 523, "D5": 587, "E5": 659, "F5": 698, "G5": 784, "A5": 880,
    "REST": 0,
}

_audio = None


def begin(rate=RATE):
    """Claim the I2S peripheral. Safe to call twice."""
    global _audio, RATE
    if _audio is not None:
        return _audio
    RATE = rate
    _audio = I2S(0, sck=Pin(BCK), ws=Pin(LCK), sd=Pin(DIN),
                 mode=I2S.TX, bits=16, format=I2S.MONO, rate=rate, ibuf=20000)
    return _audio


def end():
    """Release the I2S peripheral. Always call this, or the pins stay claimed."""
    global _audio
    if _audio is not None:
        _audio.deinit()
        _audio = None


class open_amp:
    """Context manager: begin() on enter, end() on exit even if something raises."""

    def __init__(self, rate=RATE):
        self._rate = rate

    def __enter__(self):
        return begin(self._rate)

    def __exit__(self, *exc):
        end()
        return False


def _require():
    if _audio is None:
        raise RuntimeError("call audio_amp.begin() first")
    return _audio


def tone(freq, ms, vol=None):
    """Play one frequency for ms milliseconds. freq<=0 plays silence."""
    a = _require()
    vol = VOLUME if vol is None else vol
    n = int(RATE * ms / 1000)
    if freq <= 0:
        a.write(bytearray(n * 2))
        return
    # Build one cycle then repeat it, so long tones do not need a sample-by-
    # sample loop over the whole duration.
    period = max(1, int(RATE / freq))
    cycle = bytearray()
    for i in range(period):
        v = int(math.sin(2 * math.pi * i / period) * vol * 32767)
        cycle += struct.pack("<h", v)
    reps = max(1, n // period)
    # bytes supports repetition in MicroPython; bytearray does not.
    a.write(bytes(cycle) * reps)


def melody(sequence, gap_ms=40, vol=None):
    """Play [(note_name_or_hz, ms), ...]. Names come from NOTES."""
    import time
    for name, ms in sequence:
        freq = NOTES.get(name, name) if isinstance(name, str) else name
        tone(freq or 0, ms, vol)
        if gap_ms:
            time.sleep_ms(gap_ms)


def play_wav(path, vol=None, chunk=4096):
    """Stream a 16-bit mono WAV file to the amp.

    Parses the RIFF chunks properly rather than assuming a 44-byte header,
    because converters vary in what they put before the data chunk.
    """
    a = _require()
    vol = VOLUME if vol is None else vol

    with open(path, "rb") as f:
        if f.read(4) != b"RIFF":
            raise ValueError("not a RIFF/WAV file: " + path)
        f.read(4)
        if f.read(4) != b"WAVE":
            raise ValueError("not a WAVE file: " + path)

        channels = bits = 0
        rate = None
        data_len = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError("no data chunk found in " + path)
            cid, size = struct.unpack("<4sI", hdr)
            if cid == b"fmt ":
                fmt = f.read(size)
                channels = struct.unpack_from("<H", fmt, 2)[0]
                rate = struct.unpack_from("<I", fmt, 4)[0]
                bits = struct.unpack_from("<H", fmt, 14)[0]
            elif cid == b"data":
                data_len = size
                break
            else:
                f.seek(size, 1)      # skip LIST/fact/etc.

        if channels != 1 or bits != 16:
            raise ValueError("need 16-bit mono, got %d-bit %dch - reconvert it"
                             % (bits, channels))
        if rate != RATE:
            print("warning: file is %d Hz but I2S is %d Hz - pitch will be off"
                  % (rate, RATE))

        print("playing %s (%.1f s)" % (path, data_len / (rate * 2)))
        remaining = data_len
        buf = bytearray(chunk)
        mv = memoryview(buf)
        while remaining > 0:
            n = f.readinto(mv[: min(chunk, remaining)])
            if not n:
                break
            remaining -= n
            if vol >= 0.999:
                a.write(mv[:n])
            else:
                # Scale in place; 16-bit little-endian signed samples.
                for i in range(0, n - 1, 2):
                    s = struct.unpack_from("<h", buf, i)[0]
                    struct.pack_into("<h", buf, i, int(s * vol))
                a.write(mv[:n])
