# Sprint plan

Sprints are two weeks. Dates are left open because the start date is yours to set. Each sprint lists its goal, its tickets (see `docs/BACKLOG.md`) and its exit criteria. Ordering follows the dependencies in the backlog.

## Ground rules

- **Hardware-dependent work** (KB-041, KB-043) waits for the boards and for ethics approval (KB-042). Do not record human data before KB-042 is done.
- **No commits** happen without your approval (KB-044).
- **Measured results only.** Anything not run on hardware or on real data is marked as unverified in the docs.

## Sprint 1 — Validate what we have

**Goal:** find out whether the angle estimate is right on real movement, and close the open build and benchmark gaps.

Tickets: KB-014, KB-015, KB-016, KB-017, KB-018, KB-019, KB-020, KB-021

Order:
1. KB-018 (download) runs in the background from the start.
2. KB-014 and KB-015 (firmware build check) and KB-016 (edge benchmark) run alongside it.
3. KB-019 then KB-020 (reference extraction and angle validation) once the download finishes.
4. KB-021 writes down the flexion-axis convention from the KB-020 results.

**Exit criteria:**
- Thigh and shank firmware both build, with sizes recorded.
- Squat and gait edge results exist.
- GAITEX angle error is reported per exercise, with the sign and flexion-axis conclusion written down.

**Risk:** if the estimate is off by a sign or an axis, KB-021 becomes the critical path. The fix is a config change (`flexionSign`, `gyroSign`), but it has to be measured, not assumed.

## Sprint 2 — Firmware and format integration

**Goal:** the thigh pod computes and logs the knee angle on the device, using the same maths as Python.

Tickets: KB-022, KB-023, KB-024, KB-025, KB-026, KB-027

Order:
1. KB-024 (one zeroing rule) first, because KB-025 depends on it.
2. KB-022 (spec v1.1) before KB-023 and KB-026.
3. KB-023 (device wiring) and KB-025 (parity vectors) in parallel.
4. KB-027 (browser test) at the end, against the new fixtures.

**Exit criteria:**
- The ANGLE record is in the spec, and old `.kbx` files still read.
- Host C and Python outputs match on the shared vectors.
- The website shows the logged angle, and the browser test passes.

## Sprint 3 — Models

**Goal:** train angle-based classifiers, compare them with the KneE-PAD baseline, and produce a model that fits the ESP32-S3.

Tickets: KB-029, KB-030, KB-031, KB-032, KB-033, KB-034

Order:
1. KB-029 (angle features) first.
2. KB-030 (LOSO training on GAITEX-derived features).
3. KB-031 (comparison) and KB-032 (int8 and C export) together.
4. KB-033 (device inference and parity) last, because it needs KB-032.

**Exit criteria:**
- LOSO results per exercise, with the KneE-PAD comparison written up.
- Quantized model under the flash budget, with parity vectors.
- Firmware inference matches Python on the vectors, or the gap is documented.

**Note:** GAITEX uses different sensors and placement from ours, and the models are trained on simulated strap signals. Expect the real-strap accuracy to be lower until we have our own labelled data (KB-043).

## Sprint 4 — Hardware, mechanics and documentation

**Goal:** the hardware design is fixed and orderable, and the docs match the code.

Tickets: KB-035, KB-036, KB-037, KB-038, KB-040, KB-044, KB-045, KB-046

Order:
1. KB-035 and KB-036 (circuit fixes and verify list) first, then KB-046 (ordering).
2. KB-037 and KB-038 (CAD measurements and checks) once parts are on hand.
3. KB-040 (docs) last, so it describes the final state.
4. KB-044 and KB-045 (hygiene and security) can run any time.

**Exit criteria:**
- Circuit ERC and BOM agree, and the verify list is closed.
- No VERIFY placeholders remain in the CAD source.
- No stale paths in the docs.

## After Sprint 4 — Data and validation (no fixed sprint)

These depend on people and equipment outside the code:

- KB-042 ethics and consent: start early, because it can take weeks. Its lead time is the main schedule risk.
- KB-041 hardware-in-loop: needs boards from KB-046.
- KB-043 own labelled data: needs KB-041 and KB-042.
- KB-039 legacy cleanup: when you approve.

## Summary

| Sprint | Focus | Tickets | Key output |
|---|---|---|---|
| 1 | Validate | 8 | Angle error on real movement |
| 2 | Integrate | 6 | Device computes and logs angle |
| 3 | Models | 6 | Angle classifiers and int8 model |
| 4 | Hardware and docs | 8 | Orderable design, docs match code |
| After | Data | 4 | Own labelled sessions |
