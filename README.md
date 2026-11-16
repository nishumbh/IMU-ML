# Knee rehabilitation: IMU-only ML scaffold

End-to-end **research scaffold** for an ESP32 knee-brace prototype. The primary
supervised source is KneE-PAD; supplementary CSV data can be ingested using
explicit mappings. The initial `data_loader.py` and `preprocess.py` are fully
implemented, with runnable baselines, an optional compact CNN and int8 export.
This is not a trained clinical model or a safety-certified device.

**Important limits:** source dataset releases have not been downloaded or their
schemas verified. Manifest templates require real channel/rate/annotation
mappings. Supplementary pretraining/domain adaptation is not implemented. Flash
blob size is checked; actual ESP32 RAM, operator support, latency and quantized
accuracy require measurements on your exact firmware. TensorFlow is optional and
was not available in the initial development environment.

## Directory layout

```text
.
├── README.md
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── manifest.example.json               # Numeric KneE-PAD NPY mapping template
│   ├── manifest.csv.example.json           # Supplementary CSV mapping template
│   └── resource_measurements.example.json  # Null/unverified: intentionally fails gate
├── src/knee_rehab/
│   ├── __init__.py
│   ├── data_loader.py                      # Explicit, SI-unit IMU-only NPY/CSV ingestion
│   ├── preprocess.py                       # Resampling, causal DSP, windows, cheap features
│   ├── baseline.py                         # Bounded RF and scaled RBF SVM; LOSO
│   ├── models.py                           # Compact temporal CNN; fold-local channel scaling
│   ├── evaluate.py                         # Fixed confusion matrices + false acceptance
│   ├── export.py                           # Training-calibrated full-int8 TFLite export
│   ├── check_resources.py                  # Fail-closed measured-resource gate
│   └── train.py                            # CLI + artifacts/provenance
├── scripts/create_demo_data.py             # SYNTHETIC mixed-format smoke test only
├── tests/
│   ├── test_data_loader.py
│   ├── test_preprocess.py
│   ├── test_evaluation.py
│   ├── test_resources.py
│   └── test_export.py                      # Skipped without TensorFlow
├── docs/DATASETS.md                        # Mapping, alignment, labels, integration rules
├── firmware/README.md                      # MCU numerical parity and memory acceptance
├── data/
│   ├── raw/                               # External, untracked, immutable sources
│   ├── interim/                           # Verified conversions/alignment
│   └── processed/                         # Optional cached windows/features
├── artifacts/                             # One new/empty directory per training run
└── notebooks/                             # Optional exploratory analysis
```

## Install and run

Python 3.10–3.13; Python 3.11/3.12 is a practical choice for TensorFlow wheel
availability on supported platforms.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .

# No dataset credentials required: SYNTHETIC pipeline smoke test only
python scripts/create_demo_data.py
python -m knee_rehab.train \
  --manifest data/demo_manifest.json --output-dir artifacts/demo_rf \
  --model rf --correct-threshold 0.9

python -m knee_rehab.train \
  --manifest data/demo_manifest.json --output-dir artifacts/demo_svm \
  --model svm --correct-threshold 0.9

# Tests work with the standard library, without pytest
PYTHONPATH=src python -m unittest discover -s tests -v
# Or: pip install -e '.[dev]' && pytest
```

Synthetic results test plumbing, **not** rehabilitation accuracy. The demo has
four fake participants and three deliberately separable simulated classes. Use
a distinct empty output directory for every run to prevent stale model/report
mixups. Recent scikit-learn versions may warn that SVC's probability API is
being deprecated; this baseline currently uses that API.

For real data, copy a manifest template, replace all placeholders from the
actual release and follow [docs/DATASETS.md](docs/DATASETS.md). Then:

```bash
python -m knee_rehab.train \
  --manifest configs/my_verified_knee_pad.json \
  --output-dir artifacts/knee_pad_rf_v1 --model rf \
  --target-rate-hz 50 --cutoff-hz 10 --filter-order 4 \
  --window-seconds 2 --overlap 0.5 --correct-threshold 0.9
