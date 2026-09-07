# The hardware layer. Everything that touches a pin lives here, and every
# piece of it is optional.
#
# WHY EVERYTHING IS OPTIONAL
# At a hackathon the box is half-built for most of the weekend. If a missing
# LCD or an unplugged servo threw an exception the whole agent would die and
# the website would go blank, which is exactly when you least want it to.
# So each peripheral is probed once at startup, and if it is not there the
# rest keeps running and reports it as absent. The website shows that state
# instead of pretending.
#
# PINS - the LCD, amplifier and first servo come from the verified table in
# ../README.md. The two extra servo pins do not, because that table was
# written when there was one servo; see SERVO_PINS below for why these two.
#
#   servo 1 / 2 / 3   GPIO21, GPIO38, GPIO39
#   LCD 1602 (I2C)    SDA 14 / SCL 42, address 0x27
#   I2S amplifier     BCK 2 / LCK 41 / DIN 48
#   PWM speaker       GPIO47 (through the NPN transistor) - alternative to I2S
#                     note: square waves only, so it cannot play a WAV
#
# READ THIS BEFORE PLUGGING SERVOS INTO THE 5V PIN: one SG90 pulls 500-700mA
# stalled, which on its own browns out the board and makes it fall off USB
# entirely. Three of them need their own 5V supply with a shared ground, and
# that is not optional. dispense() only ever drives one servo at a time,
# creeps a degree at a time, and releases the PWM afterwards - that keeps the
# peak down, but it does not make bad power survivable.

import time

# --- servos ------------------------------------------------------------
#
# One servo per tube. GPIO21 is the pin the single-servo prototype was proven
# on. GPIO38 and GPIO39 were picked because they are among the very few pins
# this module leaves genuinely free: the camera ribbon claims 4-18, the LCD
# has 14 and 42, the amplifier has 2/41/48, 26-37 are the Octal PSRAM, 19/20
# are USB, 43/44 are the UART console and 0/45/46 are strapping pins. 38/39
# are only spoken for by the SD card in Freenove's *Arduino* sketch, which
# this MicroPython build cannot reach anyway. GPIO40 is the spare if one of
# them turns out to be awkward.

SERVO_PINS = (21, 38, 39)

# Each tube's gate: where the arm sits shut, and where it swings to drop a
# pill. Calibrate per tube from the website (the Test sweep button on each
# tube card) and copy the numbers here so they survive a reflash.
# An SG90 travels 0-180 and nothing outside that range is sent to it.
SERVO_CLOSED = (10, 10, 10)
SERVO_OPEN = (100, 100, 100)

STEP_MS = 12      # delay between single-degree steps while creeping
DWELL_MS = 260    # pause at the open position, to let a pill actually fall
BACK_MS = 140     # pause after closing, before any second sweep

# --- other peripherals -------------------------------------------------

LCD_SDA, LCD_SCL, LCD_ADDR = 14, 42, 0x27

AUDIO_BACKEND = "i2s"        # "i2s" (amplifier), "pwm" (transistor), "none"
VOLUME = 0.35                # changed live from the carer site

# THE REMINDER SOUNDS
# All of these are deliberately rising and resolved rather than alarm-like:
# one of them plays in somebody's home several times a day, every day, and a
# sound that reads as an emergency the first week reads as something to
# ignore by the third. Chosen from the carer site; the choice is stored on
# the board so it survives a power cut.
#
# A WAV entry needs a 16-bit mono 16kHz file on the board (see
# audio/convert_audio.py) and falls back to "gentle" if the file is missing.

CHIME_WAVS = {
    "jingle": "/jingle5.wav",
}

CHIME_TUNES = {
    "gentle": (("G4", 160), ("C5", 160), ("E5", 160), ("G5", 320),
               ("REST", 120), ("E5", 160), ("G5", 400)),
    "chirp":  (("E5", 90), ("REST", 70), ("G5", 90), ("REST", 70),
               ("C5", 220)),
    "bells":  (("C5", 260), ("REST", 60), ("G4", 260), ("REST", 60),
               ("C5", 200), ("E5", 200), ("G5", 420)),
    "urgent": (("A5", 130), ("REST", 50), ("A5", 130), ("REST", 50),
               ("A5", 130), ("REST", 50), ("F5", 380)),
}

