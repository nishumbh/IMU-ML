"""Optional integration check. Skipped unless the TensorFlow extra is installed."""
import importlib.util
import hashlib
import json
import re
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


class CArrayTests(unittest.TestCase):
    def test_c_array_round_trip(self):
        from knee_rehab.export import write_c_array
        data = bytes(range(256)) * 3 + b"\x00\xff"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_demo.h"
            info = write_c_array(path, data, "model_demo")
            text = path.read_text()
            self.assertIn("const unsigned char model_demo[] __attribute__((aligned(16)))", text)
            self.assertIn(f"const size_t model_demo_len = {len(data)};", text)
            body = text.split("= {", 1)[1].split("};", 1)[0]
            parsed = bytes(int(token, 16) for token in re.findall(r"0x([0-9a-f]{2})", body))
            self.assertEqual(parsed, data)
            self.assertEqual(info["array_bytes"], len(data))
            self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())

    def test_c_array_rejects_bad_names_and_empty_data(self):
        from knee_rehab.export import write_c_array
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.h"
            with self.assertRaisesRegex(ValueError, "C identifier"):
                write_c_array(path, b"\x01", "1bad-name")
            with self.assertRaisesRegex(ValueError, "empty"):
                write_c_array(path, b"", "ok_name")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
