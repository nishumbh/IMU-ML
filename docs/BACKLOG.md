# Backlog

Tickets for the knee-rehab project. Status: **Done**, **Partial**, **Todo**, **Blocked**, **In progress**.
Size: **S** under 1 day, **M** 1–3 days, **L** about a week. Sprint numbers are in `docs/SPRINT_PLAN.md`.
Tickets KB-001 to KB-013 record work completed on 2026-11-22/23 so the history is traceable.

## Completed (2026-11-22/23)

| ID | Title | Area | Status | Notes |
|---|---|---|---|---|
| KB-001 | Python 3.12 venv with TensorFlow 2.21, emlearn, cryptography; baseline 66 tests pass | Env | Done | `.venv/` (gitignored) |
| KB-002 | Edge benchmark, extension (two-IMU), LOSO: RF/ET/CNN, int8 | ML | Partial | Extension only, see KB-016. Best deployable RF (32 trees) is 58 KB flash, 65% accuracy, 26% false-accept. None met the selection rule. `artifacts/edge_extension/` |
| KB-003 | Encrypted `.kbx` SD log format: spec, Python reader/writer, CLI, fixtures, review page | Data | Done | `docs/SD_LOG_FORMAT.md`, `src/knee_rehab/secure_log.py`, `tests/fixtures/kbx/`. 106+ tests pass |
| KB-004 | Journey website (React/Vite/TS): in-browser decrypt, dashboard, sessions, import, settings | Web | Done | 33 tests, typecheck, build pass. Not yet tested in a real browser (KB-027) |
| KB-005 | S3 pod firmware scaffold (PlatformIO, partitions, pins, pod_core, provisioning tool) | Firmware | Partial | Shank env builds. Thigh env not built (KB-014). Inference is a stub |
| KB-006 | Circuit schematics for thigh and shank pods (KiCad 9), ERC clean, PDFs exported | HW | Done | `tech/electronics_s3/`. Design issues open (KB-035) |
| KB-007 | Materials list (BOM) and hardware guide | HW | Done | `tech/BOM_S3.csv`, `docs/HARDWARE.md`. Verify-before-ordering list open (KB-036) |
| KB-008 | Parametric CAD for pods, lids, curved magnetic plates; STEP/STL/3MF export; print notes | CAD | Done | `tech/cad/`. Dimensions are placeholders (KB-037) |
| KB-009 | Knee angle estimator in Python (complementary filter, standing-zero, angular speed) + tests | ML/Angle | Done | `src/knee_rehab/knee_angle.py`, `tests/test_knee_angle.py`. Checked on simulated rigid-body motion only |
| KB-010 | Web angle fix: accelerometer tilt `atan2(-ax, az)`, signed flexion zeroed on standing period | Web | Done | Matches Python convention. Needs real-data check (KB-020) |
| KB-011 | C tilt filter, gap reset and flexion zeroing in pod_core + host tests (84 checks) | Firmware | Done | `firmware/knee_pod_s3/lib/pod_core/`. Not yet wired into the thigh loop (KB-023) |
| KB-012 | Dependency split: cryptography in core requirements, emlearn in `edge` extra | Env | Done | `requirements.txt`, `pyproject.toml` |
| KB-013 | Project plan written | Docs | Done | `plan.md` (original 8-phase plan) |

## Open

### Angle validation and data

| ID | Title | Size | Priority | Depends on | Acceptance criteria |
|---|---|---|---|---|---|
| KB-014 | Build the thigh firmware env; record flash, RAM and PSRAM in `BUILD_SIZES.md` | S | High | - | `pio run -e thigh` succeeds; sizes recorded; shank sizes checked against the same file |
| KB-015 | Confirm `firmware/knee_pod_s3` state after the stopped firmware agent (review partial work, remove stale build output) | S | Medium | - | Written note of what is done and what is stubbed; `.pio/` stays gitignored |
| KB-016 | Run the edge benchmark for squat and gait (two-IMU) | M | Medium | - | `artifacts/edge_squat/`, `artifacts/edge_gait/` with `comparison.md`; table added to TRAINING_RESULTS |
| KB-017 | Find out what created the untracked `tmp/` folder at the repo root; remove or gitignore it | XS | Low | - | Folder explained or removed |
| KB-018 | Download GAITEX (17.7 GB, Zenodo 15729055) and verify the checksum | M | High | - | `data.zip` complete, checksum recorded. In progress at 540 MB |
| KB-019 | Extract the GAITEX knee-angle reference (OpenSim IK `.mot`) and quality labels; map exercise codes (rd, rgs, ng, gwo) to our squat, extension and gait classes | M | High | KB-018 | Table of mapping with evidence from the metadata files; subjects with missing IK listed |
| KB-020 | Validate the angle estimator against the GAITEX reference: simulate strap-style accel and gyro from the quaternions, run `knee_angle`, report RMS error, sign and bias | L | High | KB-019 | Error table per exercise; sign and flexion-axis conclusion written down |
| KB-021 | Document the flexion-axis and mounting convention for the straps | S | High | KB-020 | Convention in `docs/TWO_STRAP_TEAM_GUIDE.md`; `knee_angle` defaults checked |
| KB-029 | Angle-feature extraction in Python (peak, range, mean and peak speed, time to peak) | M | Medium | KB-020 | Function plus tests; features reproducible in C |

