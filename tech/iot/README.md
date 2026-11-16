# Two-node knee-strap IoT codebase (MicroPython)

This is a separate codebase directory, **not a nested Git repository/submodule**.
`../designs/` is unchanged. Example target: ESP32-C3 mini and **MPU6050**. “MPU”
is a family, not a precise part number: MPU9250/6500 and other parts may need
register/identity changes. Confirm your breakout part and exact board first.

**Research acquisition only. No clinical judgment, live movement clearance or
on-device ML.** Raw two-sensor data can later be reviewed using the project's
computer interface. Existing dataset model accuracy does not validate these
straps for use after surgery.

## Architecture

- **Master / thigh:** its own MPU6050, SPI SD card, request scheduler and CSV
  writer. It initiates a new radio session at startup.
- **Slave / shank:** its own MPU6050; replies only to the configured master's
  sample requests. No SD card required.
- ESP-NOW unicast on a fixed channel. No router/Bluetooth needed during normal
  acquisition, but **ESP-NOW uses the 2.4 GHz Wi-Fi radio**.
- The master pairs both measurements using session/sequence IDs. Time is the
  master's acquisition timestamp, not the slave clock. This is request-paired,
  sequential acquisition, **not hardware-synchronized sampling**. Quantify the
  timing skew before interpreting relative joint motion.
- Missing, stale, malformed or timed-out responses are not replaced by zeros or
  the last good value. Missing rows appear as timestamp gaps; re-record rather
  than hide gaps to make an ML upload pass.
- Wi-Fi provisioning/OTA is a separate maintenance boot; logging is stopped.
  An infrastructure AP's channel therefore cannot silently change ESP-NOW's
  acquisition channel mid-session. Both devices need the same configured radio
  channel and compatible protocol/application versions.

## Files and iterations

| Path | Purpose |
|---|---|
| `iterations/v1_hardcoded/master.py`, `slave.py`, `settings.py` | Fast bench bring-up; hardcoded peer MACs, unencrypted radio |
| `iterations/v2_provisioned/master.py`, `slave.py` | Role-checking wrappers for provisioned bootloader |
| `app/` | MPU6050, protocol, radio, master/slave acquisition, SD logging |
| `system/`, `bootloader.py`, `main.py` | Trusted setup, configuration, authenticated file OTA and boot-slot control |
| `tools/manage.py` | Host-side private factory kit and OTA bundle creation |
| `tests/` | Host unit tests; not hardware qualification |

Application firmware contains no `print()` calls. MicroPython's ROM/bootloader,
REPL and fatal interpreter output are not silenced by this application; shipping
hardware needs separate UART/USB and firmware hardening. Silent code does not
mean fault-free operation. Use USB/SD state inspection for diagnosis, and never
interpret missing feedback as a successful recording.

## Wiring (example only)

**Each node has one IMU**, so both may use address `0x68` with AD0 grounded.
Do not use the previous single-board/two-address Arduino diagram unchanged.
All connections are 3.3 V logic. Confirm module supply/regulator requirements and
provide an adequate regulated supply/common ground. Avoid strapping and native
USB pins unless verified against the exact mini-board schematic.

| Signal | Example GPIO |
|---|---:|
| MPU6050 SDA / SCL, both nodes | 4 / 5 |
| SD SCK / MOSI / MISO / CS, master only | 6 / 7 / 3 / 10 |

`tech/designs/README_electronics.md` describes **draft, not fab-ready** electronics
and suggests SD MISO GPIO2, unlike this code's GPIO3. GPIO2 is a C3 strapping pin;
this template deliberately uses GPIO3. Check the actual PCB/netlist and change
configuration before applying power. No automatic pin inference was made from
CAD, and designs were not reviewed or altered. Do not connect a 5 V-logic SD
adapter directly to an ESP32. Start with a FAT32 card.

Mount the labelled thigh/shank pods consistently; document side, axes and
anatomical positions. Gyro bias, mounting alignment, strap slippage and relative
sensor timing need real measurements. Neither mirroring nor a knee-angle
calibration is invented by the logger.

## Requirements

Use a current **ESP32-C3 MicroPython build** with `espnow`, `network`,
`machine.I2C`, `machine.SDCard` SPI support, and TLS `ssl.SSLContext` with CA
verification for iteration 2. Generic ESP32 images may not fit C3 chips. Flash
the correct official image first using its documented address/erase procedure;
this repository does not flash boot ROM or partition tables.

Install host tooling separately:

```bash
python -m pip install mpremote
```

Keep USB recovery available. There is no physical hardware connected to this
checkout; radio behavior, SPI SD support, flash space, TLS memory and power-loss
recovery all require validation on the actual firmware/boards.

## Iteration 1 — hardcoded bench bring-up

Read each node's **STA MAC** over a deliberate USB REPL session (not AP MAC):

```python
import network
sta = network.WLAN(network.STA_IF)
sta.active(True)
sta.config('mac').hex()
```

