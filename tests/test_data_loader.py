import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from knee_rehab.data_loader import load_manifest, load_recording


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.values = np.column_stack([np.ones(20), np.full(20, 180), np.full(20, 999)])
        np.save(self.root / "imu.npy", self.values)
        self.spec = {
            "path": "imu.npy", "dataset": "synthetic", "subject_id": "person1",
            "recording_id": "trial1", "label": "Correct Form", "sample_rate_hz": 100,
            "channels": [
                {"name": "acc_x", "column": 0, "kind": "accelerometer", "unit": "g"},
                {"name": "gyro_x", "column": 1, "kind": "gyroscope", "unit": "deg/s"},
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_npy_si_units_and_only_selected_imu(self):
        record = load_recording(self.spec, self.root)
        self.assertEqual(record.data.shape, (20, 2))
        np.testing.assert_allclose(record.data[:, 0], 9.80665)
        np.testing.assert_allclose(record.data[:, 1], np.pi)

    def test_transpose_and_slice(self):
        np.save(self.root / "imu.npy", self.values.T)
        record = load_recording(dict(self.spec, time_axis=1, sample_start=3, sample_stop=9), self.root)
        self.assertEqual(record.data.shape, (6, 2))

    def test_csv_named_channels_and_timestamps(self):
        pd.DataFrame({"time": np.arange(20) / 100, "ax": self.values[:, 0],
                      "gx": self.values[:, 1], "emg": self.values[:, 2]}).to_csv(self.root / "imu.csv", index=False)
        spec = dict(self.spec, path="imu.csv", timestamps={"column": "time", "unit": "s"})
        spec["channels"] = [dict(self.spec["channels"][0], column="ax"), dict(self.spec["channels"][1], column="gx")]
        np.testing.assert_allclose(load_recording(spec, self.root).data, load_recording(self.spec, self.root).data)

    def test_invalid_timestamp_rate_rejected(self):
        pd.DataFrame({"t": np.arange(20) / 80, "a": np.ones(20), "g": np.ones(20)}).to_csv(self.root / "imu.csv", index=False)
        spec = dict(self.spec, path="imu.csv", timestamps={"column": "t", "unit": "s"})
        spec["channels"] = [dict(self.spec["channels"][0], column="a"), dict(self.spec["channels"][1], column="g")]
        with self.assertRaisesRegex(ValueError, "sample rate"):
            load_recording(spec, self.root)

    def test_missing_values_are_not_silently_dropped(self):
        self.values[4, 0] = np.nan
        np.save(self.root / "imu.npy", self.values)
        with self.assertRaisesRegex(ValueError, "NaN"):
            load_recording(self.spec, self.root)

    def test_pickle_not_enabled(self):
        np.save(self.root / "imu.npy", np.array([{"a": [1, 2]}], dtype=object))
        with self.assertRaises(ValueError):
            load_recording(self.spec, self.root)

    def test_manifest_resolves_relative_paths(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"records": [self.spec]}))
        self.assertEqual(load_manifest(path)[0].source_path, self.root / "imu.npy")

    def test_inconsistent_channel_order_rejected(self):
        second = dict(self.spec, recording_id="trial2", channels=list(reversed(self.spec["channels"])))
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"records": [self.spec, second]}))
        with self.assertRaisesRegex(ValueError, "channel layout"):
            load_manifest(path)

    def test_subject_alias_leakage_guard(self):
        second = dict(self.spec, recording_id="trial2", subject_id="other_person")
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"records": [self.spec, second]}))
        with self.assertRaisesRegex(ValueError, "multiple subject"):
            load_manifest(path)

    def test_rejects_pressure_as_imu_and_bad_segments(self):
        bad = dict(self.spec, channels=[dict(self.spec["channels"][0], kind="pressure")])
        with self.assertRaises(ValueError):
            load_recording(bad, self.root)
        with self.assertRaises(ValueError):
            load_recording(dict(self.spec, sample_stop=21), self.root)


if __name__ == "__main__":
    unittest.main()