### Firmware and format integration

| ID | Title | Size | Priority | Depends on | Acceptance criteria |
|---|---|---|---|---|---|
| KB-022 | Add an ANGLE record to the `.kbx` spec (v1.1), with Python tests and updated fixtures | M | High | KB-020 | Spec updated; old files still read; new fixtures pass |
| KB-023 | Wire pod_core angle filter into the thigh loop; log ANGLE records | M | High | KB-014, KB-022 | Host test passes; device build succeeds; angle logged per sample |
| KB-024 | Use one zeroing rule in Python and C (median or mean) | S | Medium | - | Both use the same rule and a test checks they agree |
| KB-025 | Shared test vectors: C and Python angle outputs must match | S | Medium | KB-024 | Vector file generated from Python; C host test checks against it |
| KB-026 | Website reads ANGLE records when present; keeps the estimate otherwise | M | Medium | KB-022 | Vitest covers both cases |
| KB-027 | Browser test of the website: import fixtures, check each page, take screenshots | M | Medium | - | Pages load without console errors; screenshots saved |
| KB-033 | Firmware inference: model header and INFER integration; parity with Python vectors | L | Medium | KB-032 | Device output matches Python within tolerance on the test vectors |

### Models

| ID | Title | Size | Priority | Depends on | Acceptance criteria |
|---|---|---|---|---|---|
| KB-030 | Train leave-one-subject-out classifiers on angle features (GAITEX-derived), per exercise | L | High | KB-020, KB-029 | `artifacts/gaitex_angle_*`; LOSO table with accuracy, macro-F1, false-accept |
| KB-031 | Compare with the KneE-PAD baseline; document the result | S | High | KB-030 | Section in TRAINING_RESULTS with the caveat that placement and sensors differ |
| KB-032 | Quantize the chosen model (int8) and export C; check flash fits 3 MB app partition | M | Medium | KB-030 | `.tflite` plus C header; size recorded; parity vectors written |
| KB-034 | Decide whether to keep the KneE-PAD models in the project or mark them as research only | S | Low | KB-031 | Decision recorded in README |

### Hardware, mechanical and docs

| ID | Title | Size | Priority | Depends on | Acceptance criteria |
|---|---|---|---|---|---|
| KB-035 | Fix circuit issues: 4 Schottky diodes not 2 (battery and USB paths); recheck brownout (about 3.7 V under load); check TP4056 charge current (PROG resistor); update BOM | M | High | - | ERC clean; BOM and HARDWARE.md updated; power path reviewed |
| KB-036 | Verify-before-ordering list: LOLIN 5V and LDO behaviour, GY-521 pull-ups and 3.3 V, microSD 3.3 V compatibility, protected 503035 cell size, TP4056 pad labels | S | High | - | Each item checked against a datasheet or a bench test; results recorded |
| KB-037 | Calipers measurements for all VERIFY values in `tech/cad/common.py`; set plate chord and width; test pod fit on the curved plate | M | High | Hardware on hand | No VERIFY placeholders left; fit check passes |
| KB-038 | Run the CAD skill's DFM and interference checks (needs the cad server) | S | Medium | - | Check output saved |
| KB-039 | Legacy cleanup: move the C3 enclosure and KiCad files out of the main path; decide on `core.229531` (217 MB core dump) and the `.ki11.bak` files | S | Low | User approval | Only after you confirm each deletion |
| KB-040 | Phase 8 docs: README architecture, team guide (S3, ESP-NOW, no cable, provisioning), firmware README, TRAINING_RESULTS edge section; fix stale `/home/nish/dev/projects/ibd_ml` paths | M | Medium | Sprints 1–3 | Docs match the code; `grep ibd_ml` returns nothing |
| KB-041 | Hardware-in-loop checklist: latency, parity, link loss, power loss mid-write, full SD card, battery life | L | High | Boards (KB-046) | Results recorded in `firmware/knee_pod_s3/README.md` |
| KB-042 | Ethics and data protection: university ethics approval and participant consent before any human data collection | M | Blocker | - | Approval reference on file. Needed before KB-043 |
| KB-043 | Record our own labelled sessions with a reference angle (video with markers, or a goniometer later) | L | High | KB-041, KB-042 | Reference data stored, with consent. Used for validation and training |
| KB-044 | Repo hygiene: permission-only changes (about 1,680 files show as modified; `git config core.fileMode false`); decide how to commit this work | S | Medium | Your decision | Clean `git status`; commits agreed with you |
| KB-045 | Security review: key stored unencrypted in NVS, option to enable ESP32 flash encryption, passphrase policy | M | Medium | - | Written findings; decision on flash encryption |
| KB-046 | Order boards and parts from BOM_S3 (after KB-036) | S | High | KB-036 | Parts received |

## Legacy (C3 design)

Not in active development. Listed so nothing is lost.

- KiCad board `tech/designs/board.kicad_pcb`: 22 footprints, no tracks, R2 shorted (both pins on net LED), U4 footprint mismatch (SOT-23-5 vs SOT-89), strapping pins IO8/IO9 used for SD.
- The MicroPython `tech/iot/` protocol is the reference for ESP-NOW. Its SD storage is unencrypted.
