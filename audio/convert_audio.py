"""Convert an MP3 (or any audio file) to a WAV the ESP32 can stream to I2S.

Runs on the PC, not the board. MicroPython has no MP3 decoder, so the decode
has to happen here; the board only ever sees raw PCM.

Target format is what the amp test confirmed working:
    16 kHz, 16-bit signed, mono   ->  32 KB per second of audio

Usage:
    python convert_audio.py "path/to/song.mp3" out.wav [seconds]

`seconds` trims the clip so it fits in the board's ~5.8 MB of free flash.
Default 30 s (about 960 KB).
"""

import sys, wave, miniaudio

RATE = 16000
CHANNELS = 1
SAMPWIDTH = 2          # 16-bit


def convert(src, dst, seconds=30):
    print("decoding", src)
    decoded = miniaudio.decode_file(
        src,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=CHANNELS,
        sample_rate=RATE,
    )
    samples = decoded.samples
    total_s = len(samples) / RATE
    print("  source: %.1f s at %d Hz mono" % (total_s, RATE))

    if seconds and total_s > seconds:
        samples = samples[: int(RATE * seconds)]
        print("  trimmed to %.1f s" % (len(samples) / RATE))

    with wave.open(dst, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())

    kb = len(samples) * SAMPWIDTH / 1024
    print("wrote %s  (%.0f KB, %.1f s)" % (dst, kb, len(samples) / RATE))
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 30
    convert(sys.argv[1], sys.argv[2], secs)
