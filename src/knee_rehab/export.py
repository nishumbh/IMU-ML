"""Strict full-integer export; size checks are NOT target RAM certification."""
import hashlib
import json
from pathlib import Path

import numpy as np

from .models import scale_windows


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def export_int8(model, training_windows, scaling, output_path, max_flash_bytes,
                representative_limit=256, seed=42):
    """Calibrate only on supplied TRAINING data, never LOSO test windows.

    Use with a final model trained on all inputs, or only a fold's training subset.
    This function cannot infer provenance: the caller must enforce that contract.
    Returns deployment report; writes <output>.deployment.json even on size failure.
    """
    import tensorflow as tf
    if max_flash_bytes <= 0 or representative_limit <= 0 or not len(training_windows):
        raise ValueError("Positive flash budget and nonempty training calibration required")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = np.random.default_rng(seed).choice(
        len(training_windows), min(representative_limit, len(training_windows)), replace=False)
    calibration = scale_windows(training_windows[selected], scaling)

    def representative():
        for window in calibration:
            yield [window[np.newaxis].astype(np.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    binary = converter.convert()
    interpreter = tf.lite.Interpreter(model_content=binary,
                                      experimental_preserve_all_tensors=True)
    interpreter.allocate_tensors()
    inputs, outputs = interpreter.get_input_details(), interpreter.get_output_details()
    tensors = interpreter.get_tensor_details()
    if any(t["dtype"] != np.int8 for t in inputs + outputs):
        raise RuntimeError("Export did not produce signed int8 input/output")
    if any(np.issubdtype(t["dtype"], np.floating) for t in tensors):
        raise RuntimeError("Float tensor found: export is not strictly integer")

    def describe(tensor):
        scale, zero = tensor["quantization"]
        if scale <= 0:
            raise RuntimeError("Missing input/output quantization scale")
        return {"shape": tensor["shape"].tolist(), "dtype": "int8",
                "scale": float(scale), "zero_point": int(zero),
                "bytes": int(np.prod(tensor["shape"]))}

    sizes = [int(np.prod(t["shape"])) * np.dtype(t["dtype"]).itemsize for t in tensors]
    report = {
        "tflite_model_bytes": len(binary), "max_model_flash_bytes": max_flash_bytes,
        "flash_budget_pass": len(binary) <= max_flash_bytes,
        "deployment_ready": False,
        "deployment_status": "UNVERIFIED: run check_resources with measurements from the exact ESP32 build",
        "model_sha256": hashlib.sha256(binary).hexdigest(),
        "flash_scope": "Model blob only; firmware, resolver and other assets need extra flash.",
        "inputs": [describe(t) for t in inputs], "outputs": [describe(t) for t in outputs],
        "operators": sorted({op["op_name"] for op in interpreter._get_ops_details()
                             if op["op_name"] != "DELEGATE"}),
        "resource_estimates": {
            "largest_tensor_bytes": max(sizes), "sum_tensor_bytes": sum(sizes),
            "warning": "Tensor sizes include constants and ignore lifetimes, aliasing, kernel "
                       "scratch, alignment, runtime overhead, stacks and application buffers. "
                       "These are NOT an arena estimate or RAM guarantee. TFLM arena "
                       "measurement required on the exact target, resolver and kernels "
                       "(e.g. RecordingMicroInterpreter); verify AllocateTensors and peak RAM."
        },
        "representative_windows": len(selected), "calibration_source": "training_windows ONLY",
        "scaling": scaling,
        "deployment_instructions": [
            "Match training channel order, units, filtering, sample rate and windowing.",
            "Apply saved per-channel scaling outside the network (no embedded normalizer).",
            "Quantize input: clip(round(scaled / input_scale) + input_zero_point, -128, 127).",
            "Dequantize output: (int8_value - output_zero_point) * output_scale.",
            "Apply saved class mapping and conservative threshold to dequantized probabilities.",
            "Unknown/unsafe motion must not be auto-accepted; rejection is not OOD detection.",
            "Verify listed operators and versions with your TFLM build; conversion alone is not compatibility proof.",
            "Reported LOSO metrics are float-model metrics, NOT validation of this int8 artifact."
        ],
    }
    write_json(str(path) + ".deployment.json", report)
    if not report["flash_budget_pass"]:
        raise ValueError(f"Model is {len(binary)} bytes, exceeds {max_flash_bytes}-byte flash limit")
    path.write_bytes(binary)
    return report
