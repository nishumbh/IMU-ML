# Knee Brace: plan for ESP32-S3 hardware, on-device ML, encrypted logs and a journey website

## Context
The repo is a research ML pipeline: scikit-learn random forests trained on KneE-PAD, scored by leaving one subject out at a time (LOSO). Around it sit three hardware designs that disagree with each other:
- an ESP32-C3 round PCB pod (KiCad has defects, and the STL/STEP files have no CAD source);
- an Arduino sketch with two cabled MPU6050s;
- a MicroPython ESP-NOW master/slave design (`tech/iot/`).

Nothing runs ML on the device. The int8 CNN export path exists but has never been run (no TensorFlow installed). The 12-channel RF models are about 500–600 KB as joblib, with LOSO accuracy of 47–66%. SD logs are plain CSV, and the only "frontend" is an HTML string in `src/knee_rehab/interface.py`.

**Decisions taken (confirmed by you):**
- Two wireless pods: thigh and shank, each with an ESP32-S3 Mini dev board and an IMU, linked by ESP-NOW. The thigh pod owns the SD card and runs inference.
- Off-the-shelf dev boards and breakout modules, no custom PCB for now.
- SD logs encrypted with AES-256-GCM, using a key derived from a passphrase.
- A React + Vite + TypeScript website that runs only in the browser: decryption, analysis and history all stay on the user's machine.

**Defaults I chose** (tell me if you want otherwise):
- Board: **LOLIN S3 Mini** (ESP32-S3FH4R2: 4 MB flash, 2 MB PSRAM, 34×25 mm, on-board RGB LED and button).
- IMU: GY-521 MPU6050, so the existing drivers carry over.
- Firmware: Arduino-ESP32 (C++) built with PlatformIO, because TFLite Micro, ESP-NN and the hardware-accelerated mbedTLS AES are first-class there. MicroPython is not. `tech/iot` stays as the protocol reference.
- The old ESP32-C3 KiCad pod moves to "legacy".

---

## Phase 0: Environment and repo hygiene
- Create `.venv` (Python 3.12, which TensorFlow 2.16–2.21 supports; 3.13 may lack wheels). Install with `pip install -e .[train,dev]` plus `emlearn` and `cryptography`, and add both to new extras in `pyproject.toml`.
- Run the existing tests to get a baseline: `PYTHONPATH=src python -m unittest discover -s tests -v`.
- Note for you, not changed automatically: about 1,680 "modified" files are only 644→755 permission flips. `git config core.fileMode false` hides them.
- Regenerate the manifests in `data/interim/*two_imu.json` if they point at the old `/home/nish/dev/projects/ibd_ml` path.

## Phase 1: Model testing and quantization for ESP32-S3 (4 MB flash)
**Budget:** about 3 MB app partition (4 MB minus bootloader, NVS and a no-OTA layout). Model target is **≤ 64 KB in flash** (ideally ≤ 32 KB), with a TFLM arena of ≤ 64 KB in internal RAM and ≤ 50 ms per 2 s window. Input is fixed to the 12-channel, two-IMU, 50 Hz, 100-sample windows from `preprocess.prepare_windows`.

New module `src/knee_rehab/benchmark_edge.py` (CLI `python -m knee_rehab.benchmark_edge --manifest … --output-dir artifacts/edge_<exercise>`). It reuses `evaluate.loso_splits`, `preprocess.prepare_windows/extract_features`, `baseline.make_estimator`, `models.build_cnn/fit_scaling/scale_windows` and `export.export_int8`. Candidates, all scored by LOSO on extension, squat and gait:

1. **RF reference:** the existing 64 trees at depth 6. This is the accuracy baseline.
2. **Compact RF / ExtraTrees** (8–32 trees, depth 4–8), converted to C with `emlearn`. Size is measured from the generated int16/float node tables. Feature extraction (14 features × 12 channels, Goertzel) is ported to C.
3. **1D CNN, fp32 then int8:** the current `build_cnn` plus one or two variants: a wider DS-CNN, and an added channel-mixing 1×C conv. All use only TFLM-supported ops.
   - Implement the missing **int8 inside the LOSO loop**: quantize each fold's model using only that fold's training windows for calibration, then score the int8 interpreter on the held-out subject. That gives a real quantized accuracy, not an fp32 proxy.
   - Augmentation: small random rotations and gain noise, to soften the gap between KneE-PAD sensors and MPU6050 placement.
