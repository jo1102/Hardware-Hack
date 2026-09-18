# Kairo — the website

**Kairo by Access Granted.** A three-tube automatic pill dispenser, and the
two screens that go with it.

Somebody who needs prompting three times a day does not need a 24/7 carer —
they need something in the kitchen that opens a gate, plays a tune, and tells
one relative when the pills are running out. That is the whole pitch, and this
folder is the software half of it.

---

## Two sites, on purpose

| | Who it is for | What it is |
|---|---|---|
| **`index.html`** | the carer, on a laptop or phone | Refill alerts, schedule editing, pill counts, adherence, the camera, the sound, the raw serial. Everything that involves a decision. |
| **`patient.html`** | the patient, on a tablet beside the box | One message, at most two buttons, type sized to fill the screen. |

They are separate pages, not two tabs of one app, because the patient's device
must have **no path** into schedule editing or pill counts. There is no
navigation on the kiosk at all — nothing to get lost in, and nothing a confused
tap can reach. The way back to the carer console is a deliberate
press-and-hold on the corner status dot, which a stray touch will not trigger.

The two divide the information as well as the controls. **Stock warnings never
appear on the box or on the kiosk.** Being nagged about restocking is not a
task you hand to somebody who needs a machine to remember their medicine — so
running low surfaces at the top of the carer console, with a Refill button that
writes the new count straight to the board.

---

## Run it

### The full demo (laptop + board over USB)

```bash
python dispenser/deploy.py          # once, to put the agent on the board
python site/bridge.py               # then this, every time
```

| | |
|---|---|
| Carer console | <http://localhost:9000> |
| Patient kiosk | <http://localhost:9000/patient> |

No WiFi, no IP addresses, no re-flashing between changes. `deploy.py` finds the
COM port itself, copies the agent plus the drivers it needs, and soft-resets so
`main.py` starts.

### With no hardware at all

```bash
python site/bridge.py --no-serial
```

…or just double-click `site/index.html`. Both pages ship with a full simulation
of the box — a patient-shaped schedule, one tube already low, a fortnight of
adherence history — so they are presentable with nothing plugged in. **Use this
if the demo table has no power for the servos.**

### From a phone or a tablet

`bridge.py` binds `0.0.0.0` and prints a LAN address on startup. Open that on
the phone, and `/patient` on the tablet. CORS is wide open, so a copy of the
`site/` folder on a USB stick also works — put the bridge address into the
panel in the bottom-right corner of the carer console.

Keyboard, for demoing quickly: <kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> switch
carer sections, <kbd>F</kbd> fires the next dose.

---

## Files

No build step, no `node_modules`, no external requests — the site has to open
off a USB stick at a venue with no internet. Plain `<link>` and `<script>`
tags, and classic scripts rather than ES modules, because modules are blocked
by CORS on `file://`.

```
site/
  index.html        carer console shell
  patient.html      patient kiosk shell
  bridge.py         HTTP server + persistent USB serial link
  app/
    theme.css       design tokens and shared components (both pages)
    carer.css       carer layout
    patient.css     kiosk layout
    core.js         helpers, LCD mirror, sound, toasts, adherence history
    device.js       the data layer: simulator, bridge client, command sending
    carer.js        carer rendering and wiring
    patient.js      kiosk rendering and wiring
dispenser/
  main.py           the agent on the board: scheduler, protocol, persistence
  hardware.py       three gate servos, the 1602, the chimes
  lcdview.py        the 16x2 screen layouts
  deploy.py         one command to put all of it on the board
```

`core.js` and `device.js` are shared by both pages, so the simulator, the
serial protocol and the LCD mirror exist once. Everything that differs between
audiences lives in `carer.*` or `patient.*`.

---

## How the pieces fit

```
  carer console ─┐                                    dispenser/main.py
  patient kiosk ─┴──HTTP──▶ site/bridge.py ──USB──▶  (Freenove ESP32-S3,
                            (laptop, holds             MicroPython)
                             the port open)                 │
       │                                         3 servos ──┤ GPIO21/38/39
       └──────HTTP/MJPEG──────▶ XIAO ESP32-S3 Sense   LCD  ──┤ SDA14/SCL42
                                (its own WiFi)       audio ──┘ I2S 2/41/48
```