```

`0.9` is an **illustrative prespecified threshold**, not a validated safety
setting. Estimate a clinically acceptable operating point using inner,
subject-disjoint validation and a separate outer/prospective evaluation. Do not
tune a threshold on the held-out participant.

## Data loading and preprocessing APIs

```python
from knee_rehab.data_loader import load_manifest
from knee_rehab.preprocess import PreprocessConfig, prepare_windows, extract_features

recordings = load_manifest("configs/my_verified_knee_pad.json")
batch = prepare_windows(recordings, PreprocessConfig(
    target_rate_hz=50, cutoff_hz=10, filter_order=4,
    window_seconds=2, overlap=0.5,
))
features, names = extract_features(batch.X, batch.sample_rate_hz)
# batch.X: float32 [windows, 100 samples, channels]
# batch.y / batch.subject_ids / batch.recording_ids: one value per window
# Six IMU channels -> features.shape == (windows, 84)
```

Each manifest record is one contiguous, constant-label, single-subject trial
segment. Paths are relative to the manifest. Only selected acceleration and
rotation channels are read into model inputs; sEMG, pressure, magnetometer,
subject information and labels never become features. Subject information is
retained only for grouping/evaluation. Acceleration/rotation are converted to
m/s² and rad/s. Missing/invalid readings and irregular timestamps are rejected
rather than silently dropping rows or mixing samples across gaps.

Processing order per record:

1. `resample_poly`: rational polyphase conversion, including anti-aliasing for
   downsampling. This is an **offline, noncausal** FIR. It is not the MCU's causal
   Butterworth filter. Acquire directly at the target rate on-device, or validate
   a streaming rate converter with the same effective transfer function/delay.
2. Butterworth low-pass using **causal** second-order sections (`sosfilt`), not
   `filtfilt`. Initialize using `sosfilt_zi * first_channel_value`. Retain state
   across windows; reset only at stream/segment boundaries. Audit startup effects
   and transition latency before deploying.
3. Full, unpadded fixed windows. Default: 100 samples and 50-sample hop at 50 Hz.
   Hop is `round(window_samples * (1-overlap))`, so arbitrary requested overlap is
   sample-rounded. Trailing partial windows are discarded; too-short records are
   reported and skipped. Windows never cross label, subject or recording bounds.

Fixed windows are **not automatically discrete repetitions**. Repetition onset,
exercise recognition, a resting/unknown state and temporal decision smoothing
require separately validated logic. No augmentation or learned preprocessing
runs before LOSO splitting.

### Features that can be ported to C/C++

Per channel, in this order:

- Mean, population variance, standard deviation, population skewness, demeaned
  zero-crossing rate, minimum, maximum, RMS and peak-to-peak amplitude.
- Demeaned rectangular-window Goertzel power at 0.5, 1, 2, 3 and 5 Hz, normalized
  by `N*N`. These are **frequency probes**, not broadband energy or a full FFT.

14 features/channel, channel-major. Mean/variance/skewness use finite sums;
constants produce zero skewness and zero ZCR. A standard-deviation floor of
`1e-8` stabilizes skewness. ZCR treats exactly zero as nonnegative and divides by
`N-1`. Goertzel uses precomputed `2*cos(2*pi*f/fs)` coefficients and only basic
addition/multiplication per sample. Square root is needed for std/RMS. There is
no runtime SciPy/stats/FFT dependency in the algorithm that must be ported.
Change the function's probe frequencies for low-rate applications; the CLI's
default probes require a target rate strictly above 10 Hz.

## Training and evaluation

- **RF:** 64 trees, depth capped at 6, minimum leaf size 2, balanced class weights.
  A baseline for comparison, not automatically an MCU-compatible executable.
- **SVM:** RBF SVC with `StandardScaler` inside each fold's pipeline and balanced
  class weights. Support-vector size can grow; no ESP32 size promise. Internal
  SVC probability calibration uses training-fold data, not the held-out subject;
  it is not a substitute for subject-grouped clinical threshold validation.
- **CNN:** two temporal convolution blocks (8 and 12 filters), two max pools,
  flatten and softmax dense output. Expressed as `(k,1)` Conv2D for TFLM-friendly
  operator support. No LSTM, batch norm, dropout or embedded floating normalizer.
  Per-channel means/scales are fitted on training folds only. Fixed epochs avoid
  selecting epochs using the outer held-out participant.

LOSO holds out **all windows/sessions of each actual person**. Dataset-specific
participant IDs must be globally unambiguous. Each fold must have at least two
training classes; missing other classes are reported, with a fixed label order
and zero-support-safe metrics. Class names are manifest driven: preserve
`Incomplete Extension` separately from `Incomplete Flexion` when appropriate.
Unassessed or pretraining-only supplementary records are rejected by the
supervised training CLI.

`cv_results.json` contains per-subject and pooled confusion matrices (rows=true,
columns=predicted), per-class precision/recall/F1 and safety metrics:

```text
false acceptance rate = actual incorrect windows predicted Correct Form
                       / all actual incorrect windows