4. **Threshold tuning:** sweep the `Correct Form` reject threshold with nested selection on the training folds only. The 0.9 threshold currently gives 0% recall.

**Outputs:**
- `artifacts/edge_*/results.json`, plus a comparison table covering accuracy, macro-F1, false-accept rate, correct-form recall, flash bytes, estimated arena and MACs.
- The chosen model exported as `final.int8.tflite`, a C header `model_<exercise>.h` (new `export.write_c_array`), `scaling.json` and `deployment.json`.
- Python-vs-C parity test vectors: 20 windows with the expected int8 outputs.

**Success rule:** pick the smallest model within about 2 points of macro-F1 of the RF reference and with no worse false-accept. If no small model reaches useful accuracy (likely, since the baseline itself is only 47–66%), document that honestly in `docs/TRAINING_RESULTS.md` along with what was tried and the next levers: more on-body data, calibration, rep segmentation.

**Tests:** extend `tests/test_export.py` (C-array round trip, int8 LOSO on synthetic data) and add `tests/test_benchmark_edge.py` using `data/demo_manifest.json`.

## Phase 2: Encrypted SD log format (shared contract)
Spec in `docs/SD_LOG_FORMAT.md`, reference implementation `src/knee_rehab/secure_log.py` (writer, reader, CLI `encrypt`/`decrypt`/`make-fixtures`).

**File `/KB/S<NNNN>.kbx`:**
- Header (plaintext, used as additional authenticated data): magic `KBX1`, version, device_id, session number, 16 B salt, PBKDF2 iteration count, 4 B random nonce prefix, sample rate, IMU scale factors, firmware version, optional wall-clock time (from NTP during provisioning, otherwise null).
- Chunks: `[u32 len][12 B nonce = prefix ‖ u64 counter][ciphertext][16 B tag]`, about 1 s of data each. Losing power drops at most the last chunk.
- Plaintext records:
  - `IMU`: int16 raw for 12 channels, plus a sequence number and a link-loss flag.
  - `INFER`: exercise, class probabilities, label.
  - `EVENT`: start, stop, exercise selected, battery level.

**Keys:**
- During provisioning on a PC (extend `tech/iot/tools/manage.py` or add `secure_log provision`), the passphrase goes through PBKDF2-SHA256 (600k iterations) with a per-device salt to produce a 256-bit key.
- Only the key and salt are written to the thigh pod's NVS. The device never runs PBKDF2.
- The website derives the same key from the passphrase and the salt in the header.
- Documented limit: without ESP32 flash encryption the key can be read from the device. Enabling flash encryption is listed as a hardening step.
- `make-fixtures` writes test vectors to `web/src/__fixtures__/` and `tests/fixtures/kbx/`, so the firmware, Python and the website all agree on the format.
- Update `interface.py` and `predict.py` to accept `.kbx` (decrypt through `secure_log`) as well as the CSV format.

## Phase 3: Firmware for the ESP32-S3 pods
New `firmware/knee_pod_s3/` (PlatformIO, `board = lolin_s3_mini`, custom `partitions.csv`: nvs, phy, a 3 MB factory app, and a small spiffs/coredump; no OTA because 4 MB is tight).
- `shank` env: reads the MPU6050 at 50 Hz through `esp_timer` and sends sequenced packets to the thigh pod over ESP-NOW with ESP-NOW encryption (LMK/PMK). The packet layout and sequence pairing are ported from `tech/iot/app/protocol.py`.
- `thigh` env:
  - reads its local IMU and merges it with shank packets by sequence number, flagging any loss;
  - keeps a PSRAM ring buffer and uses a FreeRTOS writer task for SD, which addresses the documented flush-stall issue;
  - writes AES-GCM chunks through mbedTLS (hardware AES);
  - runs the TFLM model on each 2 s window (`esp-tflite-micro` with ESP-NN) and logs an `INFER` record;
  - handles the button (cycle exercise, long press to start/stop), the RGB LED for status and the exercise colour, and battery level through an ADC divider.
