"""Edge benchmark tests. Fast: demo manifest, one fold per subject, 1 epoch, few candidates."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo_manifest.json"
HAS_TF = importlib.util.find_spec("tensorflow") is not None
HAS_EMLEARN = importlib.util.find_spec("emlearn") is not None


def _result(name, f1, fa, flash, deployable=True, fa_count=0):
    return {"candidate": name, "macro_f1": f1, "false_accept_rate": fa, "flash_bytes": flash,
            "deployable_on_esp32": deployable, "false_accept_count": fa_count,
            "incorrect_windows": 10}


class SelectionRuleTests(unittest.TestCase):
    def test_smallest_qualifying_candidate_is_chosen(self):
        from knee_rehab.benchmark_edge import _select
        results = [
            _result("rf_reference", 0.60, 0.20, None, deployable=False),
            _result("rf_t8_d4", 0.50, 0.10, 1000),          # too low F1
            _result("rf_t16_d6", 0.59, 0.20, 2000),         # qualifies, larger
            _result("cnn_int8", 0.585, 0.20, 1500),         # qualifies, smaller than rf_t16
            _result("cnn_fp32", 0.70, 0.05, 9000, deployable=False),  # not deployable
        ]
        selection = _select(results)
        self.assertEqual(selection["chosen"], "cnn_int8")

    def test_false_accept_must_not_be_worse_than_reference(self):
        from knee_rehab.benchmark_edge import _select
        results = [_result("rf_reference", 0.60, 0.20, None, deployable=False),
                   _result("rf_t8_d4", 0.61, 0.25, 1000)]
        selection = _select(results)
        self.assertIsNone(selection["chosen"])
        self.assertIn("No deployable candidate qualifies", selection["message"])

    def test_no_reference_reports_not_evaluated(self):
        from knee_rehab.benchmark_edge import _select
        self.assertIsNone(_select([_result("rf_t8_d4", 0.5, 0.1, 10)])["chosen"])


class CandidateValidationTests(unittest.TestCase):
    def test_unknown_candidate_rejected_before_loading(self):
        from knee_rehab.benchmark_edge import run_benchmark
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Unknown candidates"):
                run_benchmark(DEMO, Path(directory) / "out", candidates=("not_a_model",))


@unittest.skipUnless(HAS_TF and HAS_EMLEARN, "TensorFlow and emlearn optional extras not installed")
class DemoBenchmarkTests(unittest.TestCase):
    def test_demo_run_writes_results_and_int8_artifacts(self):
        from knee_rehab.benchmark_edge import run_benchmark
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "edge_demo"
            report = run_benchmark(DEMO, out, exercise="demo", candidates=("rf_reference", "rf_t8_d4", "cnn_int8"),
                                   epochs=1, augment=False, representative_limit=16, parity_count=5)
            results = json.loads((out / "results.json").read_text())
            names = [r["candidate"] for r in results["results"]]
            self.assertEqual(names, ["rf_reference", "rf_t8_d4", "cnn_int8"])
            for row in results["results"]:
                self.assertGreaterEqual(row["pooled_accuracy"], 0)
                self.assertLessEqual(row["pooled_accuracy"], 1)
                self.assertIn("false_accept_rate", row)
                self.assertEqual(len(row["folds"]), 4)  # four synthetic subjects
            self.assertTrue((out / "comparison.md").read_text().startswith("# Edge benchmark"))
            self.assertFalse(report["results"][0]["deployable_on_esp32"])
            cnn = next(r for r in results["results"] if r["candidate"] == "cnn_int8")
            self.assertEqual(cnn["precision"], "int8")
            self.assertTrue(cnn["deployable_on_esp32"])
            self.assertGreater(cnn["flash_bytes"], 0)
            if report["selection"]["chosen"] == "cnn_int8":
                self.assertTrue((out / "final.int8.tflite").is_file())
                self.assertTrue((out / "model_demo.h").is_file())
                vectors = json.loads((out / "parity_vectors.json").read_text())
                self.assertEqual(len(vectors["vectors"]), 5)
                self.assertEqual(len(vectors["vectors"][0]["input_int8"]), 100 * 6)
                self.assertTrue(all(len(v["expected_output_int8"]) == 3 for v in vectors["vectors"]))

    def test_parity_vectors_reproduce_interpreter_output(self):
        import tensorflow as tf
        from knee_rehab.benchmark_edge import _parity_vectors, build_dscnn
        from knee_rehab.export import convert_int8_bytes
        from knee_rehab.models import fit_scaling, scale_windows
        rng = np.random.default_rng(0)
        X = rng.normal(size=(24, 100, 4)).astype(np.float32)
        y = np.arange(24) % 3
        labels = ["a", "b", "c"]
        scaling = fit_scaling(X)
        model = build_dscnn((100, 4), 3, seed=1)
        model.fit(scale_windows(X, scaling), y, epochs=1, verbose=0)
        binary = convert_int8_bytes(model, scale_windows(X[:16], scaling))
        parity = _parity_vectors(binary, X, np.array(["a", "b", "c"] * 8), y, labels, scaling, 6, 0)
        interpreter = tf.lite.Interpreter(model_content=binary)
        inp, out = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
        interpreter.resize_tensor_input(inp["index"], [1, 100, 4])
        interpreter.allocate_tensors()
        for vector in parity["vectors"]:
            interpreter.set_tensor(inp["index"], np.asarray(vector["input_int8"], dtype=np.int8).reshape(1, 100, 4))
            interpreter.invoke()
            np.testing.assert_array_equal(interpreter.get_tensor(out["index"])[0], vector["expected_output_int8"])


if __name__ == "__main__":
    unittest.main()
