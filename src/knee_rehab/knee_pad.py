"""Build explicit manifests for the published KneE-PAD numeric NPY release.

Schema: doi:10.5281/zenodo.12112951 (metadata.txt), and
https://doi.org/10.1038/s41597-025-04963-4 (Tables 2/3, Data Records).
No EMG is loaded. Exercise-specific form labels are never pooled across tasks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

import numpy as np

SAMPLE_RATE_HZ = 148.14814814814815
SOURCE_DOI = "10.5281/zenodo.12112951"
SENSOR_NAMES = (
    "right_rectus_femoris", "right_hamstrings", "right_tibialis_anterior",
    "right_gastrocnemius", "left_rectus_femoris", "left_hamstrings",
    "left_tibialis_anterior", "left_gastrocnemius",
)
# Keep each exercise's two source errors distinct, including incomplete extension.
EXERCISES = {
    "squat": {0: "Correct Form", 1: "Weight Transfer to Healthy Leg",
              2: "Injured Leg Placed in Front"},
    "extension": {3: "Correct Form", 4: "Incomplete Extension",
                  5: "Limb Lift from Chair"},
    "gait": {6: "Correct Form", 7: "Incomplete Knee Extension",
             8: "Hip Abduction with Extended Knee"},
}


def build_manifest(dataset_dir, output_path, exercise="extension", sensor_ids=None, skip_empty=False):
    """Inspect release files and return a manifest; does not write or modify data.

    Default uses all eight IMUs as a research reference, not a single-IMU brace
    surrogate. Sensor IDs are fixed anatomical placements, never changed using
    a participant's injured side. Missing activities are allowed by the release.
    """
    root, output = Path(dataset_dir).resolve(), Path(output_path).resolve()
    if exercise not in EXERCISES:
        raise ValueError(f"exercise must be one of {list(EXERCISES)}")
    sensors = list(range(1, 9)) if sensor_ids is None else list(sensor_ids)
    if (not sensors or len(set(sensors)) != len(sensors)
            or any(isinstance(s, bool) or not isinstance(s, int) or not 1 <= s <= 8 for s in sensors)):
        raise ValueError("sensor_ids must be distinct integers in [1, 8]")
    if not root.is_dir():
        raise ValueError(f"Dataset directory not found: {root}")
    channels = []
    for sensor in sensors:
        for axis_index, axis in enumerate("xyzxyz"):
            kind = "accelerometer" if axis_index < 3 else "gyroscope"
            channels.append({"name": f"{SENSOR_NAMES[sensor-1]}_{kind}_{axis}",
                             "column": (sensor - 1) * 6 + axis_index,
                             "kind": kind, "unit": "g" if axis_index < 3 else "deg/s"})
    paths = sorted(root.rglob("imu.npy"))
    if not paths:
        raise ValueError(f"No imu.npy trials found under {root}")
    records = []
    excluded = []
    source_counts = Counter()
    for path in paths:
        relative = path.relative_to(root)
        match = re.fullmatch(r"Subject_([1-9]\d*)/([0-8])/Trial_([1-9]\d*)/imu\.npy", relative.as_posix())
        if match is None or not 1 <= int(match[1]) <= 31:
            raise ValueError(f"Unexpected KneE-PAD trial path: {relative}")
        subject, class_id, trial = map(int, match.groups())
        source_counts[str(class_id)] += 1
        if class_id not in EXERCISES[exercise]:
            continue
        data = np.load(path, mmap_mode="r", allow_pickle=False)
        if data.ndim != 2 or data.shape[0] != 48 or data.dtype.kind not in "iuf":
            raise ValueError(f"{relative}: expected numeric [48, samples>=2] array, got {data.shape}")
        if data.shape[1] < 2:
            if not skip_empty:
                raise ValueError(f"{relative}: empty/too-short trial; review before using --skip-empty")
            excluded.append({"path": relative.as_posix(), "shape": list(data.shape),
                             "reason": "fewer than two samples",
                             "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            continue
        columns = [channel["column"] for channel in channels]
        if not np.isfinite(data[columns]).all():
            raise ValueError(f"{relative}: nonfinite selected IMU readings")
        records.append({
            "path": os.path.relpath(path, output.parent), "format": "npy", "time_axis": 1,
            "dataset": "KneE-PAD", "subject_id": f"knee_pad:P{subject:03d}",
            "recording_id": f"knee_pad:P{subject:03d}:class{class_id}:trial{trial}",
            "label": EXERCISES[exercise][class_id], "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": channels,
            "metadata": {"exercise": exercise, "original_class_id": class_id,
                         "trial": trial, "sensor_ids": sensors, "source_doi": SOURCE_DOI,
                         "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "source_shape": list(data.shape), "usage": "supervised"},
        })
    if not records:
        raise ValueError(f"No trials found for exercise {exercise!r}")
    found_labels = {record["label"] for record in records}
    if found_labels != set(EXERCISES[exercise].values()):
        raise ValueError(f"Missing source classes for {exercise}: {set(EXERCISES[exercise].values()) - found_labels}")
    return {"dataset": "KneE-PAD", "source_doi": SOURCE_DOI,
            "schema_reference": "https://doi.org/10.1038/s41597-025-04963-4",
            "exercise": exercise, "sensor_ids": sensors,
            "sensor_policy": "Fixed anatomical placements; no side mirroring or injured-side substitution",
            "channel_names": [channel["name"] for channel in channels],
            "allowed_labels": list(EXERCISES[exercise].values()),
            "source_trial_counts_by_class": dict(sorted(source_counts.items())),
            "excluded_trials": excluded,
            "selected_trial_counts_by_label": dict(Counter(record["label"] for record in records)),
            "records": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("data/raw/dataset"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise", choices=tuple(EXERCISES), default="extension")
    parser.add_argument("--sensor-ids", type=int, nargs="+", default=list(range(1, 9)),
                        help="Fixed sensor IDs 1..8; default all eight, NOT a single-IMU model")
    parser.add_argument("--skip-empty", action="store_true",
                        help="Explicitly exclude trials with fewer than two samples and record them in the manifest")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("--output already exists; choose a new manifest path")
    manifest = build_manifest(args.dataset_dir, args.output, args.exercise, args.sensor_ids, args.skip_empty)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids accidentally overwriting an existing mapping.
    with args.output.open("x") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Saved {len(manifest['records'])} {args.exercise} trials, "
          f"{len(manifest['channel_names'])} channels to {args.output}")
    for excluded in manifest["excluded_trials"]:
        print(f"EXCLUDED {excluded['path']}: {excluded['reason']} {excluded['shape']}")
    return manifest


if __name__ == "__main__":
    main()
