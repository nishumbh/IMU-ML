"""Create SYNTHETIC mixed-format smoke-test data, NOT clinical validation data.

Run from anywhere: python scripts/create_demo_data.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "data" / "raw" / "synthetic"
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    names = [f"thigh_{kind}_{axis}" for kind in ("acc", "gyro") for axis in "xyz"]
    labels = ["Correct Form", "Incomplete Flexion", "Incorrect Compensation"]
    records = []
    for subject in range(4):
        for class_index, label in enumerate(labels):
            rate = 100 if subject % 2 == 0 else 60
            t = np.arange(rate * 8) / rate
            amplitude = (1.5, 0.4, 2.5)[class_index]
            imu = np.stack([amplitude * np.sin(2 * np.pi * (1 + class_index * 0.3) * t + i * 0.2)
                            + rng.normal(0, 0.05, len(t)) for i in range(6)], axis=1)
            imu[:, 2] += 9.80665
            # Extra channels deliberately present and deliberately excluded.
            data = np.column_stack([imu, rng.normal(size=(len(t), 2))])
            key = f"synthetic_p{subject}_class{class_index}"
            fmt = "npy" if subject % 2 == 0 else "csv"
            path = output / f"{key}.{fmt}"
            if fmt == "npy":
                np.save(path, data)
            else:
                pd.DataFrame(data, columns=names + ["emg", "pressure"]).to_csv(path, index=False)
            records.append({
                "dataset": "SYNTHETIC_ONLY", "subject_id": f"synthetic_p{subject}",
                "recording_id": key, "label": label, "format": fmt,
                "path": str(path.relative_to(root / "data")), "sample_rate_hz": rate,
                "metadata": {"synthetic": True, "exercise": "synthetic_oscillation"},
                "channels": [
                    {"name": name, "column": i if fmt == "npy" else name,
                     "kind": "accelerometer" if i < 3 else "gyroscope",
                     "unit": "m/s^2" if i < 3 else "rad/s"}
                    for i, name in enumerate(names)
                ],
            })
    manifest_path = root / "data" / "demo_manifest.json"
    manifest_path.write_text(json.dumps({"channel_names": names, "allowed_labels": labels, "records": records}, indent=2))
    print(f"Synthetic-only manifest: {manifest_path}")


if __name__ == "__main__":
    main()
