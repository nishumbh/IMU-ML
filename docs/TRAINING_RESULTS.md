# Stored KneE-PAD reference models

## Scope

Completed offline RF baseline training, leave-one-subject-out (LOSO) evaluation,
final all-input fitting, persistence, and successful reload/prediction checks.
**No firmware, embedded integration, or quantization was performed.** These are
research reference models, not clinically validated models or a deployable
single-IMU brace solution.

Input: the local numeric release in `data/raw/dataset`, verified against
[Zenodo 12112951](https://doi.org/10.5281/zenodo.12112951) and
[the dataset paper](https://doi.org/10.1038/s41597-025-04963-4).
Separate models preserve each exercise's original two error types. All models
use **all eight fixed IMU placements / 48 channels**, converted to SI units.
No sEMG, subject attributes or label metadata are input features.

## Saved files

Paths relative to the project root:

| Exercise | Saved final model | Size (bytes) |
|---|---|---:|
| Seated leg extension | `artifacts/knee_pad_extension_rf_v1/final.joblib` | 522,474 |
| Squat | `artifacts/knee_pad_squat_rf_v1/final.joblib` | 584,570 |
| Gait | `artifacts/knee_pad_gait_rf_v1/final.joblib` | 532,170 |

Keep the **entire directory**, not just `final.joblib`: inference also needs
saved preprocessing, feature settings and class/channel mappings. Each directory
includes `metadata.json`, `feature_names.json`, `feature_config.json`,
`cv_results.json` and the input manifest snapshot. Metadata includes package
versions, the original manifest path/hash and selected source file SHA-256s.
Only load trusted joblib artifacts using compatible package versions.

The local backup bundle is `artifacts/knee_pad_models_v1.zip`; it contains all
three model directories plus this report. `artifacts/knee_pad_models_v1.sha256`
records the archive checksum. Both are gitignored, as are the model directories
and generated manifests: **back them up separately** if preserving the models
outside this checkout. Raw source data is not included in the archive.

## Evaluation results

Default preprocessing: 148.14814814814815 Hz input → 50 Hz polyphase resampling,
causal fourth-order 10 Hz low-pass, full two-second windows with 50% overlap,
and 14 features/channel (672 total). RF: 64 trees, maximum depth 6, minimum leaf
size 2, balanced class weights, seed 42. No parameter search was performed.

| Exercise | Participants / LOSO folds | Manifest trials | Trials yielding windows | Windows | Raw pooled accuracy | Raw false acceptance |
|---|---:|---:|---:|---:|---:|---:|
| Seated leg extension | 29 | 914 | 914 | 1,899 | 75.83% | 142 / 1,266 = 11.22% |
| Squat | 31 | 1,075 | 1,073 | 2,562 | 56.36% | 447 / 1,335 = 33.48% |
| Gait | 28 | 95 | 95 | 1,156 | 58.74% | 141 / 830 = 16.99% |

False acceptance is an actual incorrect-form window predicted `Correct Form`,
divided by all actual incorrect-form windows. Accuracy is pooled **window-level**
accuracy, not independent-patient accuracy. Missing participant activities are
expected in this release; no missing classes or trials were fabricated.

### Illustrative 0.9 threshold — not a usable validated setting

The stored conservative policy rejects a winning `Correct Form` prediction
whose correct-class probability is below 0.9. It never promotes another class.

| Exercise | Conservative false acceptance | Correct-form recall | Conservative accuracy (rejections count as wrong) |
|---|---:|---:|---:|
| Seated leg extension | 0 / 1,266 = 0.00% | **0.00%** | 51.18% |
| Squat | 0 / 1,335 = 0.00% | **0.00%** | 27.01% |
| Gait | 2 / 830 = 0.24% | 17.79% | 42.30% |

**Zero false acceptance here does not establish useful or safe performance:**
the extension and squat models accept no correct-form windows at this
threshold. The threshold was retained as the scaffold's illustrative,
prespecified setting, not tuned on held-out subjects. Choosing a useful operating
point needs inner subject-disjoint validation plus a separate outer/prospective
evaluation. Unknown movement can still be falsely accepted. Squat/gait raw
performance is modest and needs further research, not deployment.

## Data exclusions

The 2,086 source trials contain two empty IMU arrays, explicitly excluded from
the gait manifest using `--skip-empty`:

- `Subject_18/6/Trial_1/imu.npy` — `[48, 0]`;
- `Subject_27/6/Trial_1/imu.npy` — `[48, 0]`.

The squat manifest retains two short trials that preprocessing warns about and
skips because they cannot produce a two-second window:

- `Subject_10/0/Trial_1/imu.npy` — 222 samples, 1.4985 seconds;
- `Subject_18/0/Trial_6/imu.npy` — 222 samples, 1.4985 seconds.

Their IDs appear in `metadata.json` under `skipped_recording_ids`; each recording
also has its output window count. Raw files were not changed.

## Verification and reuse

All three saved pipelines were reloaded and produced valid class probabilities
for all their usable windows: 1,899 extension, 2,562 squat and 1,156 gait. This is
**persistence verification, not new evaluation**: final artifacts are fitted on
all available subjects. The LOSO results come from independently fitted folds.

```bash
# From the project root, with the package installed or PYTHONPATH=src:
PYTHONPATH=src python -m unittest discover -s tests -v

PYTHONPATH=src python -m knee_rehab.predict \
  --model-dir artifacts/knee_pad_extension_rf_v1 \
  --manifest data/interim/knee_pad_extension_all_imus.json \
  --output artifacts/new_extension_predictions.json
```

The predictor reuses saved DSP/features and refuses incompatible ordered channel
names, inconsistent feature mappings and output overwrites. Input labels are
required by the current manifest schema but are not model inputs. Exercise type
is assumed known, not inferred. `README.md` documents manifest generation and
retraining, including explicit fixed sensor selection for future reduced-sensor
experiments. Embedded adaptation remains deferred.
