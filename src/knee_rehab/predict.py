"""Offline saved-model inference, not clinical advice or validation.

Run: python -m knee_rehab.predict --model-dir artifacts --manifest manifest.json
    --output predictions.json

Load only trusted model directories: joblib artifacts can execute Python code.
Manifests use the existing loader schema (including a label, which is ignored for
prediction). No estimator, scaler, threshold, or preprocessing setting is fitted.

Encrypted ESP32 session logs can be used directly: a manifest record with
"format": "kbx" and "passphrase_env": "<ENV_VAR_NAME>" is decrypted by data_loader
through secure_log (the passphrase is read from that environment variable and is
never stored in the manifest). Truncated files are used up to the last complete
chunk; the record metadata then has kbx_truncated = true.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from .baseline import aligned_probabilities
from .data_loader import load_manifest
from .evaluate import predict_labels
from .preprocess import PreprocessConfig, extract_features, prepare_windows


INFERENCE_LIMITATIONS = (
    "Offline window-level model output, not clinical advice. No out-of-distribution "
    "guarantee; unknown/unsafe motion can still be falsely accepted. Inference on "
    "training data is not validation; these predictions are not evaluation metrics."
)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _features(model_dir, windows, sample_rate_hz, channel_names):
    config = _read_json(model_dir / "feature_config.json")
    # These semantics are implemented by extract_features, not configurable at
    # inference. Fail closed rather than silently using incompatible features.
    supported = {
        "order": "channel-major", "channel_names": channel_names,
        "variance": "population", "skewness_std_floor": 1e-8,
        "zcr": "demeaned, zero is nonnegative, divided by N-1",
        "goertzel_power": "demeaned rectangular window; max(s1*s1+s2*s2-coeff*s1*s2,0)/(N*N)",
    }
    for key, expected in supported.items():
        if config.get(key) != expected:
            raise ValueError(f"Unsupported or inconsistent saved feature config: {key}")
    frequencies = config["goertzel_frequencies_hz"]
    features, names = extract_features(windows, sample_rate_hz, frequencies)
    coefficients = np.asarray(config["goertzel_coefficients"], dtype=float)
    expected = 2 * np.cos(2 * np.pi * np.asarray(frequencies) / sample_rate_hz)
    if coefficients.shape != expected.shape or not np.allclose(coefficients, expected, rtol=1e-7, atol=1e-10):
        raise ValueError("Saved Goertzel coefficients do not match frequencies/sample rate")
    if list(names) != _read_json(model_dir / "feature_names.json"):
        raise ValueError("Extracted feature names/order do not match saved feature names")
    return features


def _cnn_probabilities(model_dir, windows, labels):
    from .models import scale_windows

    scaling = _read_json(model_dir / "scaling.json")
    for key in ("mean", "scale"):
        values = np.asarray(scaling[key], dtype=float)
        if values.shape != (windows.shape[2],) or not np.isfinite(values).all():
            raise ValueError(f"Saved CNN {key} must be finite and per-channel")
        if key == "scale" and np.any(values <= 0):
            raise ValueError("Saved CNN scale must be positive")
    scaled = scale_windows(windows, scaling)
    if not np.isfinite(scaled).all():
        raise ValueError("Scaled CNN windows contain nonfinite values")
    # RF/SVM inference and importing this module never require TensorFlow.
    import tensorflow as tf

    model = tf.keras.models.load_model(model_dir / "final.keras", compile=False)
    if tuple(model.input_shape) != (None, *windows.shape[1:]):
        raise ValueError("Saved CNN input shape does not match window shape")
    if tuple(model.output_shape) != (None, len(labels)):
        raise ValueError("Saved CNN output shape does not match labels")
    return np.asarray(model.predict(scaled, verbose=0))


def predict_manifest(model_dir, manifest_path):
    """Return JSON-compatible per-window predictions from a trusted saved model.

    Uses only saved preprocessing and fitted model/scaling state. Labels in the
    input manifest are required by load_manifest but are not prediction inputs.
    Windows remain in manifest order, then chronological order per recording;
    window_index is zero-based within each recording (incomplete tails omitted).
    """
    model_dir = Path(model_dir)
    metadata = _read_json(model_dir / "metadata.json")
    kind = metadata["model"]
    if kind not in ("rf", "svm", "cnn"):
        raise ValueError(f"Unsupported saved model: {kind!r}")
    config = PreprocessConfig(**metadata["preprocessing"])
    config.validate()
    labels = metadata["labels"]
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("Saved labels must be nonempty strings")
    correct_label = metadata["correct_label"]
    threshold = metadata["correct_probability_threshold"]
    # Validate saved labels and threshold before loading a model or predicting.
    predict_labels(np.empty((0, len(labels))), labels, correct_label, threshold)
    channel_names = metadata["channel_names"]
    if (not isinstance(channel_names, list) or not channel_names
            or any(not isinstance(name, str) or not name for name in channel_names)
            or len(set(channel_names)) != len(channel_names)):
        raise ValueError("Saved channel_names must be nonempty unique ordered names")
    shape = metadata["window_shape"]
    if (not isinstance(shape, list) or len(shape) != 2
            or any(type(value) is not int or value < 1 for value in shape)
            or shape != [round(config.window_seconds * config.target_rate_hz), len(channel_names)]):
        raise ValueError("Saved window shape is inconsistent with preprocessing/channels")
    recordings = load_manifest(manifest_path)
    for recording in recordings:
        if list(recording.channel_names) != channel_names:
            raise ValueError(f"{recording.recording_id}: ordered channel names do not match saved model")
    batch = prepare_windows(recordings, config)
    windows = np.asarray(batch.X, dtype=np.float32)
    if (windows.ndim != 3 or list(windows.shape[1:]) != shape
            or not len(windows) or not np.isfinite(windows).all()):
        raise ValueError("Expected finite, nonempty windows matching saved window shape")
    if kind == "cnn":
        probabilities = _cnn_probabilities(model_dir, windows, labels)
    else:
        features = _features(model_dir, windows, batch.sample_rate_hz, channel_names)
        estimator = joblib.load(model_dir / "final.joblib")
        if set(estimator.classes_) != set(labels):
            raise ValueError("Saved estimator classes do not match metadata labels")
        probabilities = aligned_probabilities(estimator, features, labels)
    if probabilities.shape != (len(windows), len(labels)):
        raise ValueError("Model probability shape does not match windows/labels")
    raw = predict_labels(probabilities, labels, correct_label)
    conservative = predict_labels(probabilities, labels, correct_label, threshold)
    predictions, counts = [], {}
    for index, (recording_id, subject_id) in enumerate(zip(batch.recording_ids, batch.subject_ids)):
        recording_id, subject_id = str(recording_id), str(subject_id)
        window_index = counts.get(recording_id, 0)
        counts[recording_id] = window_index + 1
        predictions.append({
            "recording_id": recording_id, "subject_id": subject_id,
            "window_index": window_index,
            "predicted_label_raw": str(raw[index]),
            "predicted_label_conservative": str(conservative[index]),
            "probabilities": {label: float(probabilities[index, column])
                              for column, label in enumerate(labels)},
        })
    return {
        "model": kind, "labels": labels, "correct_label": correct_label,
        "correct_probability_threshold": threshold,
        "channel_names": channel_names, "window_shape": shape,
        "limitations": INFERENCE_LIMITATIONS, "predictions": predictions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="Trusted saved model directory (joblib can execute code)")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="New JSON file; existing paths are never overwritten")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("--output already exists; choose a new path (no overwrite)")
    result = predict_manifest(args.model_dir, args.manifest)
    # Serialize before opening; exclusive creation also prevents race overwrites.
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(text)
    return result


if __name__ == "__main__":
    main()