- Reuses the MPU6050 configuration from `firmware/two_imu_logger/two_imu_logger.ino`: ±4 g, ±500 °/s, DLPF 10 Hz.
- Pins avoid the strapping pins (0, 3, 45, 46), USB (19, 20) and the PSRAM/flash pins. Each pin is checked against the LOLIN S3 Mini pinout and recorded in one `pins.h` and in the docs.
- Build output records firmware size, free heap and arena usage, and fills `configs/resource_measurements.example.json` so `check_resources` can run against real numbers once hardware exists.
- Hardware-in-loop steps (latency, parity against the Phase 1 vectors) are documented as a checklist. They can't be verified here without boards.

## Phase 4: Circuit diagram
- Use the text-to-cad **pcb** skill to build a KiCad schematic for each pod: `tech/electronics_s3/thigh_pod.kicad_sch` and `shank_pod.kicad_sch`. This is a wiring-level diagram of the module blocks, with no PCB layout. Run ERC and export PDF/SVG to `tech/electronics_s3/out/`.
- **Thigh pod:** LOLIN S3 Mini, GY-521 (I2C), microSD SPI breakout, TP4056 + DW01 (protected) charger module, 1S LiPo (about 500 mAh), slide switch, SS14 Schottky into the board's 5V/VBUS pin, 100k/100k battery divider to ADC, tactile button.
- **Shank pod:** the same parts minus the microSD and the button.
- A Markdown pin table and an ASCII/Mermaid block diagram go in `docs/HARDWARE.md`.
- Verify against the board's schematic: the LiPo → VBUS path through the on-board ME6211 LDO (brownout below about 3.4 V), and that no back-feed happens when USB and battery are both connected. Document the rule "charge via the TP4056 port".

## Phase 5: CAD for 3D printing
Use the text-to-cad **cad** skill to make parametric build123d sources in `tech/cad/` (finally giving the enclosures real source files):
- `thigh_pod_body.py` / `thigh_pod_lid.py`: stacked LOLIN S3 Mini, MPU6050, microSD breakout (card slot reachable from outside), TP4056 (USB-C port opening), LiPo bay, switch and button cutouts, light pipe for the RGB LED, M2 heat-set inserts or snap-fit lid, and a fixed IMU pocket with an axis arrow embossed on the lid.
- `shank_pod_body.py` / `shank_pod_lid.py`: smaller version of the above.
- `strap_mount.py`: keeps the existing magnetic plate concept (3× Ø6×1.5 mm N52 magnets in the pod, 3× Ø6×1.0 mm in the plate) and adds slots for 38 mm hook-and-loop elastic straps. The plate curves to fit the thigh or shin radius, with separate parameters for each.
- `assembly.py`: imports the component envelope models to check fit and clearances.
- Export STL, 3MF and STEP to `tech/cad/out/`. Run the skill's printability check: wall ≥ 1.6 mm, no unsupported overhangs > 45°, print orientation flat on the lid face, 0.2 mm fit clearances.
- Materials: pods in **PETG** (heat and sweat resistant, about 0.25 mm tolerance); strap mount in **TPU 95A** (flexible, comfortable on skin).
- Move the old `tech/designs/enclosure_*` and `mounting_plate*` to `tech/designs/legacy_c3/` and mention them in the docs. Delete the 227 MB `core.229531` dump only after you confirm.

## Phase 6: Materials list (BOM)
`docs/HARDWARE.md` plus a machine-readable `tech/BOM_S3.csv` (qty per kit, part, spec, example source, unit price estimate, notes):
- **Electronics:** 2× LOLIN S3 Mini; 2× GY-521 MPU6050; 1× microSD SPI module (3.3 V, no 5 V level shifter, or one that tolerates 3.3 V); 1× microSD card 8–32 GB, A1 class; 2× TP4056 USB-C + DW01 protection module; 2× 1S LiPo 500 mAh (503035, JST-PH); 2× SS12D00 slide switch; 1× 6×6 mm tactile button; 2× SS14 diode; 4× 100k resistors; silicone wire 26/28 AWG; JST-PH pigtails; heat-shrink.
- **Mechanical:** PETG and TPU 95A filament; 6× Ø6×1.5 mm and 6× Ø6×1.0 mm N52 magnets; M2×4 heat-set inserts and M2×6 screws; 38 mm elastic hook-and-loop strap (about 2 × 60 cm); 1 mm clear PETG/PMMA rod for the light pipe; foam pad or neoprene for skin contact.
- **Tools:** soldering iron, multimeter, USB-C cable, microSD reader.

