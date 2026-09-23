# What the 1602 LCD shows, and nothing else.
#
# The screen is 16 characters by 2 lines. That is the hard constraint that
# shaped every string in here - there is no room for a sentence, so each
# state gets a fixed layout with the variable part budgeted in advance.
# render() always returns two strings of EXACTLY 16 characters, padded, so
# the caller can write them straight to the display without clearing first.
# Clearing before every write is what makes an HD44780 flicker.
#
# WHOSE SCREEN THIS IS
# This is the patient's screen, so it only ever says things the patient can
# act on: what to take, when, and whether the machine failed them. Stock
# management is the carer's job and it lives on the carer's site - there is
# deliberately no "tell your carer" screen here. Being nagged about running
# low is not a task you hand to somebody who needs a machine to remember
# their medicine for them.
#
# The same logic is duplicated in site/app/core.js (lcdRender) so the LCD
# panel on the carer site matches the hardware character for character. If
# you change a layout here, change it there too.

WIDTH = 16


def fit(text):
    """Pad or truncate to exactly WIDTH characters."""
    text = str(text)
    if len(text) > WIDTH:
        return text[:WIDTH]
    return text + " " * (WIDTH - len(text))


def short(text, n):
    """Squeeze a medicine name into n characters."""
    text = str(text)
    return text[:n] if len(text) > n else text


def human_gap(seconds):
    """Time remaining, in at most 7 characters: '2h 14m', '43m', '<1m'."""
    if seconds is None:
        return "--"
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "<1m"
    minutes = seconds // 60
    if minutes < 60:
        return "%dm" % minutes
    hours = minutes // 60
    if hours < 24:
        return "%dh %02dm" % (hours, minutes % 60)
    return "%dd %02dh" % (hours // 24, hours % 24)


def render(view):
    """Turn a small dict of facts into two 16-character lines.

    view keys:
      clock_set   bool
      mode        'idle' | 'dispensing' | 'due' | 'taken' | 'empty'
      tube        0-based index of the tube in question, or None
      label       medicine name
      dose        pills per dose
      next_hhmm   'HH:MM' of the next scheduled dose
      next_in     seconds until it
    """
    mode = view.get("mode", "idle")
    tube = view.get("tube")

    # 1. Nothing sensible can be shown without a wall clock. The ESP32 has no
    #    battery-backed RTC, so after a power cut the time is 2000-01-01 until
    #    the carer's laptop syncs it over USB.
    if not view.get("clock_set"):
        return fit("KAIRO"), fit("SET CLOCK")

    # 2. The servo is moving right now.
    if mode == "dispensing":
        return fit("DISPENSING"), fit("TUBE %d" % ((tube or 0) + 1))

    # 3. A dose was due and the tube is empty. The patient is told because
    #    they are standing there waiting for a pill that is not coming - but
    #    they are told it is handled, not given a job.
    if mode == "empty":
        return fit("TUBE %d EMPTY" % ((tube or 0) + 1)), fit("CARER ALERTED")

    # 4. A dose is due and the box is waiting for somebody to come to it -
    #    the pill only drops once they are there. This overrides everything:
    #    it is the one moment the screen has a job to do.
    if mode == "due":
        dose = int(view.get("dose", 1))
        label = short(view.get("label", "Medicine"), 11)
        return fit("COME TO THE BOX"), fit("%-11s x%d" % (label, dose))

    # 5. The pill has just dropped. Held for a few seconds.
    if mode == "taken":
        return fit("TAKE YOUR PILL"), fit("FROM THE TRAY")

    # 6. Idle: the countdown, and nothing else.
    hhmm = view.get("next_hhmm")
    if not hhmm:
        return fit("KAIRO  READY"), fit("NO DOSES SET")

    line0 = "NEXT %s  T%d" % (hhmm, (tube or 0) + 1)
    line1 = "IN " + human_gap(view.get("next_in"))
    return fit(line0), fit(line1)
