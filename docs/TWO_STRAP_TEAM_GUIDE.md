# Two-strap ESP32-C3 prototype — teammate instructions

## What works, and what does not

This template logs **two six-axis IMUs**, one on the thigh above the knee and
one on the shank below it, to a microSD card. A local computer web interface
reviews the CSV and can run an explicitly selected **research** model.
**There is no model running on the ESP32, no real-time patient guidance, and no
clinically validated correct/incorrect decision.** Do not let patients use these
outputs to decide how far to bend a surgically repaired knee or whether to keep
exercising. Follow the treating clinician's prescribed range/load/protocol.

The old models need 48 channels and cannot consume these recordings. New
`*_two_imu_rf_v1` models are retrained using only sensor IDs **1 and 3** from
KneE-PAD: fixed **right rectus femoris (thigh)** and **right tibialis anterior
(shank)**. They have 12 input channels / 168 features. No missing channels are
filled, duplicated, or inferred. This is a reduced-sensor research reference,
not proof that different braces, sensor locations or left-leg recordings work.
Each exercise has its own model; the app does not recognize exercise type.

## 1. Hardware assumptions — confirm before wiring

- ESP32-C3 mini variant with the listed GPIOs accessible. Consult its schematic.
- **Example driver: two MPU6050 modules**, with selectable AD0 addresses 0x68 and
  0x69. If your IMUs are not MPU6050s, replace the driver/initialization/readout;
  preserve the CSV schema, SI units, sensor identity and target rate.
- 3.3 V-compatible SPI microSD module, FAT32 card, adequate regulated supply.
  Many SD adapters are designed for 5 V Arduinos: check regulator/level-shifter
  requirements; never apply 5 V logic to the ESP32-C3.
- Common ground; short secured wiring and strain relief. Use a protected,
  insulated battery-powered enclosure for wearable work. Do not wire or test on
  a patient; do bench testing first, then obtain clinical/ethics authorization.

Example pin assignment (edit constants in the sketch for **your** board):

| Device connection | Example ESP32-C3 pin |
|---|---|
| Both IMU SDA | GPIO4 |
| Both IMU SCL | GPIO5 |
| SD SCK | GPIO6 |
| SD MISO | GPIO3 |
| SD MOSI | GPIO7 |
| SD CS | GPIO10 |
| All GND | GND |
| Compatible module power | appropriate regulated 3.3 V supply |

Thigh MPU6050 AD0 → GND (0x68); shank AD0 → 3.3 V (0x69). Confirm with an I2C
scanner first. Do not assume this GPIO assignment fits every board. Avoid boot
strapping pins and native USB pins when choosing alternatives. Check I2C pullups
are to 3.3 V; multiple breakout pullups can become excessively strong.

## 2. Mounting and calibration

Mark each sensor **THIGH** or **SHANK** and mark its X/Y/Z axes. Secure the thigh
sensor above and the shank sensor below the knee without restricting circulation
or touching the incision. Exact muscle location, distance from the joint, side,
mounting orientation and strap tightness must be standardized and documented by
the research team/clinician. Merely putting sensors somewhere above/below the
knee is not equivalent to the source dataset's placement.

For bench quality checks: stationary acceleration magnitude should be close to
9.81 m/s², angular velocity near zero, and timestamps near 20 ms apart. Check
both sensors move independently and are not swapped. Measure gyro bias,
accelerometer calibration and orientation on your actual modules. A future
alignment/calibration transform must be applied consistently in training and
inference; this template **does not invent a rotation or mirror left-leg data**.
There is no calibrated knee-angle estimator here. Dual IMUs alone do not prove
that hip compensation or knee alignment is observable reliably.

## 3. Build and log to SD

1. Open `firmware/two_imu_logger/two_imu_logger.ino` in Arduino IDE.
2. Install the Espressif **esp32** board support package. Select the correct
   ESP32-C3 board and USB settings for your mini board (USB CDC on boot may be
   needed for native USB serial).
3. Install **Adafruit MPU6050**, **Adafruit Unified Sensor**, and **Adafruit
   BusIO** with Library Manager. Wire/SPI/SD are supplied by ESP32 Arduino.
4. Edit GPIOs/addresses if needed, compile and upload. This template has not
   been compiled or physically tested on your hardware in this environment.
5. Open Serial Monitor at **115200 baud**. Wait for `Ready`.
6. Enter **s** to start a new `/session_0001.csv` (next unused number). Record
   exactly one known exercise in each file; no patient names in filenames.
7. Enter **x** to stop, flush and close the file. Wait for `STOP` before removing
   the card or cutting power. Never hot-remove a card during writing.
8. Copy the CSV from the SD card to the research computer. Keep the original
   file immutable; do not edit away timing gaps or fabricate lost samples.

The target is **50 Hz**. Reads are sequential, not hardware synchronized; row
time is the start of the paired read. Measure read skew and timing under load.
The sketch uses actual elapsed timestamps and counts missed deadlines; it never
creates fake catch-up samples. SD flushes can cause stalls. The interface rejects
large jitter/gaps rather than pretending the data is uniform. For reliable
continuous acquisition, add a tested queue/ring buffer plus asynchronous SD
writer and data-ready synchronization before patient studies. A successful
initialization is not proof of ongoing I2C integrity; test disconnections, stale
values, saturation, power loss, file corruption and card-full behavior.

## 4. Exact CSV contract

```csv
time_ms,thigh_ax,thigh_ay,thigh_az,thigh_gx,thigh_gy,thigh_gz,shank_ax,shank_ay,shank_az,shank_gx,shank_gy,shank_gz
```