Two independent boards. The dispenser talks over the USB cable it is already
plugged into; the camera is a separate device on WiFi that the browser reaches
directly. Nothing about the dose schedule depends on the camera working.

### Why serial rather than a web server on the ESP32

Hackathon networks routinely isolate clients from each other, so a browser
often cannot reach a board on the same SSID at all. The cable is already there
and adds no latency worth measuring. `bridge.py` holds one connection open for
the whole demo, so a command from either page reaches the servo in about 30 ms
with nothing to restart in between.

---

## The serial protocol

One JSON object per line, both directions. Anything the board prints that is
not JSON is treated as a console line and shown in the carer console's device
console — so ordinary `print()` debugging still works while the site is live.

| Sent to the board | Effect |
|---|---|
| `{"c":"hello"}` | identify, report which pins answered |
| `{"c":"time","t":[Y,M,D,h,m,s]}` | set the RTC — this board has no battery |
| `{"c":"sched","i":0,"times":["08:00"],"label":"…","dose":1}` | change one tube's schedule |
| `{"c":"pills","i":0,"n":42}` | carer has refilled or recounted a tube |
| `{"c":"dispense","i":0}` | dispense that tube right now |
| `{"c":"force"}` | fire the next scheduled dose immediately |
| `{"c":"taken","i":0}` | patient acknowledged |
| `{"c":"help"}` | patient pressed “I need help” on the kiosk |
| `{"c":"snooze","m":10}` | push the current reminder back |
| `{"c":"sweep","i":0}` | one gate cycle, nothing logged — the Test gate button |
| `{"c":"chime","name":"bells"}` | play a sound now, for previewing one |
| `{"c":"servo","i":0,"a":90}` | hold one servo at an angle, for calibration |
| `{"c":"cfg", …}` | patient name, low-stock threshold, reminder timing, chime, volume |

The bridge has two HTTP endpoints of its own that never reach the board:
`GET /api/camera` reports where the XIAO camera was last seen, and
`POST /api/camera/find` sweeps the local subnet looking for it.

The board replies with `{"e":"state", …}` about once a second, plus
`{"e":"ack"}` for each command and `{"e":"event"}` as things happen.

---

## Decisions a judge is likely to ask about

**Two separate screens, not one app with a patient mode.** A mode is something
you can leave. See the top of this file.

**The box never asks the patient to manage stock.** It shows what to take,
when, and — if a tube is empty — that the machine failed them and the carer has
been told. That is the entire vocabulary of the 1602 screen.

**A dose that is hours late is not dispensed.** If the box was unplugged at
08:00 and comes back at 14:00, giving somebody their morning pills six hours
late is worse than skipping them. Anything older than the one-hour catch-up
window is logged as skipped and the pills stay in the tube.

**An empty tube does not log a dose.** The servo does not turn, the count is
untouched, and the event is recorded as a failure to deliver rather than as
medicine taken. Turning the gate on an empty tube would produce a perfect
adherence record for a person who got nothing.

**Editing a schedule cannot cause a double dose.** The record of what has
already fired today is deliberately *not* cleared when the carer edits a tube,
so fixing a typo at 08:30 does not trigger a second 08:00 dose.

**The clock is refused rather than guessed.** The ESP32-S3 has no
battery-backed RTC, so after a power cut it believes it is 2000-01-01. The
scheduler will not run until the bridge sets the real time; the LCD says
`SET CLOCK` and the console raises it.

**Counts stay honest when the hardware is missing.** If a gate servo is
unplugged the count still comes down and the event log records `servo did not
respond`. An over-count is the more dangerous of the two errors.

**None of the reminder sounds are alarms.** One of them plays in somebody's
home several times a day for years, and anything that reads as an emergency in
week one gets ignored by week three. A missed dose or an empty tube uses a
separate, more insistent tune that is deliberately not adjustable, so the two
can never be confused.

**The camera is not surveillance.** No recording, no storage, no face
recognition. It opens only when a carer presses the button, shows an on-screen
indicator while it is live, and closes when they leave the page. Facial
recognition was on the table early and was dropped on purpose — a look-in to
decide whether to phone somebody needs consent, not a database.

**Adherence numbers are never invented.** In live mode the percentage is
counted only from events the box actually reported, and days with no data are
drawn as blanks rather than as successes. The fortnight of history in demo mode
is labelled *Demo data* everywhere it appears.

