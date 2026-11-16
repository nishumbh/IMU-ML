# Dataset integration contract

No dataset is downloaded, redistributed, or assumed to have a universal column
layout. The files in `configs/` are **mapping templates, not verified source
schemas**: even the example 100 Hz rate, column numbers, units, sensor placement,
and segment bounds must be replaced from the release documentation. This
repository has not been validated against the actual releases.

## Sources and intended use

| Source | Loader input | Intended use / important constraint |
|---|---|---|
| KneE-PAD, article PMC11992047 | Numeric 2-D `.npy`, explicit time axis and IMU column indices | Primary supervised form assessment. Recover actual participant, exercise, trial and error annotations from release metadata. |
| BLISS | CSV tables or a documented conversion to CSV | Walking robustness/pretraining. Impairment/able-bodied status is NOT a correct/incorrect exercise label. |
| Intelligent Knee Sleeves (NeurIPS) | CSV tables or a documented conversion to CSV | Representation pretraining / motion context only unless compatible form labels exist. Exclude pressure and magnetometer channels; use only accelerometer and gyro. |
| Mendeley Sensor-based Knee Movement Tracking | CSV tables | Kinematic robustness/pretraining. Stairs or walking speed are activity labels, not form-quality ground truth. |

A common CSV adapter is used for all three supplementary sources; different
column names, separators and rates are configuration, not guessed per-dataset
heuristics. Native MAT/HDF5 or object/nested NPY releases require a separately
verified conversion. `np.load(..., allow_pickle=False)` is intentional.

### Safe integration workflow

1. Check dataset license, consent restrictions and release version. Keep raw data
   immutable and record checksums, citation/version and conversion history.
2. Inspect real file shapes, annotation files, rate/time units, accelerometer and
   gyro units, sensor side/placement and frame conventions. Do not select channels
   just because they have the same count as the target hardware.
3. Produce a JSON manifest (`records` list). One record is a single participant,
   constant-label contiguous segment of one trial. `sample_start` is inclusive;
   `sample_stop` is exclusive. Exclude transition/ambiguous annotation intervals
   rather than joining them or assigning a majority label. Segment a trial at
   every label transition; never concatenate intervals separated by gaps.
4. Use globally unique participant identifiers, e.g. `knee_pad:P001`. All sessions
   from one person must share the same ID, including across releases if they are
   known to be the same person. Do not namespace a known shared person into
   separate folds. A multi-subject source file must be split into per-person
   files first. Copies of one subject under different file names cannot be
   detected automatically; audit provenance.
5. Select **only** accelerometer and gyroscope channels with `kind`, input `unit`,
   and semantic `name` for each. Output units are m/s² and rad/s. Input `scale`
   and `offset` are optional raw calibration parameters applied before SI unit
   conversion. They are not an orientation transform. Full frame rotations,
   side mirroring and mounting calibration must be explicitly applied upstream,
   recorded, and reproduced on the hardware. Never fabricate a missing sensor
   with zero channels to make incompatible placements fit the same manifest.
6. Match the same ordered physical channels across subjects/datasets. A thigh-only
   six-channel IMU model cannot silently consume shank-only channels, bilateral
   channels, or knee-angle ground truth computed from more sensors than the
   prototype. Whether one IMU is sufficient for a given compensation is a study
   question, not solved by a classifier.
7. Preserve original labels and exercise in `metadata`. Have a qualified domain
   expert approve any many-to-one form mapping. Lack of **extension** is not
   incomplete **flexion**. Preserve `Incomplete Extension` as its own class if
   present, or use an explicitly reviewed alternative taxonomy. The example
   three-class config is not a claim about any release's annotations.
8. Do not add supplementary unassessed gait to supervised form training. Mark it
   with `metadata.usage = "pretraining_only"` and `label = "Unassessed Gait"` in a
   separate manifest. The training CLI rejects these. The loaders make such data
   accessible; a masked-reconstruction/contrastive pretraining stage and domain
   adaptation are **not implemented** in this initial scaffold. If adding them,
   exclude outer LOSO participants from all pretraining, augmentation fitting,
   normalization, threshold selection and calibration.

### Manifest fields

Required per record: `path`, `dataset`, `recording_id`, `subject_id`, `label`,
`sample_rate_hz`, and `channels` (list of mappings). Each channel specifies `name`,
`column` (integer position or CSV header), `kind` (`accelerometer`/`gyroscope`),
and `unit` (`m/s^2`/`g` or `rad/s`/`deg/s`). Every record must contain both kinds.
Channel names must be unique and ordered identically across records.

Optional: `format` (`npy`/`csv`, inferred from extension), `time_axis` (NPY only,
0 by default), `sample_start`, `sample_stop`, `metadata`, `csv_options`
(`sep`, `header`, `skiprows`, `encoding`). Headerless CSV uses `"header": null`
and numeric channel indices. Numeric timestamps may be checked using
`"timestamps": {"column": "time", "unit": "ms", "relative_tolerance": 0.05}`.
The tolerance is a per-interval relative error bound around the declared rate.
Timestamps must be strictly increasing. Irregular clocks/missing samples are
rejected, not silently regridded or dropped. If needed, implement gap-aware
regridding before this loader, with a documented maximum interpolated gap.

Top-level optional `channel_names` asserts the layout; `allowed_labels` asserts
the chosen taxonomy. Relative source paths resolve from the manifest directory.
No sEMG, pressure or unselected channels enter the arrays or model.