- Acceleration: **m/s² including gravity**; rotation: **rad/s**.
- `time_ms`: monotonic elapsed milliseconds, not a clock-of-day string.
- No EMG, magnetometer, temperature, labels or patient information in the file.
- No blank readings, NaN/Inf, backwards timestamps, missing samples or appended
  sessions. All columns are numeric, in the exact order above.
- Adafruit's MPU6050 `acceleration` and `gyro` events already use these SI units.
  Do not multiply them by 9.81 or convert from degrees a second time.
- At 50 Hz the review requires per-interval timing within ±20% of 20 ms.
  This is a data-quality check, not a clinical criterion.
- Predictions use full two-second windows (100 samples) with one-second hops.
  Start with a short 10–30 second bench recording; upload limit is 5 MB.

## 5. Start the local interface

From the repository root (`/home/nish/dev/projects/ibd_ml` in this checkout):

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .

# Review-only mode; no trained model needed
python -m knee_rehab.interface

# Alternatively enable extension research predictions
python -m knee_rehab.interface \
  --model-dir artifacts/knee_pad_extension_two_imu_rf_v1
```

Open **http://127.0.0.1:8080**. Select the CSV from the SD card, set the actual
sampling rate (normally 50 Hz), and review data quality. Research prediction is
an explicit separate action, not a patient clearance. To review squats or gait,
restart using the matching `knee_pad_squat_two_imu_rf_v1` or
`knee_pad_gait_two_imu_rf_v1` directory. Never point this at an eight-IMU model.
Do not combine exercises/rest/transitions into a file and interpret every
window as form quality: the model has no unknown/rest detector.

The interface is a **computer-hosted page, not an ESP32-hosted Wi-Fi page**.
This version uses SD transfer, not live serial/Bluetooth/Wi-Fi streaming.
Default bind is localhost. Do not expose it on a network: no authentication,
TLS, patient access control or production security is implemented. Uploaded
recordings use temporary files for processing rather than permanent project
storage; OS temporary files/browser memory are not encrypted secure storage.
Use a controlled computer, de-identified session IDs, approved encrypted storage,
consent and retention rules. Do not commit patient recordings to git.

### Try the interface without hardware

```bash
python scripts/create_two_imu_demo.py --output data/interim/two_imu_demo.csv
```

Upload that file. It is deliberately **synthetic**, useful only for checking the
page and CSV contract. Its predictions say nothing about rehabilitation quality.
The generator refuses to overwrite an existing output.

## 6. Retrain using only two source IMUs

The new model directories are generated locally and gitignored. If missing,
create them with new output paths (commands reject overwrites):

```bash
for exercise in extension squat gait; do
  python -m knee_rehab.knee_pad --exercise "$exercise" --sensor-ids 1 3 \
    --skip-empty --output "data/interim/knee_pad_${exercise}_two_imu.json"
  python -m knee_rehab.train \
    --manifest "data/interim/knee_pad_${exercise}_two_imu.json" \
    --output-dir "artifacts/knee_pad_${exercise}_two_imu_rf_v1" --model rf
done
```

No confidence threshold has been clinically established; these commands save
raw research models with no claim of safe operating limits. A probability is
not calibrated clinical certainty. Keep the complete artifact directory, not
just `final.joblib`. Only load trusted model files. No conversion to MCU machine
code or embedded inference is included.

### Actual two-sensor reference results

These are pooled **window-level LOSO**, not tests on your braces or independent
clinical validation:

| Exercise | Participants | Windows | Raw accuracy | Incorrect-form windows falsely accepted |
|---|---:|---:|---:|---:|
| Extension | 29 | 1,899 | 66.35% | 253 / 1,266 = 19.98% |
| Squat | 31 | 2,562 | 47.42% | 476 / 1,335 = 35.66% |
| Gait | 28 | 1,156 | 54.93% | 185 / 830 = 22.29% |

The two-IMU restriction is supported without fabricating inputs, but current
accuracy and false reassurance rates do **not** support patient-facing use.
More representative labelled data and validation are needed, not a claim that
fewer sensors automatically preserve eight-sensor performance. An archive of
the complete research artifacts is `artifacts/knee_pad_two_imu_models_v1.zip`;
it is gitignored and should be backed up separately.

## 7. What is needed before patient-facing feedback

1. Confirm surgery-specific exclusions and motion/load limits with the treating
   clinician. Establish supervision and stop rules outside this classifier.
2. Collect consented, clinician-labelled data using the actual two straps,
   mounting method, IMUs and sampling firmware across relevant participants,
   sides, sessions, exercises, errors and recovery stages.
3. Keep all sessions from each participant together during training/evaluation.
   Fit calibration/normalization and choose thresholds only inside training
   participants; retain independent prospective patients for final evaluation.
4. Quantify per-patient/per-exercise errors, false reassurance, correct-form
   recall, dropout, strap slippage, left/right differences and unknown motion.
   Do not select the best threshold on the reported held-out folds.
5. Validate physical angle/range estimates, latency, electronics and fault handling
   separately. Obtain appropriate clinical, privacy and regulatory review.

## Teammate handoff checklist

- [ ] Confirm board revision, IMU model and SD voltage compatibility.
- [ ] Record GPIO assignment, sensor addresses, side and axis/mounting photos.
- [ ] Bench-test timestamp quality and SD stop/flush behavior.
- [ ] Check a CSV in review-only mode before enabling research prediction.
- [ ] Select the matching exercise's **12-channel** model.
- [ ] Keep source and synthetic data separate; back up complete artifacts.
- [ ] Do not describe research predictions as medical clearance.

Run software checks: `PYTHONPATH=src python -m unittest discover -s tests -v`.
Hardware compile, electrical safety and on-body validation remain outstanding.