**“I need help” does not claim to call anyone.** There is no phone network in
this build, so the button records the request on the board and the carer
console raises it. It says exactly that and nothing more.

---

## Hardware notes that cost us real time

**Servo power.** One SG90 pulls 500–700 mA stalled, which browns out the board
and drops it off USB entirely — file transfers die mid-write, the COM port
vanishes, and none of it looks like a power problem. There are **three** of
them, so an external 5 V supply with a shared ground is not optional. The
firmware only ever drives one servo at a time, creeps a degree at a time, and
releases the PWM between doses to keep the peak down. **If the board starts
behaving strangely, unplug the servos before debugging anything else.**

**One servo per tube, one sweep per pill.** Each tube has its own gate servo on
GPIO21 / 38 / 39, with a shut angle and an open angle you calibrate from the
`Test gate` button on each tube card. A two-pill dose opens and closes the gate
twice rather than holding it open for longer — holding it open is how you get
three pills out of a two-pill dose. Copy the numbers you settle on into
`SERVO_CLOSED` / `SERVO_OPEN` in `dispenser/hardware.py` so they survive a
reflash.

**Why GPIO38 and 39.** They are among the very few pins this module leaves
free: the camera ribbon claims 4–18, the LCD has 14 and 42, the amplifier has
2/41/48, 26–37 are the Octal PSRAM, 19/20 are USB, 43/44 are the UART console
and 0/45/46 are strapping pins. 38/39 are only spoken for by the SD card in
Freenove's *Arduino* sketch, which this MicroPython build cannot reach anyway.
GPIO40 is the spare.

**A servo cannot be detected.** `Servo.release()` calls `PWM.deinit()`, and a
de-initialised PWM silently refuses every later write — which is why the
firmware builds a fresh PWM per movement rather than holding one open. All the
probe at startup can tell you is that the pin accepted PWM, *not* that a servo
is wired to it. `Test gate` is the only real check.

**The LCD is 16 characters by 2 lines**, which is the constraint that shaped
every string on the box. `dispenser/lcdview.py` returns exactly two
16-character lines for each state, and `app/core.js` runs the identical logic
so the LCD panel on the carer console matches the hardware character for
character. Change one, change the other.

**GPIO42 for I²C SCL, not the manual's GPIO13** — 13 is the camera's pixel
clock and unusable while the ribbon is attached. The full verified pin table is
in the repository root `README.md`.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `COM4 answered, but no Kairo agent` | The port is right but `main.py` is not on the board. Run `python dispenser/deploy.py`. |
| `no serial ports found` | Board unplugged, or a stale port. `python -m mpremote connect list`. |
| `mpremote: failed to access COM4` | The bridge is holding the port. `Ctrl-C` it, or if it is orphaned in the background: `Get-CimInstance Win32_Process \| Where-Object { $_.CommandLine -like '*bridge.py*' } \| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`. Check it is free with `python -c "import serial; serial.Serial('COM4').close(); print('free')"`. |
| Console says **No device** | `bridge.py` is not running, or the address in the bottom-right panel is wrong. |
| Countdown frozen, frame age climbing | The agent stopped. The bridge restarts it on its own after fifteen seconds; the device console shows it happening. |
| A tube's gate does nothing but its dot is green | The dot only means the PWM channel came up. Check the wiring, then `Test gate`. |
| `No stream at …` on check-in | Wrong IP, or **Start Stream** was never pressed on the camera's own page. Press **Find it for me** rather than hunting for the address — the IP is DHCP and moves every boot. |
| `nothing answering on N addresses` | The camera has no power, has not joined the network, or your laptop is on a different one. All three devices have to be on the same hotspot. |
| Bridge keeps soft-resetting the wrong board | Both boards report Espressif's USB vendor id, so auto-detect has to try one and remember. It rotates to the next candidate on its own; `--port COM4` skips the guessing. |
| Board keeps rebooting during upload | Servo brownout. Unplug them and upload again. |
| A chime option is greyed out | Its WAV is not on the board. `deploy.py` uploads `jingle5.wav`; the tune-based options need no file. |
| Edits to `app/*.js` seem to do nothing | Hard-reload. The bridge sends `Cache-Control: no-store`, but browsers cache aggressively off `file://`. |