CHIME_NAME = "jingle"        # which of the above plays for a due dose

# Not selectable: this is the "something is wrong" sound - a missed dose
# reminder or an empty tube. It stays distinct from the dose chime on
# purpose, so the two never get confused.
ALERT_MELODY = (("E5", 120), ("REST", 60), ("E5", 120), ("REST", 60),
                ("E5", 120), ("C5", 300))

_NOTES = {"C4": 262, "D4": 294, "E4": 330, "F4": 349, "G4": 392,
          "A4": 440, "B4": 494, "C5": 523, "D5": 587, "E5": 659,
          "F5": 698, "G5": 784, "A5": 880, "REST": 0}


# --- state -------------------------------------------------------------

# present["servo"] is True if ANY servo answered; servos[] says which.
present = {"servo": False, "lcd": False, "audio": False}
detail = {"servo": "", "lcd": "", "audio": ""}
servos = [False, False, False]

_servo = [None, None, None]
_at = [0, 0, 0]                  # last commanded angle, per servo
_lcd = None
_lcd_cache = [None, None]
_pwm_speaker = None
_wav_ok = {}          # chime name -> is its WAV actually on the board


def _note_hz(name):
    if isinstance(name, str):
        return _NOTES.get(name, 0)
    return int(name)


def _clamp_angle(a):
    return max(0, min(180, int(a)))


# --- probe -------------------------------------------------------------

def probe():
    """Look for each peripheral once. Safe to call again."""
    global _lcd, _have_wav, _pwm_speaker

    from_servo = []
    try:
        from servo import Servo
    except Exception as e:
        Servo = None
        detail["servo"] = repr(e)

    for i, pin in enumerate(SERVO_PINS):
        if Servo is None:
            servos[i] = False
            continue
        try:
            # Claim the pin to prove it takes PWM, then drop it again. Note
            # what this does NOT tell us: whether a servo is actually wired
            # to it. Nothing on the board can sense that - the Test gate
            # button on the website is the only real check.
            Servo(pin).release()
            _servo[i] = None
            _at[i] = SERVO_CLOSED[i]
            servos[i] = True
            from_servo.append("T%d:%d" % (i + 1, pin))
        except Exception as e:
            _servo[i] = None
            servos[i] = False
            from_servo.append("T%d:%d?" % (i + 1, pin))
            detail["servo"] = repr(e)
    present["servo"] = any(servos)
    if any(servos):
        detail["servo"] = " ".join(from_servo)

    try:
        from machine import I2C, Pin
        from I2C_LCD import I2cLcd
        i2c = I2C(scl=Pin(LCD_SCL), sda=Pin(LCD_SDA), freq=100000)
        found = i2c.scan()
        if not found:
            raise OSError("nothing on SDA%d/SCL%d" % (LCD_SDA, LCD_SCL))
        addr = LCD_ADDR if LCD_ADDR in found else found[0]
        _lcd = I2cLcd(i2c, addr, 2, 16)
        _lcd.clear()
        _lcd_cache[0] = _lcd_cache[1] = None
        present["lcd"] = True
        detail["lcd"] = "0x%02x" % addr
    except Exception as e:
        present["lcd"] = False
        detail["lcd"] = repr(e)

    if AUDIO_BACKEND == "i2s":
        try:
            import audio_amp
            audio_amp.VOLUME = VOLUME
            audio_amp.begin()
            audio_amp.end()       # prove the peripheral claims, then let go
            present["audio"] = True
            detail["audio"] = "I2S 2/41/48"
            # Check every WAV chime once, so a missing file becomes a
            # greyed-out option on the site rather than a failure at 08:00.
            for name, path in CHIME_WAVS.items():
                try:
                    open(path, "rb").close()
                    _wav_ok[name] = True
                except Exception:
                    _wav_ok[name] = False
        except Exception as e:
            present["audio"] = False
            detail["audio"] = repr(e)
    elif AUDIO_BACKEND == "pwm":
        try:
            from machine import Pin, PWM
            _pwm_speaker = PWM(Pin(47))
            _pwm_speaker.duty_u16(0)
            present["audio"] = True
            detail["audio"] = "PWM GPIO47"
        except Exception as e:
            present["audio"] = False
            detail["audio"] = repr(e)
    else:
        present["audio"] = False
        detail["audio"] = "disabled"

    return present


