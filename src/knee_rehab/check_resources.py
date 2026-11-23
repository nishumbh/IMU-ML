"""Fail-closed deployment gate for externally measured, exact-build resources.

This validates a supplied measurement record, not the truth/completeness of the
measurements. It does not estimate ESP32 RAM from a desktop TFLite interpreter.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def check_resources(model_path, measurements):
    model = Path(model_path)
    errors = []
    if not measurements.get("target_board") or not measurements.get("firmware_build_id"):
        errors.append("target_board and firmware_build_id are required")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    if measurements.get("model_sha256") != digest:
        errors.append("model_sha256 must match the tested model artifact")
    values = {}
    byte_keys = (
        "app_partition_bytes", "firmware_image_bytes", "available_internal_ram_bytes",
        "static_internal_ram_bytes", "peak_dynamic_internal_ram_bytes", "ram_margin_bytes",
        "tflm_arena_required_bytes", "tflm_arena_reserved_bytes",
    )
    for key in byte_keys:
        value = measurements.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"{key} must be a nonnegative measured/configured integer")
        else:
            values[key] = value
    for key in ("latency_budget_ms", "measured_worst_case_latency_ms"):
        value = measurements.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            errors.append(f"{key} must be a finite positive number")
        else:
            values[key] = value
    for key in ("sd_logging_enabled", "operators_verified", "int8_accuracy_verified", "allocation_verified"):
        if measurements.get(key) is not True:
            errors.append(f"{key} must be verified true on target")
    if all(key in values for key in byte_keys):
        if not 0 < values["firmware_image_bytes"] <= values["app_partition_bytes"]:
            errors.append("Full firmware image (including model) exceeds application partition or is empty")
        if values["firmware_image_bytes"] < model.stat().st_size:
            errors.append("Firmware must embed the model; image smaller than model blob")
        ram = values["static_internal_ram_bytes"] + values["peak_dynamic_internal_ram_bytes"] + values["ram_margin_bytes"]
        if not 0 < ram <= values["available_internal_ram_bytes"] or values["ram_margin_bytes"] == 0:
            errors.append("Static + worst-case dynamic RAM + positive margin exceed usable internal RAM")
        arena = values["tflm_arena_required_bytes"]
        if not 0 < arena <= values["tflm_arena_reserved_bytes"]:
            errors.append("Measured tensor arena does not fit reservation")
        if values["tflm_arena_reserved_bytes"] > values["static_internal_ram_bytes"] + values["peak_dynamic_internal_ram_bytes"]:
            errors.append("Tensor arena reservation must be included exactly once in total measured RAM")
    if "latency_budget_ms" in values and "measured_worst_case_latency_ms" in values:
        if values["measured_worst_case_latency_ms"] > values["latency_budget_ms"]:
            errors.append("Worst-case inference latency exceeds budget")
    return {"resource_gate_pass": not errors, "errors": errors, "model_sha256": digest,
            "scope": "Supplied exact-build measurements only; not clinical/safety certification"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = check_resources(args.model, json.loads(args.measurements.read_text()))
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")
    return 0 if report["resource_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
