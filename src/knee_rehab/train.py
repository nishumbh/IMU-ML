"""Run: python -m knee_rehab.train --manifest manifest.json --output-dir artifacts."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
from scipy.signal import butter

from .baseline import fit_loso as fit_baseline
from .data_loader import load_manifest
from .evaluate import EVALUATION_LIMITATIONS, REJECT, loso_splits
from .export import write_json
from .preprocess import PreprocessConfig, extract_features, prepare_windows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", choices=("rf", "svm", "cnn"), default="rf")
    parser.add_argument("--correct-label", default="Correct Form")
    parser.add_argument("--correct-threshold", type=float)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-rate-hz", type=float, default=50.0)
    parser.add_argument("--cutoff-hz", type=float, default=10.0)
    parser.add_argument("--filter-order", type=int, default=4)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--overlap", type=float, default=0.5)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-flash-bytes", type=int, default=262144)
    parser.add_argument("--calibration-windows", type=int, default=256)
    args = parser.parse_args(argv)
    if args.output_dir.exists() and any(p.name != ".gitkeep" for p in args.output_dir.iterdir()):
        parser.error("--output-dir must be new or empty; use a separate directory per run to avoid stale artifacts")
    if args.correct_threshold is not None and not 0 <= args.correct_threshold <= 1:
        parser.error("--correct-threshold must be in [0, 1]")
    config = PreprocessConfig(target_rate_hz=args.target_rate_hz, cutoff_hz=args.cutoff_hz,
                              filter_order=args.filter_order, window_seconds=args.window_seconds,
                              overlap=args.overlap)
    recordings = load_manifest(args.manifest)
    unassessed = [r.recording_id for r in recordings
                  if r.label.strip().casefold() in {"unassessed gait", "unassessed"}
                  or str(r.metadata.get("usage", "")).strip().casefold() == "pretraining_only"]
    if unassessed:
        parser.error(f"Unassessed/pretraining-only recordings cannot be supervised targets: "
                     f"{unassessed}. Supplementary pretraining is not implemented; "
                     "use an explicitly annotated supervised manifest.")
    batch = prepare_windows(recordings, config)
    X, y = np.asarray(batch.X, dtype=np.float32), np.asarray(batch.y, dtype=str)
    subjects = np.asarray(batch.subject_ids, dtype=str)
    if X.ndim != 3 or not len(X) or not np.all(np.isfinite(X)):
        raise ValueError("Expected finite, nonempty [windows,time,channels] data")
    if len(X) != len(y) or len(X) != len(subjects) or len(X) != len(batch.recording_ids):
        raise ValueError("Window metadata lengths do not match")
    if len(np.unique(subjects)) < 2:
        raise ValueError("LOSO requires at least two actual participants")
    labels = sorted(set(y))
    if args.correct_label not in labels or REJECT in labels:
        raise ValueError("Correct label must exist; __REJECT__ is reserved")
    for subject, train, _ in loso_splits(subjects):
        if len(np.unique(y[train])) < 2:
            raise ValueError(f"LOSO fold holding out {subject!r} has fewer than two training classes")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / ("input_manifest" + args.manifest.suffix)
    if snapshot.resolve() != args.manifest.resolve():
        shutil.copyfile(args.manifest, snapshot)
    metadata = {
        "model": args.model, "seed": args.seed, "preprocessing": asdict(config),
        "filter_sos": butter(config.filter_order, config.cutoff_hz, fs=config.target_rate_hz,
                             btype="lowpass", output="sos").tolist(),
        "filter_state_policy": "sosfilt_zi * first_channel_value; causal; continuous across windows",
        "window_samples": round(config.window_seconds * config.target_rate_hz),
        "hop_samples": round(round(config.window_seconds * config.target_rate_hz) * (1 - config.overlap)),
        "resampling": "offline noncausal resample_poly; acquire directly at target rate or validate streaming parity",
        "manifest_path": str(args.manifest.resolve()),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "class_to_index": {label: i for i, label in enumerate(labels)},
        "labels": labels, "correct_label": args.correct_label,
        "correct_probability_threshold": args.correct_threshold,
        "channel_names": list(batch.channel_names), "sample_rate_hz": batch.sample_rate_hz,
        "window_shape": list(X.shape[1:]), "window_count": len(X),
        "subjects": sorted(set(subjects)),
        "recordings": [{"recording_id": r.recording_id, "subject_id": r.subject_id,
                        "dataset": r.dataset, "label": r.label,
                        "source_path": str(r.source_path), "sample_rate_hz": r.sample_rate_hz,
                        "channel_names": list(r.channel_names), "metadata": r.metadata}
                       for r in recordings],
        "evaluation_limitations": EVALUATION_LIMITATIONS,
        "supplementary_pretraining": "Not implemented; supervised rehab-only manifest required",
        "final_model_status": "Fitted on ALL input subjects; non-evaluation deployment fit",
        "deployment_safety": "No OOD guarantee. Unknown/unsafe classes are never designated "
                             "Correct Form; unknown inputs can still be falsely accepted."
    }
    # Source metadata may contain Paths or NumPy scalars; retain them as text if necessary.
    metadata = json.loads(json.dumps(metadata, default=str))
    write_json(output / "metadata.json", metadata)
    common = dict(labels=labels, correct_label=args.correct_label,
                  threshold=args.correct_threshold, seed=args.seed)
    if args.model != "cnn":
        features, names = extract_features(X, batch.sample_rate_hz)
        if not np.all(np.isfinite(features)):
            raise ValueError("Features contain nonfinite values")
        report, estimator = fit_baseline(features, y, subjects, kind=args.model, **common)
        joblib.dump(estimator, output / "final.joblib")
        write_json(output / "feature_names.json", list(names))
        write_json(output / "feature_config.json", {
            "order": "channel-major", "channel_names": list(batch.channel_names),
            "variance": "population", "skewness_std_floor": 1e-8,
            "zcr": "demeaned, zero is nonnegative, divided by N-1",
            "goertzel_frequencies_hz": [0.5, 1.0, 2.0, 3.0, 5.0],
            "goertzel_coefficients": (2 * np.cos(2 * np.pi * np.array([0.5, 1, 2, 3, 5]) / batch.sample_rate_hz)).tolist(),
            "goertzel_power": "demeaned rectangular window; max(s1*s1+s2*s2-coeff*s1*s2,0)/(N*N)"
        })
    else:
        from .models import fit_loso
        report, model, scaling = fit_loso(X, y, subjects, epochs=args.epochs,
                                         batch_size=args.batch_size, **common)
        model.save(output / "final.keras")
        write_json(output / "scaling.json", scaling)
    report["limitations"] = EVALUATION_LIMITATIONS
    write_json(output / "cv_results.json", report)
    if args.model == "cnn":
        from .export import export_int8
        export_int8(model, X, scaling, output / "final.int8.tflite", args.max_flash_bytes,
                    args.calibration_windows, args.seed)
    print(f"Saved LOSO results and final non-evaluation model to {output}")
    return report


if __name__ == "__main__":
    main()
