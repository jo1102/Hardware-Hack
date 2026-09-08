# Kairo

**A three-tube automatic pill dispenser, and the two screens that go with it.**
Built by **Access Granted**.

Somebody who needs prompting three times a day does not need a 24/7 carer.
They need something in the kitchen that opens a gate at the right moment,
plays a tune, and tells one relative when the pills are running out.

| | Who it is for | What it is |
|---|---|---|
| **Carer console** | a relative, on a laptop or phone | refill alerts, schedule editing, pill counts, adherence, camera check-in, reminder sound |
| **Patient kiosk** | the patient, on a tablet beside the box | one message, at most two buttons, type sized to fill the screen |

They are deliberately **separate pages on separate devices**, so the patient
tablet has no route into schedule editing or pill counts. Stock warnings never
appear on the box either - being nagged about restocking is not a job you hand
to somebody who needs a machine to remember their medicine.

The box runs on its own. The schedule and the pill counts live in the board's
flash, so a dispenser that gets unplugged carries on where it left off. The
laptop is only needed to change something or to watch.

---

## Run it

### 1. What you need

| | Check |
|---|---|
| Python 3.9 or newer | `python --version` |
| A Freenove ESP32-S3 WROOM running MicroPython 1.27+ | flash from `firmware/` if the board is bare |
| A USB cable | that is the whole connection - **no WiFi involved** |

**Hardware is optional.** Both screens ship with a full simulation of the box -
a patient-shaped schedule, one tube already low, a fortnight of adherence
history - so everything runs and demos with nothing plugged in.

### 2. Set up, once

```bash
git clone https://github.com/jo1102/Hardware-Hack.git
cd Hardware-Hack
python -m pip install -r requirements.txt
```

Only needed if you want the **camera** (the dispenser itself never touches
WiFi). Both copies are gitignored, so your credentials stay on your machine:

```bash
cp wifi_config.example.py wifi_config.py
cp camera/XiaoCam/wifi_secrets.example.h camera/XiaoCam/wifi_secrets.h
```

Then edit those two copies and put your own 2.4GHz network in them.

### 3. Run it with no hardware

```bash
python site/bridge.py --no-serial
```

Open <http://localhost:8000>. Or skip Python entirely and double-click
`site/index.html` - there is no build step, so it opens straight off disk.

### 4. Run it with the board

Put the agent on the board. Once, and again whenever you change anything in
`dispenser/`:

```bash
python dispenser/deploy.py
```

Then start the bridge. Every time:

```bash
python site/bridge.py
```

| Screen | URL |
|---|---|
| Carer console | <http://localhost:8000> |
| Patient kiosk | <http://localhost:8000/patient> |

It binds `0.0.0.0` and prints a LAN address too, so a phone or a tablet on the
same network can open either page.

Three things worth knowing:

- `deploy.py` finds the COM port itself. The port number **changes between
  reboots** on this board.
- **Stop the bridge before running `deploy.py` or any `mpremote` command** -
  it holds the serial port open.
- The bridge re-detects, re-opens and re-syncs the clock on its own, so
  nothing needs unplugging or resetting between commands.

### 5. The camera, optional

`arduino-cli` is not in the repository (it is 53MB of binary). Download it
into `tools/` when you need it:

```bash
curl -fsSL https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip -o tools/arduino-cli.zip
```

Unzip it there, then build and upload the sketch:

```bash
tools/arduino-cli.exe compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" camera/XiaoCam
```

`PSRAM=opi` is **not optional** - see the camera section below.

---

## What is in here

| Folder | What is in it |
|---|---|
| `site/` | The two web screens and the USB serial bridge. **Start here:** [`site/README.md`](site/README.md) |
| `dispenser/` | Kairo firmware: scheduler, gate servos, 16x2 screen layouts, deploy script |
| `lcd/` | I2C 1602 LCD driver and tests |
| `audio/` | I2S amplifier, PWM speaker, audio conversion, the chime WAVs |
| `servo/` | The servo driver, plus sweep and positioning tests |
| `camera/` | Camera scripts, plus the XIAO Arduino sketch |
| `serial-tools/` | Reading boot logs and serial output |
| `diagnostics/` | Hardware probes for when something is not responding |
| `firmware/` | MicroPython image, for restoring a board that was flashed with Arduino |
| `tools/` | Where `arduino-cli` goes (not committed) |

[`site/README.md`](site/README.md) has the serial protocol, the design
decisions behind each safety rule, and a troubleshooting table. Read that one
before changing anything.

---

## Hardware reference

Everything below is the underlying hardware notes. They were written while
getting each peripheral working and every one of them cost real time.


Two separate boards live in here. They are independent computers, not a board
and a peripheral, and each is flashed on its own:

| Board | Firmware | Talks over |
|---|---|---|
| **Freenove ESP32-S3 WROOM** | MicroPython 1.27 | `mpremote` |
| **XIAO ESP32-S3 Sense** | Arduino (CameraWebServer) | `arduino-cli` |

The COM port number **changes between reboots** - the Freenove board exposes
both a native USB and a WCH UART bridge, so it has appeared as COM3, COM4 and
COM6 at different times. Always check first:

```
python -m mpremote connect list
```

## Confirmed pin assignments (Freenove ESP32-S3)

Verified working, not guessed. The camera ribbon uses the **ESP32S3_EYE**
pinout, which claims a lot of pins - everything else was chosen around it.

| Peripheral | Pins |
|---|---|
| Camera ribbon | 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18 |
| LCD | SDA **14**, SCL **42** (5V + GND) |
| Servos (one per tube) | **21**, **38**, **39** (spare: 40) |
| I2S amplifier | BCK **2**, LCK **41**, DIN **48** |
| PWM speaker (transistor) | **47** |

The audio pins deliberately avoid all the others, so **camera + LCD + audio can
run at the same time**.

Pins to leave alone: 0 / 45 / 46 (strapping), 19 / 20 (USB), 43 / 44 (UART
console), 26-37 (Octal PSRAM on this module - `Pin(34)` raises `invalid pin`).

## Power

**Do not run the servo from the board's 5V pin while it is USB powered.** This
cost the most time of anything in this project. An SG90 draws 500-700 mA at
stall, which on its own exceeds what a typical USB port supplies, and the
brownouts do not look like a power problem at all. Symptoms seen:

- a file transfer dying mid-write, leaving a 0-byte file on the board
- the board falling off USB entirely, no COM port at all
- coming back stuck in `boot:0x0 (DOWNLOAD(USB/UART0))`, ignoring the reset
  button, and only recovering after a physical unplug
- MicroPython answering one command and then vanishing

None of these point at the servo, so if the board starts behaving erratically,
**unplug the servo first** before debugging anything else. Give it its own 5V
supply with a shared ground.

The same caution applies to the amplifier at volume, though it is a much
smaller load than the servo.

Diagnosing this: `serial-tools/read_any.py COM5` reads the raw boot log, which
is what exposed the download-mode state. `python -m esptool --port COM5 flash-id`
confirms the chip is alive and the flash intact even when MicroPython is silent.

## LCD

```
python -m mpremote connect COM4 fs cp lcd/LCD_API.py :LCD_API.py
python -m mpremote connect COM4 fs cp lcd/I2C_LCD.py :I2C_LCD.py
python -m mpremote connect COM4 run lcd/lcd_test.py
```

Two things that cost real time here:

- **VCC and GND are diagonally opposite corners** on this board. The first LCD
  module was destroyed by reverse polarity. Check twice before powering on.
- Wiring the data lines **through a breadboard failed**; direct jumpers worked.
  Breadboard rows are split at the centre trench, so an LCD pin and its jumper
  must sit in the same 5-hole block on the *same side* of the gap.

`i2c_scan.py` sweeps several SDA/SCL combinations and reports which one answers
- use it instead of guessing when the screen is silent. A working bus reports
address `0x27`.

## Audio

`audio/audio_amp.py` is the reusable module. Import it rather than copying tone
code around:

```python
import audio_amp
with audio_amp.open_amp():
    audio_amp.tone(1000, 200)
    audio_amp.melody([("E5", 200), ("C5", 200), ("G5", 400)])
    audio_amp.play_wav("/jingle.wav")
```

`VOLUME` at the top of the module is the only knob you normally touch. `0.9` is
painfully loud; `0.05` is the current default.

The ES7148 + PAM8403 module **generates its own master clock**, so its `SCK`
pin needs no connection. This is the reason the whole thing works in
MicroPython at all - `machine.I2S` has no `mck=` parameter, and an external
MCLK would have forced an Arduino reflash.

The 8 ohm 2W speaker is a *speaker*, not a buzzer - buzzers are rated in volts.
It can be driven either through this amplifier (real audio) or through the NPN
transistor on GPIO47 (`speaker_tone.py`, square-wave beeps only). It can only be
wired to one of those at a time.

### Playing files

MicroPython has **no MP3 decoder**, so `convert_audio.py` decodes on the PC and
writes 16 kHz / 16-bit / mono WAV, which is what `play_wav()` expects:

```
python audio/convert_audio.py "song.mp3" audio/clip.wav 5
```

Budget roughly 32 KB per second of audio against ~5.8 MB of free flash.

`mpremote` uploads over the REPL, so large files take a while, but size was not
the real limit - see **Power** below. A 156 KB clip transfers fine.

For **full songs**, internal flash is the wrong tool. The SD card is the right
answer and it needs Arduino, because:

- MicroPython cannot decode MP3
- this MicroPython build cannot reach the SD card at all - `machine.SDCard`
  rejects custom SDMMC pins ("extra keyword arguments given") and every mount
  attempt returns `OSError 16`, because the defaults collide with the Octal
  PSRAM
- Freenove's Arduino `Sketch_29.2_SDMMC_Music` does both natively (SD on
  CLK 39 / CMD 38 / D0 40)

Flashing Arduino to the Freenove board **erases MicroPython**. It is reversible
using `firmware/`, but it is a flash cycle each way.

## XIAO camera

Streams live video over WiFi. Build and upload:

```
tools/arduino-cli.exe compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" camera/XiaoCam
tools/arduino-cli.exe upload -p COM6 --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" camera/XiaoCam
python serial-tools/read_serial.py
```

### The camera pin list is NOT a wiring guide

`camera_pins.h` lists sixteen GPIOs for `CAMERA_MODEL_XIAO_ESP32S3` (XCLK 10,
SIOD 40, SIOC 39, Y2-Y9, VSYNC 38, HREF 47, PCLK 13). **None of them get
wired.** They are traces already etched into the XIAO Sense expansion board,
and most of them - everything above GPIO 9 - are not brought out to the
14-pin edge header at all. The list is compile-time configuration telling the
driver which internal pins the traces use.

The whole physical assembly is:

1. Seat the XIAO ESP32-S3 core board onto the **Sense** expansion board (the
   one with the camera socket, microphone and SD slot). The two boards
   sandwich together on the board-to-board connector.
2. Plug the camera ribbon into the socket on the Sense board: flip the dark
   latch up, slide the ribbon in with the gold contacts facing the PCB, press
   the latch down.
3. USB-C to the laptop.

No jumper wires. If the ribbon is backwards, loose or not seated, the serial
log says `Camera init failed with error 0x...` and nothing else happens - the
sketch returns early rather than pretending.

### Running the XIAO without a USB cable

USB-C is needed to **flash** it, not to **run** it. Once the sketch is on
there, two wires are enough:

| XIAO pin | Goes to |
|---|---|
| **VUSB** (this is the 5V pin - same rail, Seeed label it VUSB on some revisions) | your external 5V rail |
| **GND** (directly below it) | shared ground |

Reading down the right-hand row from the USB-C end: **VUSB, GND, 3V3, D10,
D9, D8, D7**. The left row is D0-D6.

Two cautions:

- **Never feed VUSB while USB-C is plugged in.** That pin is the USB VBUS
  rail, so powering both ends can push current back at the host. One or the
  other.
- **Power it from the servos' external 5V supply, not from the Freenove.**
  The XIAO with the camera streaming and WiFi transmitting is a real load,
  and the Power section above is what happens when this board is asked for
  more current than it has.

### Finding the camera without the serial log

Pin-powered means no serial, and the address is DHCP so it changes on every
boot. The carer console solves this: **Check-in > Find it for me** asks the
bridge to sweep the network for it. That works because the bridge is Python
on the same network with no browser sandbox in the way - it looks for a host
with port 81 open whose `/status` returns the CameraWebServer JSON, so it
cannot mistake something else for the camera.

It sweeps roughly 250 addresses per subnet in about four seconds, and it is
**on demand only** - never on startup. Sweeping a network you happen to be
joined to is not something a program should do uninvited, particularly on a
university network.

Static IPs and mDNS were both considered and rejected: on a phone hotspot the
subnet varies by vendor and OS version, and hotspots frequently drop the
multicast that mDNS needs.

**Coincidence worth naming:** the XIAO camera uses GPIO38 and GPIO39
internally, and Kairo's tube-2 and tube-3 gate servos also use GPIO38 and
GPIO39. Different boards, so there is no conflict - but do not "fix" one
after reading the other.

`PSRAM=opi` is **not optional** - this board definition defaults PSRAM to
*disabled*, and the camera needs it.

The serial log prints the URL to open, e.g. `http://10.24.109.107`. The IP is
DHCP-assigned and changes; re-read the log rather than trusting an old one.
Press **Start Stream** on the page - it does not auto-start.

There is no FPS setting. Frame rate is a side effect of **Resolution**,
**Quality** (higher number = more compression = faster) and **XCLK MHz**.
Changes made in the web UI are lost on reboot; bake them into the sketch to
make them stick.

WiFi credentials are compiled into the sketch and must live in
`camera/XiaoCam/wifi_secrets.h` - it does not read `wifi_config.py`. Copy the
example file:

```
cp camera/XiaoCam/wifi_secrets.example.h camera/XiaoCam/wifi_secrets.h
```

That copy is gitignored. If it is missing the sketch still compiles (an
`__has_include` guard swaps in placeholders and the compiler warns), it just
will not connect.