# --- LCD ---------------------------------------------------------------

def lcd_write(line0, line1):
    """Write two 16-character lines, skipping any line that has not changed.

    Not clearing between writes is what keeps the display steady - clearing
    first is what makes an HD44780 flicker. Both lines are assumed to be
    exactly 16 characters already (lcdview.render guarantees that), so a
    shorter new string cannot leave stale characters behind.
    """
    if not present["lcd"] or _lcd is None:
        return
    try:
        if _lcd_cache[0] != line0:
            _lcd.move_to(0, 0)
            _lcd.putstr(line0)
            _lcd_cache[0] = line0
        if _lcd_cache[1] != line1:
            _lcd.move_to(0, 1)
            _lcd.putstr(line1)
            _lcd_cache[1] = line1
    except Exception as e:
        # A yanked I2C wire should not take the scheduler with it.
        present["lcd"] = False
        detail["lcd"] = repr(e)


# --- servos ------------------------------------------------------------

def _acquire(i):
    """Create a fresh PWM for one servo.

    Servo.release() calls PWM.deinit(), and a de-initialised PWM rejects
    every later duty_ns() write - so the object cannot be kept across a
    release. Since releasing between doses is exactly what keeps the peak
    current down, the PWM is built per movement instead of held open.
    """
    from servo import Servo
    s = Servo(SERVO_PINS[i])
    _servo[i] = s
    return s


def _release(i):
    """Let the servo go slack and drop the dead PWM object."""
    s = _servo[i]
    _servo[i] = None
    if s is not None:
        try:
            s.release()
        except Exception:
            pass


def _creep(i, target):
    """Walk one degree at a time from wherever this servo last was.

    Fast travel is what stalls an SG90, and a stalled SG90 is what browns
    out the board. Creeping costs a few hundred milliseconds and buys a
    dispense that does not crash the box.
    """
    s = _servo[i]
    target = _clamp_angle(target)
    current = _clamp_angle(_at[i])
    step = 1 if target >= current else -1
    for a in range(current, target + step, step):
        s.angle(a)
        time.sleep_ms(STEP_MS)
    _at[i] = target
    return target


def _sweep_once(i):
    """Open the gate, wait for a pill to fall, close it again."""
    _creep(i, SERVO_OPEN[i])
    time.sleep_ms(DWELL_MS)
    _creep(i, SERVO_CLOSED[i])
    time.sleep_ms(BACK_MS)


def dispense(tube, pills=1):
    """Drop `pills` pills from one tube, then let the servo go slack.

    One sweep of the gate per pill: a two-pill dose opens and closes twice
    rather than holding the gate open twice as long, because holding it open
    is how you get three pills out of a two-pill dose.
    """
    i = tube % len(SERVO_PINS)
    if not servos[i]:
        return False
    try:
        _acquire(i)
        for _ in range(max(1, min(9, int(pills)))):
            _sweep_once(i)
        return True
    except Exception as e:
        servos[i] = False
        present["servo"] = any(servos)
        detail["servo"] = repr(e)
        return False
    finally:
        _release(i)


def sweep(tube):
    """One gate cycle, for checking the mechanism. Dispenses nothing on paper.

    Same movement as a real dose, but the caller does not touch the pill
    count - this is the button on each tube card in the website.
    """
    return dispense(tube, pills=1)