Edit `iterations/v1_hardcoded/settings.py`: master peer MAC is the slave's STA
MAC and slave peer MAC is the master's. The included `02...` addresses are
**placeholders**, not a way to reassign hardware identities. Set identical
channels and the actual GPIOs.

From `tech/iot`, copy onto the master (replace `PORT` with the actual port):

```bash
mpremote connect PORT fs cp -r app :
mpremote connect PORT fs cp iterations/v1_hardcoded/settings.py :settings.py
mpremote connect PORT fs cp iterations/v1_hardcoded/master.py :main.py
mpremote connect PORT reset
```

Repeat on the slave, copying `slave.py` as `:main.py`. Test on a bench with no
patient information: iteration 1 uses unencrypted packets and peer MAC filtering
is **not authentication**. Recording begins with the acquisition application;
use controlled power/reset and close files before removing SD. Do not hot-remove
the SD card. To interrupt the app for recovery use `mpremote repl`, Ctrl-C.

The master CSV schema is compatible with the desktop interface:

```text
time_ms,thigh_ax,thigh_ay,thigh_az,thigh_gx,thigh_gy,thigh_gz,shank_ax,shank_ay,shank_az,shank_gx,shank_gy,shank_gz
```

Acceleration is m/s² including gravity, angular velocity is rad/s. Target 50 Hz
is a scheduling goal, not a tested timing guarantee. SD flushes/radio loss can
produce gaps. Request/response round trips must fit within the sampling period;
stress test both radio nodes and SD together.

## Iteration 2 — private one-time factory bootstrap

No credentials are compiled into source. An initial **trusted USB provisioning**
step remains necessary: you cannot securely bootstrap ownership over an open
radio with no trust material at all.

```bash
# From tech/iot; replace with the two actual STA MACs.
python tools/manage.py factory-pair \
  --master-mac AA:BB:CC:DD:EE:10 --slave-mac AA:BB:CC:DD:EE:12 \
  --iteration 2 --channel 6 --out private/pair001
```

This creates **different per-device setup and OTA secrets**, one shared radio
PMK/LMK for this pair, and unique maintenance AP names/passwords. Do not reuse a
pair's keys across products. `private/` is ignored by git; restrict access and
back up securely. Never send `*_PRIVATE.json` files in logs, support tickets,
SD recordings or screenshots. The factory tool does not print secrets.

Review each kit's `config/device.json` and correct pins before upload. Copy the
**contents** of `private/pair001/master/` to the master's filesystem root,
including `system/`, `config/`, `slots/`, `main.py` and `bootloader.py`; use the
slave kit for the slave. For example:

```bash
mpremote connect PORT fs cp -r private/pair001/master/system :
mpremote connect PORT fs cp -r private/pair001/master/config :
mpremote connect PORT fs cp -r private/pair001/master/slots :
mpremote connect PORT fs cp private/pair001/master/bootloader.py :bootloader.py
mpremote connect PORT fs cp private/pair001/master/main.py :main.py
```

The common `main.py` uses the persisted role; separate v2 master/slave wrappers
are optional. The default kit leaves the maintenance GPIO unset to avoid
accidentally assigning a strapping/SD/IMU pin. Use deliberate USB maintenance
entry, or configure an available physical service-button pin after checking
wiring. Do not automatically open a setup AP when config is missing/corrupt.

Detailed maintenance and OTA commands are in [MAINTENANCE.md](MAINTENANCE.md).

## Security boundaries — not a commercial-release claim

- Iteration 2 encrypts unicast ESP-NOW with per-pair keys; no insecure broadcast
  fallback. Session/sequence checks reject unrelated or duplicate samples.
- Setup uses physical/USB maintenance entry, a private password-protected AP and
  a separate setup token. It is not a fully authenticated TLS provisioning UX.
- File OTA uses a **per-device HMAC**, HTTPS with mandatory certificate checking,
  bounded bundles, file digests, version checks and inactive application slots.
  HMAC is symmetric authentication, **not public-key firmware signing**.
- Filesystem slots update Python `app/` only, not the MicroPython interpreter,
  ESP-IDF, partitions or immutable provisioning/update loader. Those require a
  separate trusted service/firmware update process.
- Device filesystem secrets can be extracted by someone with sufficient device
  or REPL access. File-level version checks are not hardware anti-rollback and
  do not withstand arbitrary flash rewriting.
- No secure boot/flash encryption, encrypted SD, fleet backend, key revocation,
  secure time service or regulatory/clinical certification is implemented.
- Keep Wi-Fi setup/OTA out of clinical exercise sessions. Use protected power
  and test power loss at every update and recording stage.

Before sale: perform security review, select production ESP-IDF/MicroPython
build features, secure manufacturing and key lifecycle processes, lock down
service/debug access without destroying recovery, protect patient data, and
obtain appropriate medical-device and privacy advice. Iteration 2 is a stronger
prototype, not a promise that the device is ready to sell.

## Tests

From the repository root:

```bash
python -m unittest discover -s tech/iot/tests -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Host tests cover deterministic logic only. They cannot validate radio/flash/SD,
hardware watchdog operation, TLS firmware capabilities or electrical safety.
