# Provisioning, updates and recovery (iteration 2)

Read README.md first. This is authenticated **Python application file OTA**, not
an update of MicroPython/ESP-IDF or a production secure-boot implementation.
Stop acquisition and arrange USB recovery before experimenting.

## Enter maintenance deliberately

From an authorized USB REPL, interrupt the app with Ctrl-C (runs logger cleanup)
and create the service flag:

```python
with open('/config/maintenance.flag', 'w') as f:
    f.write('1')
import machine
machine.reset()
```

Alternatively configure an unused `maintenance_pin` in device.json and hold it
low while booting. Check for GPIO conflicts first. A missing/corrupt config does
**not** automatically expose a setup AP. Both nodes have their own maintenance
credentials; setup/update each individually.

The maintenance window lasts up to approximately 180 seconds / 24 clients;
ordinary socket operations time out, but platform DNS/driver stalls cannot be
bounded absolutely. It returns without starting acquisition. Reset explicitly
when finished. There is no background Wi-Fi connection during normal logging.

## One-time Wi-Fi setup

1. Read the appropriate `master_PRIVATE.json` or `slave_PRIVATE.json` on the
   trusted factory computer. It holds that device's unique AP SSID/password and
   64-character setup-token hex. Do not share screenshots or log tokens.
2. Join that WPA2 AP from a trusted computer. Browse to its gateway address
   (normally `http://192.168.4.1/`; confirm the board's AP configuration).
3. Enter the setup token, role, other device's STA MAC, identical channel,
   PMK/LMK, GPIOs as applicable, Wi-Fi credentials and HTTPS OTA URL. Preserve
   the factory pair's unique radio keys; never use example strings in a product.
4. Save configuration, then reset. Wi-Fi credentials are separate from device
   radio config; secrets persist in unencrypted internal flash, not SD.

The page submits authenticated JSON to `/configure`; `/update` accepts only a
setup token and fetches the configured bundle URL. Setup is **HTTP inside a
private WPA2 network**, not an end-to-end TLS provisioning protocol. Physical
service entry plus a separate random setup token restrict access; this remains
a prototype with limited abuse protection. Do not use an open/public AP.

## Prepare a per-device OTA bundle

From `tech/iot`, on the private factory/update computer:

```bash
python tools/manage.py build-ota --app-dir app \
  --key private/pair001/master/config/ota.key --version 1 \
  --output bundles/master_v1.json
```

Build a separate bundle using the slave's key for the slave. Never publish the
keys. Versions must increase relative to confirmed/staged versions. Each bundle
contains canonical-JSON-authenticated manifest/content, sizes and SHA-256
checksums. The canonical serializer is shared by device and host. Only `app/`
Python files are allowed; `system/`, credentials and loader paths are forbidden.
Limits: 32 files, 192 KiB decoded, 256 KiB encoded. These are parser bounds, not
a promise that the C3 has enough RAM/flash; test heap/TLS/SD together.

Serve the bundle at the configured **HTTPS** URL. The server must return a
bounded Content-Length body: no redirects, chunked encoding or compression.
Install the server CA certificate as DER via USB:

```bash
mpremote connect PORT fs cp path/to/your_ca.der :config/ca.der
```

The transport requires certificate verification and hostname checking support;
unsupported TLS APIs fail closed instead of downgrading. Ensure a valid board
RTC for certificate date checks using a trusted service procedure; secure time
bootstrap is not implemented. The CA file is private factory configuration, not
a remotely replaced arbitrary certificate. A Python local HTTP server is **not**
a suitable iteration-2 HTTPS endpoint.

Enter maintenance again, join the private setup AP, authenticate and click
**Fetch signed update**. The board temporarily joins the configured Wi-Fi to
fetch; its AP channel may change and the browser can disconnect. Reconnect or
use USB to inspect state. Both radios are shut down when maintenance ends. A
successful fetch stages an inactive application slot; reset to try it.

## Explicit confirmation / rollback

- Factory application lives in `/slots/a/app/`, initially confirmed version 0.
- Update writes only the inactive slot and stages a pending version.
- The first normal boot records that pending slot as attempted **before**
  starting it. The previously confirmed slot is retained.
- **No automatic “healthy” timer exists.** After bench checks, interrupt the
  running candidate via USB Ctrl-C without resetting, then confirm it:

```python
from system.bootstate import confirm_pending
confirm_pending()
```

- Reset after confirming. If the candidate resets/power-cycles before explicit
  confirmation, the next normal boot rolls back to the previously confirmed
  application. A caught pending-app failure requests a reset to do this.
- A hung application needs operator reset; there is **no watchdog guarantee**.
  This prevents false promises of unattended fleet recovery.
- Confirmed-app failures stay closed for manual repair rather than reset-looping.

Only update mutually compatible protocol versions. Restart **both nodes** after
restarting/updating the master: the slave pins its first accepted session until
restart and will not automatically adopt a different master's session. This
intentional replay/session restriction requires coordinated restart today.

## Logs and stopping

The master mounts the SD at `/sd`; files are `/sd/logNNNNNN.csv` with companion
`/sd/logNNNNNN.health.jsonl`. They are not in the application OTA slots. Health
records describe drop/error counters; they do not mean a patient's motion was
correct. There is no model inference in these scripts.

Use USB Ctrl-C for a controlled bench stop so `finally` can flush/close/unmount.
A reset/power cut is **not** a clean stop and can lose buffered samples or corrupt
a filesystem. No patient-ready stop button/user interface or power-fail circuit
is implemented. Add and validate those before wearable studies.

## Release checklist

- Verify ESP-NOW encrypted link and wrong-peer/old-session rejection on hardware.
- Verify 50 Hz paired timing/skew, RF loss, SD stalls/card full/disconnects.
- Test altered HMAC/content, old versions, invalid CA and interrupted writes.
- Test first boot, pending boot crash, manual confirmation and reset rollback.
- Check actual MicroPython build's `ssl`, `espnow` and SPI `SDCard` APIs.
- Confirm filesystem free space, TLS heap headroom and recovery after power loss.
- Maintain per-device key inventory/revocation outside source control.
- Commercial rollout still needs secure manufacturing, hardware-backed firmware
  protections, debug policy, patient-data safeguards and independent review.
