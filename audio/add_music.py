"""Convert an audio file and put it on the board in one step.

    python audio/add_music.py "C:\\path\\to\\song.mp3" [seconds] [port]

Does the two things that are easy to forget separately: converts to the 16 kHz
16-bit mono WAV that play_wav() requires, then copies it into the board's flash.
The board cannot read files from the PC, so the copy is not optional.

Prints the exact play_wav() path to use when it finishes, and warns if the clip
will not fit in the free space reported by the board.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from convert_audio import convert, RATE, SAMPWIDTH

DEFAULT_PORT = "COM4"


def board_free_kb(port):
    """Free flash on the board in KB, or None if it cannot be read."""
    code = "import os; s=os.statvfs('/'); print(int(s[0]*s[3]/1024))"
    try:
        out = subprocess.run(
            [sys.executable, "-m", "mpremote", "connect", port, "exec", code],
            capture_output=True, text=True, timeout=30,
        )
        return int(out.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    src = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    port = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_PORT

    if not os.path.exists(src):
        print("no such file:", src)
        return 1

    here = os.path.dirname(os.path.abspath(__file__))
    name = os.path.splitext(os.path.basename(src))[0]
    # Board filenames are flat and awkward with spaces; keep them simple.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).lower()[:24]
    wav = os.path.join(here, safe + ".wav")

    expected_kb = seconds * RATE * SAMPWIDTH / 1024
    free = board_free_kb(port)
    if free is not None:
        print("board has %d KB free; this clip needs about %d KB" % (free, expected_kb))
        if expected_kb > free - 200:      # leave a little headroom
            print("WILL NOT FIT - ask for fewer seconds, or delete an old clip:")
            print("  python -m mpremote connect %s fs rm :oldclip.wav" % port)
            return 1
    else:
        print("could not read free space from %s - continuing anyway" % port)

    convert(src, wav, seconds)

    print("copying to board...")
    r = subprocess.run(
        [sys.executable, "-m", "mpremote", "connect", port,
         "fs", "cp", wav, ":" + safe + ".wav"]
    )
    if r.returncode != 0:
        print("copy failed. If the board just vanished from USB, unplug the servo -")
        print("it browns out the board and kills transfers mid-write.")
        return r.returncode

    print("\ndone. play it with:")
    print('    audio_amp.play_wav("/%s.wav")' % safe)
    print("or straight from the PC:")
    print('    python -m mpremote connect %s exec "import audio_amp'
          '\\nwith audio_amp.open_amp(): audio_amp.play_wav(\'/%s.wav\')"'
          % (port, safe))
    return 0


if __name__ == "__main__":
    sys.exit(main())
