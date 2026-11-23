import hashlib
from pathlib import Path
import tempfile
import unittest

from knee_rehab.check_resources import check_resources


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "model.tflite"
        self.path.write_bytes(b"synthetic-model-placeholder")
        self.measurements = {
            "target_board": "test-only", "firmware_build_id": "test-build",
            "model_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "app_partition_bytes": 1000000, "firmware_image_bytes": 500000,
            "available_internal_ram_bytes": 250000, "static_internal_ram_bytes": 50000,
            "peak_dynamic_internal_ram_bytes": 100000, "ram_margin_bytes": 30000,
            "tflm_arena_required_bytes": 40000, "tflm_arena_reserved_bytes": 50000,
            "latency_budget_ms": 100, "measured_worst_case_latency_ms": 50,
            "sd_logging_enabled": True, "operators_verified": True,
            "int8_accuracy_verified": True, "allocation_verified": True,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_consistent_measurement_record_passes(self):
        self.assertTrue(check_resources(self.path, self.measurements)["resource_gate_pass"])

    def test_missing_measurements_fail_closed(self):
        self.assertFalse(check_resources(self.path, {})["resource_gate_pass"])

    def test_ram_flash_arena_hash_latency_and_sd_failures(self):
        for key, value in [("peak_dynamic_internal_ram_bytes", 300000),
                           ("firmware_image_bytes", 2000000), ("tflm_arena_reserved_bytes", 100),
                           ("model_sha256", "wrong-model"), ("sd_logging_enabled", False),
                           ("measured_worst_case_latency_ms", 150), ("ram_margin_bytes", 0),
                           ("int8_accuracy_verified", False)]:
            with self.subTest(key=key):
                self.assertFalse(check_resources(self.path, dict(self.measurements, **{key: value}))["resource_gate_pass"])


if __name__ == "__main__":
    unittest.main()