def servo_to(tube, angle):
    """Hold one servo at an angle, for calibrating CLOSED and OPEN."""
    i = (tube or 0) % len(SERVO_PINS)
    if not servos[i]:
        return False
    try:
        _acquire(i)
        _creep(i, angle)
        time.sleep_ms(200)
        return True
    except Exception as e:
        detail["servo"] = repr(e)
        return False
    finally:
        _release(i)


def set_angles(closed=None, open_=None):
    """Replace the calibration from the website."""
    global SERVO_CLOSED, SERVO_OPEN
    if closed and len(closed) == 3:
        SERVO_CLOSED = tuple(_clamp_angle(a) for a in closed)
    if open_ and len(open_) == 3:
        SERVO_OPEN = tuple(_clamp_angle(a) for a in open_)
    return {"closed": list(SERVO_CLOSED), "open": list(SERVO_OPEN)}


# --- sound -------------------------------------------------------------

def _pwm_melody(sequence):
    for name, ms in sequence:
        hz = _note_hz(name)
        if hz <= 0:
            _pwm_speaker.duty_u16(0)
        else:
            _pwm_speaker.freq(hz)
            _pwm_speaker.duty_u16(32768)
        time.sleep_ms(ms)
        _pwm_speaker.duty_u16(0)
        time.sleep_ms(30)


def chime_names():
    """Everything the carer site can offer, and whether each is playable.

    A WAV whose file is not on the board is reported as unavailable rather
    than silently swapped, so the site can grey it out and say why.
    """
    out = []
    for name in CHIME_WAVS:
        out.append({"name": name, "kind": "wav",
                    "ok": bool(_wav_ok.get(name)) or AUDIO_BACKEND == "pwm"})
    for name in CHIME_TUNES:
        out.append({"name": name, "kind": "tune", "ok": True})
    return out


def set_chime(name):
    """Choose the dose sound. Unknown names are ignored, not guessed at."""
    global CHIME_NAME
    if name in CHIME_WAVS or name in CHIME_TUNES:
        CHIME_NAME = name
    return CHIME_NAME


def chime(kind="dose", name=None):
    """Play a sound. Blocks for as long as it lasts.

    Blocking is fine here: nothing else is time-critical at the instant a
    dose fires, and MicroPython I2S writes are the simplest correct way to
    get audio out. The amplifier is claimed and released around each play so
    the I2S pins are free the rest of the time. The caller publishes state
    before calling this, because a five-second WAV is five seconds of
    silence on the serial link.

    kind "dose" plays the carer's chosen chime (or `name`, for previewing a
    different one from the site without committing to it). Anything else
    plays the fixed alert tune.
    """
    if not present["audio"]:
        return False

    want = (name or CHIME_NAME) if kind == "dose" else "urgent"
    if kind != "dose":
        tune, wav = ALERT_MELODY, None
    elif want in CHIME_WAVS and (_wav_ok.get(want) or AUDIO_BACKEND == "pwm"):
        # A PWM speaker cannot play a WAV at all, so it gets the fallback
        # tune even when the file is present.
        wav = CHIME_WAVS[want] if AUDIO_BACKEND != "pwm" else None
        tune = CHIME_TUNES["gentle"]
    else:
        tune, wav = CHIME_TUNES.get(want, CHIME_TUNES["gentle"]), None

    try:
        if AUDIO_BACKEND == "pwm":
            _pwm_melody(tune)
            return True
        import audio_amp
        audio_amp.VOLUME = VOLUME
        with audio_amp.open_amp():
            if wav:
                audio_amp.play_wav(wav)
            else:
                audio_amp.melody(tune)
        return True
    except Exception as e:
        detail["audio"] = repr(e)
        return False


def shutdown():
    """Leave every pin in a safe state."""
    for i in range(len(SERVO_PINS)):
        _release(i)
    try:
        if _pwm_speaker is not None:
            _pwm_speaker.duty_u16(0)
            _pwm_speaker.deinit()
    except Exception:
        pass
    try:
        import audio_amp
        audio_amp.end()
    except Exception:
        pass
