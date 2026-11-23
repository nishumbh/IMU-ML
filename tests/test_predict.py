from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from knee_rehab.baseline import aligned_probabilities, make_estimator
from knee_rehab.data_loader import load_manifest
from knee_rehab.evaluate import REJECT
from knee_rehab.models import scale_windows
from knee_rehab.predict import main, predict_manifest
from knee_rehab.preprocess import PreprocessConfig, extract_features, prepare_windows


class PredictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.manifest_path = self.root / "manifest.json"
        self.channels = [
            {"name": "acc_x", "column": 0, "kind": "accelerometer", "unit": "g"},
            {"name": "gyro_x", "column": 1, "kind": "gyroscope", "unit": "deg/s"},
        ]
        self.records = []
        for index in range(2):
            time = np.arange(160 + index * 16) / 80
            values = np.column_stack((index + np.sin(2 * np.pi * 2 * time),
                                      30 * np.cos(2 * np.pi * time)))
            path = self.root / f"subject{index}.npy"
            np.save(path, values)
            self.records.append({
                "path": path.name, "recording_id": f"r{index}",
                "subject_id": f"s{index}", "dataset": "synthetic",
                # Unknown input labels are not model inputs or OOD detection.
                "label": "unassessed", "sample_rate_hz": 80,
                "channels": self.channels,
            })
        self.save_manifest()
        self.config = PreprocessConfig(target_rate_hz=40, cutoff_hz=6, filter_order=2,
                                       window_seconds=0.5, overlap=0.25)
        self.batch = prepare_windows(load_manifest(self.manifest_path), self.config)
        self.frequencies = [0.75, 2.5]
        self.features, names = extract_features(self.batch.X, 40, self.frequencies)
        # Deliberately reverse sklearn's sorted class order to test alignment.
        self.labels = ["Wrong", "Correct Form"]
        self.metadata = {
            "model": "rf", "preprocessing": asdict(self.config),
            "channel_names": ["acc_x", "gyro_x"], "window_shape": [20, 2],
            "labels": self.labels, "correct_label": "Correct Form",
            "correct_probability_threshold": 0.8,
        }
        self.save_metadata()
        self.feature_config = {
            "order": "channel-major", "channel_names": ["acc_x", "gyro_x"],
            "variance": "population", "skewness_std_floor": 1e-8,
            "zcr": "demeaned, zero is nonnegative, divided by N-1",
            "goertzel_frequencies_hz": self.frequencies,
            "goertzel_coefficients": (2 * np.cos(2 * np.pi * np.array(self.frequencies) / 40)).tolist(),
            "goertzel_power": "demeaned rectangular window; max(s1*s1+s2*s2-coeff*s1*s2,0)/(N*N)",
        }
        self.save_json(self.model_dir / "feature_config.json", self.feature_config)
        self.save_json(self.model_dir / "feature_names.json", list(names))
        # Every feature vector has conflicting 3:1 labels, ensuring uncertain
        # Correct winners to exercise rejection with a real saved tiny RF.
        training_x = np.tile(self.features, (4, 1))
        training_y = np.repeat(["Correct Form"] * 3 + ["Wrong"], len(self.features))
        self.estimator = make_estimator("rf", seed=7)
        self.estimator.set_params(classifier__n_estimators=3, classifier__max_depth=1,
                                  classifier__bootstrap=False, classifier__class_weight=None,
                                  classifier__n_jobs=1)
        self.estimator.fit(training_x, training_y)
        joblib.dump(self.estimator, self.model_dir / "final.joblib")

    @staticmethod
    def save_json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def save_metadata(self):
        self.save_json(self.model_dir / "metadata.json", self.metadata)

    def save_manifest(self):
        self.save_json(self.manifest_path, {"records": self.records})

    def test_rf_saved_preprocessing_feature_and_probability_parity_no_fitting(self):
        expected = aligned_probabilities(self.estimator, self.features, self.labels)
        with patch("knee_rehab.predict.extract_features", wraps=extract_features) as extract, \
                patch.object(Pipeline, "fit", side_effect=AssertionError("no fitting")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("no fitting")), \
                patch.object(RandomForestClassifier, "fit", side_effect=AssertionError("no fitting")):
            result = predict_manifest(str(self.model_dir), str(self.manifest_path))
        np.testing.assert_array_equal(extract.call_args.args[0], self.batch.X)
        self.assertEqual(extract.call_args.args[1:], (40, self.frequencies))
        rows = result["predictions"]
        self.assertEqual(len(rows), len(self.batch.X))
        self.assertEqual(len(rows), 10)
        self.assertEqual([r["subject_id"] for r in rows], self.batch.subject_ids.tolist())
        self.assertEqual([r["recording_id"] for r in rows], self.batch.recording_ids.tolist())
        self.assertEqual([r["window_index"] for r in rows], list(range(5)) * 2)
        np.testing.assert_allclose([[r["probabilities"][label] for label in self.labels]
                                    for r in rows], expected)
        self.assertEqual({r["predicted_label_raw"] for r in rows}, {"Correct Form"})
        self.assertEqual({r["predicted_label_conservative"] for r in rows}, {REJECT})
        self.assertIn("not clinical advice", result["limitations"])
        self.assertIn("training data is not validation", result["limitations"])
        json.dumps(result, allow_nan=False)

    def test_saved_threshold_never_promotes_and_boundary_is_inclusive(self):
        self.metadata["correct_probability_threshold"] = 0.75
        self.save_metadata()
        probabilities = np.tile([0.25, 0.75], (len(self.features), 1))
        probabilities[0] = [0.8, 0.2]
        probabilities[1] = [0.3, 0.7]
        with patch("knee_rehab.predict.aligned_probabilities", return_value=probabilities):
            rows = predict_manifest(self.model_dir, self.manifest_path)["predictions"]
        self.assertEqual([r["predicted_label_conservative"] for r in rows[:3]],
                         ["Wrong", REJECT, "Correct Form"])
        self.metadata["correct_probability_threshold"] = None
        self.save_metadata()
        rows = predict_manifest(self.model_dir, self.manifest_path)["predictions"]
        self.assertTrue(all(r["predicted_label_raw"] == r["predicted_label_conservative"] for r in rows))

    def test_invalid_saved_threshold_and_labels_rejected(self):
        for threshold in (-0.01, 1.01, float("nan")):
            with self.subTest(threshold=threshold):
                self.metadata["correct_probability_threshold"] = threshold
                self.save_metadata()
                with self.assertRaisesRegex(ValueError, "threshold"):
                    predict_manifest(self.model_dir, self.manifest_path)
        self.metadata["correct_probability_threshold"] = 0.8
        self.metadata["labels"] = ["Correct Form", "Correct Form"]
        self.save_metadata()
        with self.assertRaisesRegex(ValueError, "unique"):
            predict_manifest(self.model_dir, self.manifest_path)

    def test_reordered_channels_rejected_even_when_manifest_consistent(self):
        for record in self.records:
            record["channels"] = list(reversed(self.channels))
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "ordered channel names"):
            predict_manifest(self.model_dir, self.manifest_path)

    def test_saved_window_shape_mismatch_rejected(self):
        for shape in ([21, 2], [20, 3]):
            with self.subTest(shape=shape):
                self.metadata["window_shape"] = shape
                self.save_metadata()
                with self.assertRaisesRegex(ValueError, "window shape"):
                    predict_manifest(self.model_dir, self.manifest_path)

    def test_feature_order_or_semantics_mismatch_rejected(self):
        names = json.loads((self.model_dir / "feature_names.json").read_text())
        self.save_json(self.model_dir / "feature_names.json", names[::-1])
        with self.assertRaisesRegex(ValueError, "feature names"):
            predict_manifest(self.model_dir, self.manifest_path)
        self.save_json(self.model_dir / "feature_names.json", names)
        self.feature_config["variance"] = "sample"
        self.save_json(self.model_dir / "feature_config.json", self.feature_config)
        with self.assertRaisesRegex(ValueError, "feature config: variance"):
            predict_manifest(self.model_dir, self.manifest_path)

    def test_svm_reuses_saved_pipeline_scaler(self):
        estimator = make_estimator("svm", seed=4).fit(
            self.features, ["Correct Form"] * 5 + ["Wrong"] * 5)
        joblib.dump(estimator, self.model_dir / "final.joblib")
        self.metadata["model"] = "svm"
        self.save_metadata()
        expected = aligned_probabilities(estimator, self.features, self.labels)
        with patch.object(StandardScaler, "fit", side_effect=AssertionError("no refit")):
            rows = predict_manifest(self.model_dir, self.manifest_path)["predictions"]
        np.testing.assert_allclose([[r["probabilities"][label] for label in self.labels]
                                    for r in rows], expected)

    def test_cnn_lazy_loading_and_saved_scaling_without_tensorflow_dependency(self):
        self.metadata["model"] = "cnn"
        self.save_metadata()
        scaling = {"mean": [1.2, -0.5], "scale": [2.0, 3.0]}
        self.save_json(self.model_dir / "scaling.json", scaling)
        expected = np.tile([0.2, 0.8], (len(self.features), 1))
        from unittest.mock import Mock
        model = Mock(input_shape=(None, 20, 2), output_shape=(None, 2))
        model.predict.return_value = expected
        loader = Mock(return_value=model)
        tf = SimpleNamespace(keras=SimpleNamespace(models=SimpleNamespace(load_model=loader)))
        with patch.dict(sys.modules, {"tensorflow": tf}), \
                patch("knee_rehab.models.fit_scaling", side_effect=AssertionError("no fitting")):
            result = predict_manifest(self.model_dir, self.manifest_path)
        loader.assert_called_once_with(self.model_dir / "final.keras", compile=False)
        np.testing.assert_array_equal(model.predict.call_args.args[0], scale_windows(self.batch.X, scaling))
        self.assertEqual(result["predictions"][0]["predicted_label_conservative"], "Correct Form")
        scaling["scale"] = [0, 3]
        self.save_json(self.model_dir / "scaling.json", scaling)
        with self.assertRaisesRegex(ValueError, "scale must be positive"):
            predict_manifest(self.model_dir, self.manifest_path)

    def test_invalid_model_probabilities_rejected(self):
        for values in ([0.1, 0.1], [float("nan"), 0.5], [-0.1, 1.1]):
            with self.subTest(values=values), patch(
                    "knee_rehab.predict.aligned_probabilities",
                    return_value=np.tile(values, (len(self.features), 1))):
                with self.assertRaises(ValueError):
                    predict_manifest(self.model_dir, self.manifest_path)

    def test_cli_writes_json_refuses_overwrite_and_rf_needs_no_tensorflow(self):
        project = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(project / "src"))
        output = self.root / "predictions.json"
        command = [sys.executable, "-m", "knee_rehab.predict", "--model-dir", str(self.model_dir),
                   "--manifest", str(self.manifest_path), "--output", str(output)]
        completed = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        saved = output.read_bytes()
        result = json.loads(saved)
        self.assertEqual(result, predict_manifest(self.model_dir, self.manifest_path))
        completed = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("already exists", completed.stderr)
        self.assertEqual(output.read_bytes(), saved)
        lazy_check = subprocess.run(
            [sys.executable, "-c", "import sys; from knee_rehab.predict import predict_manifest; "
             "predict_manifest(sys.argv[1], sys.argv[2]); assert 'tensorflow' not in sys.modules",
             str(self.model_dir), str(self.manifest_path)],
            cwd=project, env=env, capture_output=True, text=True)
        self.assertEqual(lazy_check.returncode, 0, lazy_check.stderr)

    def test_failed_prediction_does_not_create_output(self):
        output = self.root / "invalid.json"
        self.metadata["window_shape"] = [999, 2]
        self.save_metadata()
        with self.assertRaises(ValueError):
            main(["--model-dir", str(self.model_dir), "--manifest", str(self.manifest_path),
                  "--output", str(output)])
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
