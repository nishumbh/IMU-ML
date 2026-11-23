import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import joblib
import numpy as np

from knee_rehab.data_loader import load_manifest
from knee_rehab.knee_pad import build_manifest, main as manifest_main, SAMPLE_RATE_HZ
from knee_rehab.preprocess import PreprocessConfig, extract_features, prepare_windows
from knee_rehab.train import main as train_main


class KneePadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dataset = self.root / "dataset"
        self.manifest = self.root / "manifests" / "extension.json"
        # Enough for two-second windows after 148.148 -> 50 Hz resampling.
        for subject in (1, 2, 3):
            for label in (3, 4, 5):
                path = self.dataset / f"Subject_{subject}" / str(label) / "Trial_1" / "imu.npy"
                path.parent.mkdir(parents=True)
                t = np.arange(450) / SAMPLE_RATE_HZ
                data = np.vstack([np.sin(t * (label + 1)) + index + subject / 10 for index in range(48)])
                np.save(path, data)

    def save_manifest(self, **kwargs):
        manifest = build_manifest(self.dataset, self.manifest, **kwargs)
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps(manifest))
        return manifest

    def test_verified_layout_units_labels_hash_and_subjects(self):
        manifest = self.save_manifest()
        records = load_manifest(self.manifest)
        self.assertEqual(len(records), 9)
        self.assertEqual(records[0].data.shape, (450, 48))
        self.assertEqual(len(manifest["channel_names"]), 48)
        self.assertEqual({r.label for r in records}, {"Correct Form", "Incomplete Extension", "Limb Lift from Chair"})
        self.assertEqual(len({r.subject_id for r in records}), 3)
        raw = np.load(records[0].source_path)
        np.testing.assert_allclose(records[0].data[:, :3], raw[:3].T * 9.80665)
        np.testing.assert_allclose(records[0].data[:, 3:6], np.deg2rad(raw[3:6].T))
        self.assertEqual(manifest["records"][0]["metadata"]["source_sha256"],
                         hashlib.sha256(records[0].source_path.read_bytes()).hexdigest())
        self.assertNotIn("flexion", str(manifest).lower())

    def test_fixed_sensor_selection(self):
        manifest = self.save_manifest(sensor_ids=[5, 1])
        self.assertEqual(len(manifest["channel_names"]), 12)
        record = load_manifest(self.manifest)[0]
        raw = np.load(record.source_path)
        np.testing.assert_allclose(record.data[:, 0], raw[24] * 9.80665)
        self.assertTrue(record.channel_names[0].startswith("left_rectus_femoris"))
        self.assertEqual(manifest["records"][0]["channels"][6]["column"], 0)

    def test_invalid_selection_paths_shapes_and_missing_classes(self):
        for sensors in ([], [0], [9], [1, 1], [True]):
            with self.subTest(sensors=sensors), self.assertRaises(ValueError):
                build_manifest(self.dataset, self.manifest, sensor_ids=sensors)
        with self.assertRaisesRegex(ValueError, "No trials"):
            build_manifest(self.dataset, self.manifest, exercise="gait")
        path = next(self.dataset.rglob("imu.npy"))
        np.save(path, np.ones((6, 450)))
        with self.assertRaisesRegex(ValueError, "48"):
            build_manifest(self.dataset, self.manifest)
        path.unlink()
        invalid = self.dataset / "Subject_1" / "9" / "Trial_1" / "imu.npy"
        invalid.parent.mkdir(parents=True)
        np.save(invalid, np.ones((48, 450)))
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            build_manifest(self.dataset, self.manifest)

    def test_empty_trials_require_explicit_exclusion(self):
        path = self.dataset / "Subject_1" / "3" / "Trial_2" / "imu.npy"
        path.parent.mkdir(parents=True)
        np.save(path, np.empty((48, 0)))
        with self.assertRaisesRegex(ValueError, "skip-empty"):
            build_manifest(self.dataset, self.manifest)
        manifest = build_manifest(self.dataset, self.manifest, skip_empty=True)
        self.assertEqual(len(manifest["records"]), 9)
        self.assertEqual(len(manifest["excluded_trials"]), 1)
        self.assertEqual(manifest["excluded_trials"][0]["shape"], [48, 0])
        self.assertEqual(manifest["source_trial_counts_by_class"]["3"], 4)

    def test_cli_no_overwrite(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manifest_main(["--dataset-dir", str(self.dataset), "--output", str(self.manifest)])
        before = self.manifest.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            manifest_main(["--dataset-dir", str(self.dataset), "--output", str(self.manifest)])
        self.assertEqual(before, self.manifest.read_bytes())

    def test_training_persistence_and_reloaded_predictions(self):
        self.save_manifest(sensor_ids=[1])
        output = self.root / "model"
        with contextlib.redirect_stdout(io.StringIO()):
            report = train_main(["--manifest", str(self.manifest), "--output-dir", str(output),
                                 "--correct-threshold", "0.9"])
        metadata = json.loads((output / "metadata.json").read_text())
        self.assertFalse(metadata["embedded_export_requested"])
        self.assertEqual(len(report["folds"]), 3)
        self.assertEqual(len(metadata["subjects"]), 3)
        self.assertEqual(metadata["skipped_recording_ids"], [])
        self.assertEqual(sum(metadata["window_counts_by_label"].values()), metadata["window_count"])
        self.assertFalse(list(output.glob("*.tflite")))
        self.assertIn("scikit-learn", metadata["environment"]["packages"])
        model = joblib.load(output / "final.joblib")
        batch = prepare_windows(load_manifest(self.manifest), PreprocessConfig(**metadata["preprocessing"]))
        features, names = extract_features(batch.X, batch.sample_rate_hz)
        probabilities = model.predict_proba(features)
        self.assertEqual(probabilities.shape, (metadata["window_count"], 3))
        np.testing.assert_allclose(probabilities.sum(axis=1), 1)
        self.assertEqual(list(names), json.loads((output / "feature_names.json").read_text()))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            train_main(["--manifest", str(self.manifest), "--output-dir", str(output)])

    def test_cnn_default_saves_without_embedded_conversion(self):
        self.save_manifest(sensor_ids=[1])
        output = self.root / "cnn_model"
        model = Mock()
        scaling = {"mean": [0.0] * 6, "scale": [1.0] * 6}
        with patch("knee_rehab.models.fit_loso", return_value=({}, model, scaling)), \
                patch("knee_rehab.export.export_int8", side_effect=AssertionError("No embedded export")), \
                contextlib.redirect_stdout(io.StringIO()):
            train_main(["--manifest", str(self.manifest), "--output-dir", str(output), "--model", "cnn"])
        model.save.assert_called_once_with(output / "final.keras")
        self.assertEqual(json.loads((output / "scaling.json").read_text()), scaling)
        self.assertFalse(list(output.glob("*.tflite")))
        self.assertFalse(json.loads((output / "metadata.json").read_text())["embedded_export_requested"])

    def test_embedded_export_must_be_explicit_and_cnn_only(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            train_main(["--manifest", str(self.manifest), "--output-dir", str(self.root / "model"),
                        "--export-int8"])


if __name__ == "__main__":
    unittest.main()
