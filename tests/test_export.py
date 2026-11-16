"""Optional integration check. Skipped unless the TensorFlow extra is installed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np


@unittest.skipUnless(importlib.util.find_spec("tensorflow"), "TensorFlow optional extra not installed")
class ExportTests(unittest.TestCase):
    def test_strict_int8_conversion_and_flash_budget(self):
        from knee_rehab.export import export_int8
        from knee_rehab.models import build_cnn, fit_scaling, scale_windows

        x = np.random.default_rng(42).normal(size=(12, 16, 2)).astype(np.float32)
        scaling = fit_scaling(x)
        model = build_cnn((16, 2), 3)
        model.train_on_batch(scale_windows(x, scaling), np.arange(12) % 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.tflite"
            report = export_int8(model, x, scaling, path, 256 * 1024)
            self.assertTrue(path.is_file())
            self.assertEqual(report["inputs"][0]["dtype"], "int8")
            self.assertEqual(report["outputs"][0]["dtype"], "int8")
            self.assertFalse(report["deployment_ready"])
            failed = Path(directory) / "too_big.tflite"
            with self.assertRaisesRegex(ValueError, "flash limit"):
                export_int8(model, x, scaling, failed, 1)
            self.assertFalse(failed.exists())
            self.assertFalse(json.loads(Path(str(failed) + ".deployment.json").read_text())["flash_budget_pass"])


if __name__ == "__main__":
    unittest.main()