## Phase 7: Website (journey frontend)
New `web/` (Vite + React + TypeScript; Recharts for charts; Dexie for IndexedDB; Vitest for tests; no backend).
- **Import:** pick `.kbx` files or the SD folder (File System Access API, with a fallback `<input multiple>`), enter the passphrase, then decrypt in a Web Worker (`crypto.subtle` PBKDF2 → AES-GCM, checking every chunk's tag). Show a clear error for a wrong passphrase or a corrupted chunk, and mark partially recovered sessions.
- **Parse and analyse** (`web/src/lib/`):
  - `kbxReader.ts`, mirroring `secure_log.py`.
  - `analysis.ts`: per-session duration, exercises done, share of correct-form windows, error-type breakdown, link-loss %, and an **estimated knee-angle range** from a thigh/shank complementary-filter tilt difference. It is clearly labelled as uncalibrated.
- **Pages:**
  - Dashboard ("Your journey"): sessions per week, streak, active minutes, correct-form trend per exercise, estimated range-of-motion trend, and milestones.
  - Sessions list.
  - Session detail: signal plot, timeline of window predictions, error breakdown.
  - Settings: forget passphrase, export decrypted CSV/JSON, delete all local data.
- **Privacy and safety:**
  - Decrypted data stays in IndexedDB on that browser only.
  - The passphrase is never stored; a derived key is optionally held for the session only.
  - A visible "research prototype, not medical advice" banner, consistent with the existing doc warnings.
- **Tests:** Vitest decrypts the Phase 2 fixtures and checks they match the Python output, plus unit tests for analysis. Scripts: `npm run dev/build/test`.

## Phase 8: Documentation updates
- `README.md`: new architecture overview (S3 wireless pods, encrypted logs, edge model, website), repo map, quickstart for each part, and the fixed `notebooks/` reference.
- `docs/TWO_STRAP_TEAM_GUIDE.md`: switch C3 and cable to S3 and ESP-NOW; new pin table, strap placement, button/LED usage, provisioning and passphrase steps; fix the stale `ibd_ml` path.
- `firmware/README.md`: the new PlatformIO project, partitions, build and flash, and the hardware-in-loop checklist. Mark `two_imu_logger` as legacy.
- `docs/TRAINING_RESULTS.md`: the Phase 1 edge benchmark tables and the chosen-model decision, or the documented shortfall.
- New docs: `docs/HARDWARE.md` (BOM, wiring, assembly and print settings) and `docs/SD_LOG_FORMAT.md`.
- `tech/designs/README_electronics.md`: legacy note pointing at `tech/electronics_s3/`. Add `web/README.md`.

## Suggested execution order
Phases 0 → 1 → 2 first: models plus the format contract, which can all be verified on this machine. Then 7 (website, using fixtures). Phases 4, 5 and 6 (diagram, CAD, BOM) can run alongside. Phase 3 firmware is compiled here but can only be fully validated on hardware. Phase 8 comes last, written from what was actually measured.

## Verification
- `PYTHONPATH=src python -m unittest discover -s tests -v` (all pass, including the new export, benchmark and secure_log tests). `pytest tech/iot/tests` still passes.
- `python -m knee_rehab.benchmark_edge` runs for all three exercises. The results table shows int8 LOSO accuracy and flash bytes, and the chosen `.tflite` is ≤ 64 KB, or the shortfall is documented.
- `python -m knee_rehab.secure_log make-fixtures` then a Python decrypt round trip; tampering with a byte fails the tag check.
- `cd web && npm test && npm run build`; `npm run dev`, import the fixture `.kbx` files with the passphrase, and check that the dashboard and session pages render (screenshots via the run skill or browser).
- `pio run -e thigh -e shank` compiles. The size report fits the 3 MB app partition, with the model embedded.
- KiCad ERC passes on both schematics; PDFs are exported.
- CAD: the build123d scripts build, the printability check passes, the assembly shows no interferences, and the STL/3MF files are exported.
- Hardware-only checks (latency, arena, parity on the device, battery life, ESP-NOW link loss) are listed in the firmware checklist to run when the boards arrive.
