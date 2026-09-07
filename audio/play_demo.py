"""Demo of the audio_amp module: tones, a melody, then a WAV file.

Shows the two ways to use the module - tones generated on the fly, and PCM
streamed from a file in flash.

Needs the module and a clip on the board first:
    python -m mpremote connect COM4 fs cp audio/audio_amp.py :audio_amp.py
    python -m mpremote connect COM4 fs cp audio/jingle5.wav :jingle.wav

Run:
    python -m mpremote connect COM4 run audio/play_demo.py
"""

import audio_amp

with audio_amp.open_amp():
    print("volume is", audio_amp.VOLUME)

    print("two beeps")
    audio_amp.tone(1000, 200)
    audio_amp.tone(0, 150)          # brief silence
    audio_amp.tone(1000, 200)

    print("rising scale")
    audio_amp.melody([(n, 220) for n in
                      ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5")])

    print("melody")
    audio_amp.melody([("E5", 200), ("E5", 200), ("REST", 120), ("E5", 200),
                      ("REST", 120), ("C5", 200), ("E5", 200), ("G5", 400),
                      ("REST", 200), ("G4", 400)])

    print("wav file")
    audio_amp.play_wav("/jingle.wav")

print("done - I2S released")