```

If there are no incorrect examples the rate is `null`, not a misleading zero.
With `--correct-threshold`, a winning correct-class prediction below the
threshold becomes `__REJECT__`; no other class is promoted to Correct Form.
Both raw and conservative reports are retained. Unknown real-world movements
can still be falsely accepted: confidence thresholding is **not OOD detection**.

Never randomly split overlapping windows into train/test. Windows from one
participant are correlated: a large window count is not a large independent
sample size. Per-subject results should drive failure analysis. Comparing many
models/thresholds on the same LOSO folds and reporting the winner's LOSO score
as unbiased is invalid; add nested grouped selection or an independent
prospective cohort. For multiple exercise types, also stratify errors by exercise
and prevent exercise/dataset identity from becoming a proxy for form.

After CV, the CLI fits a final model on **all supplied participants** for export.
That fit is explicitly marked non-evaluation. Artifacts include the manifest
snapshot/hash, class/channel mapping, preprocessing coefficients and metadata,
CV results and `final.joblib` or `final.keras`. Do not load joblib artifacts from
untrusted sources. Source data checksums/environment lockfiles should be added
for a regulated/reproducible study.

## Optional CNN and int8 edge export

```bash
pip install -e '.[train]'
python -m knee_rehab.train \
  --manifest configs/my_verified_knee_pad.json \
  --output-dir artifacts/knee_pad_cnn_v1 --model cnn \
  --epochs 15 --batch-size 32 --correct-threshold 0.9 \
  --max-flash-bytes 262144 --calibration-windows 256
```

This trains all LOSO folds plus the final all-input model, then writes:

- `final.keras`, `scaling.json`, `metadata.json`, `cv_results.json`;
- `final.int8.tflite` with **int8 input/output** and integer-only converter ops;
- `final.int8.tflite.deployment.json`, including model bytes/hash,
  quantization parameters, operators and explicit unverified deployment status.

The 256 KiB model-only flash cap is an **example reservation**, not the usable
flash of every ESP32. Override it using your partition and firmware budgets.
Exceeding it fails export without writing the new `.tflite` file. Representative
samples are drawn only from the model's training data. In the final all-input
fit, all supplied subjects are training subjects; the export has no remaining
independent validation set. To quantize inside LOSO, call `export_int8` with only
that fold's training windows and assess the resulting artifact on that fold's
held-out subject. That quantized LOSO loop is not implemented here.

Current CV reports describe **float CNN predictions**, not int8 performance.
Quantized accuracy, especially false acceptance, must be tested before use.
Conversion and CNN runtime were not exercised in the initial environment
because TensorFlow was absent; `tests/test_export.py` runs conversion when the
extra is installed. See [firmware/README.md](firmware/README.md) for the strict
resource gate and required target measurements.
