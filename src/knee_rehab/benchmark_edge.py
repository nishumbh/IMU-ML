"""Edge (ESP32-S3) candidate benchmark under LOSO.

Candidates: RF reference (feature RF, not deployable as-is), compact RF/ExtraTrees
converted with emlearn (scored with the emlearn output the device would compute),
and a 1D CNN (`cnn`, the existing build_cnn) plus a depthwise-separable variant
(`dscnn`). Each CNN fold is trained once in fp32 and then quantized to int8 using
ONLY that fold's training windows for calibration; the int8 TFLite interpreter is
scored on the held-out subject.

Run: python -m knee_rehab.benchmark_edge --manifest M --output-dir artifacts/edge_<exercise>

Size and arena numbers are host-side proxies (host-compiled emlearn C, TFLite tensor
lifetimes). They are NOT ESP32 measurements and NOT a TFLM arena measurement.
"""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
import time
import warnings
from pathlib import Path

import numpy as np

from .baseline import make_estimator
from .data_loader import load_manifest
from .evaluate import EVALUATION_LIMITATIONS, classification_report, loso_splits, predict_labels
from .export import convert_int8_bytes, write_c_array, write_json
from .models import build_cnn, fit_scaling, scale_windows
from .preprocess import PreprocessConfig, extract_features, prepare_windows

CORRECT_LABEL = "Correct Form"
REFERENCE = "rf_reference"
COMPACT = {  # name -> (estimator kind, n_estimators, max_depth)
    "rf_t8_d4": ("rf", 8, 4),
    "rf_t16_d6": ("rf", 16, 6),
    "rf_t32_d8": ("rf", 32, 8),
    "et_t16_d6": ("et", 16, 6),
}
CNN_FAMILIES = ("cnn", "dscnn")
ALL_CANDIDATES = (REFERENCE, *COMPACT, "cnn_fp32", "cnn_int8", "dscnn_fp32", "dscnn_int8")
# Selection: within this macro-F1 margin of the reference, with no worse false-accept rate.
F1_MARGIN = 0.02
REPRESENTATIVE_LIMIT = 256


def build_dscnn(input_shape, n_classes, seed=42):
    """Depthwise-separable 1D CNN; ops: CONV_2D, DEPTHWISE_CONV_2D, MAX_POOL_2D, RESHAPE, FULLY_CONNECTED, SOFTMAX."""
    import tensorflow as tf
    tf.keras.utils.set_random_seed(seed)
    time_steps, channels = input_shape
    if time_steps < 8:
        raise ValueError("dscnn requires at least eight time samples per window")
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=input_shape),
        tf.keras.layers.Reshape((time_steps, channels, 1)),
        tf.keras.layers.Conv2D(12, (5, 1), padding="same", activation="relu"),
        tf.keras.layers.MaxPooling2D((2, 1)),
        tf.keras.layers.DepthwiseConv2D((3, 1), padding="same", activation="relu"),
        tf.keras.layers.Conv2D(16, (1, 1), activation="relu"),
        tf.keras.layers.MaxPooling2D((2, 1)),
        # Channel-mixing convolution over all IMU channels at once.
        tf.keras.layers.Conv2D(16, (1, channels), padding="valid", activation="relu"),
        tf.keras.layers.Flatten(),
        tf.keras.layers.Dense(n_classes, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy")
    return model


ARCHITECTURES = {"cnn": build_cnn, "dscnn": build_dscnn}


def augment_scaled(windows, rng, gain_sd=0.05, noise_sd=0.05):
    """Per-window, per-channel gain jitter plus small additive noise, in standardized units."""
    gain = rng.normal(1.0, gain_sd, size=(len(windows), 1, windows.shape[2]))
    noise = rng.normal(0.0, noise_sd, size=windows.shape)
    return (windows * gain + noise).astype(np.float32)


def _train_cnn(arch, train_windows, train_idx, n_classes, *, epochs, batch_size, seed, augment, rng):
    import tensorflow as tf
    tf.keras.backend.clear_session()
    scaling = fit_scaling(train_windows)
    scaled = scale_windows(train_windows, scaling)
    labels = train_idx
    if augment:
        scaled = np.concatenate([scaled, augment_scaled(scaled, rng)])
        labels = np.concatenate([train_idx, train_idx])
    model = ARCHITECTURES[arch](train_windows.shape[1:], n_classes, seed)
    model.fit(scaled, labels, epochs=epochs, batch_size=batch_size, verbose=0)
    return model, scaling, scaled


def _int8_probabilities(model, scaled_train, scaling_windows_test, seed, limit):
    """Quantize with calibration drawn from THIS fold's scaled training windows only."""
    import tensorflow as tf
    rng = np.random.default_rng(seed)
    selected = rng.choice(len(scaled_train), min(limit, len(scaled_train)), replace=False)
    binary = convert_int8_bytes(model, scaled_train[selected])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        interpreter = tf.lite.Interpreter(model_content=binary)
    input_detail = interpreter.get_input_details()[0]
    output_detail = interpreter.get_output_details()[0]
    if input_detail["dtype"] != np.int8 or output_detail["dtype"] != np.int8:
        raise RuntimeError("Quantized model is not int8 in/out")
    interpreter.resize_tensor_input(input_detail["index"], list(scaling_windows_test.shape))
    interpreter.allocate_tensors()
    in_scale, in_zero = input_detail["quantization"]
    out_scale, out_zero = output_detail["quantization"]
    quantized = np.clip(np.round(scaling_windows_test / in_scale) + in_zero, -128, 127).astype(np.int8)
    interpreter.set_tensor(input_detail["index"], quantized)
    interpreter.invoke()
    raw = interpreter.get_tensor(output_detail["index"]).astype(np.float64)
    probs = (raw - out_zero) * out_scale
    # int8 softmax dequantizes to a sum within about 1/256 of one; renormalize for the strict checker.
    probs = np.clip(probs, 0, 1)
    return probs / probs.sum(axis=1, keepdims=True), binary


def loso_neural(arch, X, y_idx, subjects, n_classes, *, epochs, batch_size, seed, augment,
                limit, quantize):
    fp_probs = np.zeros((len(X), n_classes))
    q_probs = np.zeros((len(X), n_classes)) if quantize else None
    folds = []
    rng = np.random.default_rng(seed)
    for subject, train, test in loso_splits(subjects):
        model, scaling, scaled_train = _train_cnn(
            arch, X[train], y_idx[train], n_classes, epochs=epochs, batch_size=batch_size,
            seed=seed, augment=augment, rng=rng)
        test_scaled = scale_windows(X[test], scaling)
        fp_probs[test] = model.predict(test_scaled, verbose=0)
        fold = {"held_out_subject": subject, "train_windows": len(train), "test_windows": len(test)}
        if quantize:
            # Calibrate on unaugmented scaled training windows.
            base_scaled = scale_windows(X[train], scaling)
            q, _ = _int8_probabilities(model, base_scaled, test_scaled, seed, limit)
            q_probs[test] = q
            fold["int8_accuracy"] = float(np.mean(q.argmax(1) == y_idx[test]))
        fold["fp32_accuracy"] = float(np.mean(fp_probs[test].argmax(1) == y_idx[test]))
        folds.append(fold)
    return fp_probs, q_probs, folds


def loso_compact(name, features, y_idx, subjects, n_classes, seed):
    kind, n_estimators, depth = COMPACT[name]
    probs = np.zeros((len(features), n_classes))
    folds = []
    sklearn_agreement = []
    for subject, train, test in loso_splits(subjects):
        model = _compact_estimator(kind, n_estimators, depth, seed).fit(features[train], y_idx[train])
        converted = _emlearn_convert(model, name)
        fold_probs = _emlearn_proba(converted, features[test], n_classes)
        probs[test] = fold_probs
        sk = model.predict_proba(features[test]).argmax(1)
        sklearn_agreement.append(float(np.mean(sk == fold_probs.argmax(1))))
        folds.append({"held_out_subject": subject, "train_windows": len(train),
                      "test_windows": len(test),
                      "accuracy": float(np.mean(fold_probs.argmax(1) == y_idx[test]))})
    return probs, folds, {"emlearn_vs_sklearn_argmax_agreement_mean": float(np.mean(sklearn_agreement))}


def _compact_estimator(kind, n_estimators, depth, seed):
    from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
    cls = RandomForestClassifier if kind == "rf" else ExtraTreesClassifier
    return cls(n_estimators=n_estimators, max_depth=depth, min_samples_leaf=2,
               class_weight="balanced", random_state=seed, n_jobs=-1)


def _emlearn_convert(model, name):
    import emlearn
    return emlearn.convert(model, method="inline", dtype="float")


def _emlearn_proba(converted, features, n_classes):
    probs = np.asarray(converted.predict_proba(np.asarray(features, dtype=np.float32)), dtype=np.float64)
    if probs.shape[1] != n_classes:
        raise RuntimeError("emlearn output columns do not match label count")
    total = probs.sum(axis=1, keepdims=True)
    return probs / np.where(total > 0, total, 1)


def _c_object_bytes(c_source_path):
    """Host-compiled object size (text + data). Falls back to source bytes without a compiler."""
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        return {"method": "c_source_bytes", "bytes": Path(c_source_path).stat().st_size}
    with tempfile.TemporaryDirectory() as directory:
        obj = Path(directory) / "model.o"
        subprocess.run([cc, "-std=c99", "-Os", "-c", str(c_source_path), "-o", str(obj)],
                       check=True, capture_output=True)
        text = subprocess.run(["size", str(obj)], check=True, capture_output=True, text=True).stdout
        fields = text.strip().splitlines()[-1].split()
        return {"method": "host_gcc_Os_text_plus_data", "bytes": int(fields[0]) + int(fields[1])}


def _activation_arena_estimate(binary):
    """Peak sum of simultaneously live int8 activation tensors (lifetime-based lower bound).

    Constants (weights, biases) are excluded. The TFLM planner adds overhead, so
    this is a planning estimate, NOT the measured arena.
    """
    import tensorflow as tf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        interpreter = tf.lite.Interpreter(model_content=binary, experimental_preserve_all_tensors=True)
    interpreter.allocate_tensors()
    details = {t["index"]: t for t in interpreter.get_tensor_details()}
    ops = [op for op in interpreter._get_ops_details() if op["op_name"] != "DELEGATE"]
    inputs = [d["index"] for d in interpreter.get_input_details()]
    outputs = [d["index"] for d in interpreter.get_output_details()]
    produced = {out for op in ops for out in op["outputs"] if out >= 0}
    activation = set(inputs) | produced
    first, last = {}, {}
    for index in inputs:
        first[index] = -1
    for step, op in enumerate(ops):
        for tensor in op["inputs"]:
            if tensor in activation:
                first.setdefault(tensor, step)
                last[tensor] = max(last.get(tensor, step), step)
        for tensor in op["outputs"]:
            if tensor in activation and tensor >= 0:
                first.setdefault(tensor, step)
                last.setdefault(tensor, step)
    for index in outputs:
        last[index] = len(ops)
    peak = 0
    for step in range(-1, len(ops) + 1):
        live = [t for t in activation if t in first and first[t] <= step <= last.get(t, first[t])]
        peak = max(peak, sum(int(np.prod(details[t]["shape"])) * np.dtype(details[t]["dtype"]).itemsize
                             for t in live))
    return {"peak_live_activation_bytes": int(peak),
            "operators": sorted({op["op_name"] for op in ops}),
            "caveat": "lifetime-based lower bound on activations; excludes TFLM overhead and fragmentation"}


def _cnn_macs(model):
    """Multiply-accumulates per window for Conv2D, DepthwiseConv2D and Dense layers."""
    import tensorflow as tf
    total = 0
    for layer in model.layers:
        out = layer.output.shape
        if isinstance(layer, tf.keras.layers.Conv2D):
            kh, kw = layer.kernel_size
            cin = int(layer.input.shape[-1])
            total += int(out[1]) * int(out[2]) * kh * kw * cin * layer.filters
        elif isinstance(layer, tf.keras.layers.DepthwiseConv2D):
            kh, kw = layer.kernel_size
            total += int(out[1]) * int(out[2]) * kh * kw * int(layer.input.shape[-1]) * layer.depth_multiplier
        elif isinstance(layer, tf.keras.layers.Dense):
            total += int(layer.input.shape[-1]) * layer.units
    return int(total)


def _host_latency_us(binary, sample):
    import tensorflow as tf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        interpreter = tf.lite.Interpreter(model_content=binary)
    detail = interpreter.get_input_details()[0]
    interpreter.resize_tensor_input(detail["index"], [1, *sample.shape[1:]])
    interpreter.allocate_tensors()
    scale, zero = detail["quantization"]
    x = np.clip(np.round(sample[:1] / scale) + zero, -128, 127).astype(np.int8)
    interpreter.set_tensor(detail["index"], x)
    for _ in range(5):
        interpreter.invoke()
    times = []
    for _ in range(50):
        start = time.perf_counter()
        interpreter.invoke()
        times.append(time.perf_counter() - start)
    return float(np.median(times) * 1e6)


def _metrics(y_idx, probs, labels):
    pred = predict_labels(probs, labels, CORRECT_LABEL)
    report = classification_report(np.asarray(labels)[y_idx], pred, labels, CORRECT_LABEL)
    f1 = [report["per_class"][label]["f1"] for label in labels]
    return {
        "pooled_accuracy": report["accuracy"],
        "macro_f1": float(np.mean(f1)),
        "false_accept_rate": report["false_acceptance"]["rate"],
        "false_accept_count": report["false_acceptance"]["count"],
        "incorrect_windows": report["false_acceptance"]["actual_incorrect"],
        "correct_form_recall": report["per_class"][CORRECT_LABEL]["recall"],
        "per_class": report["per_class"],
        "confusion_matrix": report["confusion_matrix"],
    }


def _summary(name, family, deployable, probs, y_idx, labels, folds, precision, acc_key="accuracy", **extra):
    metrics = _metrics(y_idx, probs, labels)
    fold_acc = [f[acc_key] for f in folds]
    metrics.update({"candidate": name, "family": family, "precision": precision,
                    "deployable_on_esp32": deployable,
                    "fold_accuracy_mean": float(np.mean(fold_acc)),
                    "fold_accuracy_std": float(np.std(fold_acc)), "folds": folds})
    metrics.update(extra)
    return metrics


def _write_comparison(path, exercise, results, selection):
    lines = [f"# Edge benchmark: {exercise} (two-IMU, LOSO)", "",
             "| Candidate | Precision | Deployable | Pooled acc | Macro-F1 | False-accept | Correct recall | Flash bytes | Arena est. bytes | MACs | Host us/window |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        def fmt_bytes(value):
            return "n/a" if value is None else f"{value:,}"
        lines.append("| {c} | {p} | {d} | {a:.2%} | {f:.3f} | {fa:.2%} ({n}/{m}) | {rec:.2%} | {b} | {ar} | {mac} | {lat} |".format(
            c=r["candidate"], p=r["precision"], d="yes" if r["deployable_on_esp32"] else "no",
            a=r["pooled_accuracy"], f=r["macro_f1"],
            fa=r["false_accept_rate"] or 0.0, n=r["false_accept_count"], m=r["incorrect_windows"],
            rec=r["correct_form_recall"], b=fmt_bytes(r["flash_bytes"]),
            ar=fmt_bytes(r.get("tensor_arena_estimate_bytes")), mac=fmt_bytes(r.get("macs")),
            lat=("n/a" if r.get("host_us_per_window") is None else f"{r['host_us_per_window']:.0f}")))
    lines += ["", "Selection rule: smallest deployable candidate with macro-F1 >= reference - "
              f"{F1_MARGIN:.2f} and false-accept rate <= reference.", "",
              f"Result: {selection['message']}", "",
              "Flash bytes: int8 TFLite file size for CNNs; host gcc -Os text+data for emlearn C. "
              "Arena: lifetime-based activation estimate, not a TFLM measurement. Host us/window is "
              "desktop CPU, not ESP32 latency. Metrics are raw argmax (no correct-form threshold)."]
    Path(path).write_text("\n".join(lines) + "\n")


def run_benchmark(manifest, output_dir, exercise=None, candidates=ALL_CANDIDATES, epochs=15,
                  batch_size=32, seed=42, augment=True, representative_limit=REPRESENTATIVE_LIMIT,
                  config=None, parity_count=20):
    output_dir = Path(output_dir)
    manifest = Path(manifest)
    unknown = [c for c in candidates if c not in ALL_CANDIDATES]
    if unknown:
        raise ValueError(f"Unknown candidates: {unknown}")
    if exercise is None:
        exercise = json.loads(manifest.read_text()).get("exercise") or output_dir.name.replace("edge_", "")
    config = config or PreprocessConfig()
    recordings = load_manifest(manifest)
    unassessed = [r.recording_id for r in recordings
                  if r.label.strip().casefold() in {"unassessed gait", "unassessed"}
                  or str(r.metadata.get("usage", "")).strip().casefold() == "pretraining_only"]
    if unassessed:
        raise ValueError(f"Unassessed/pretraining-only recordings cannot be supervised targets: {unassessed}")
    batch = prepare_windows(recordings, config)
    X = np.asarray(batch.X, dtype=np.float32)
    y = np.asarray(batch.y, dtype=str)
    subjects = np.asarray(batch.subject_ids, dtype=str)
    labels = sorted(set(y))
    if CORRECT_LABEL not in labels:
        raise ValueError(f"{CORRECT_LABEL!r} must exist in the manifest")
    if len(np.unique(subjects)) < 2:
        raise ValueError("LOSO requires at least two participants")
    for subject, train, _ in loso_splits(subjects):
        if len(np.unique(y[train])) < 2:
            raise ValueError(f"LOSO fold holding out {subject!r} has fewer than two training classes")
    y_idx = np.asarray([labels.index(label) for label in y])
    K = len(labels)
    output_dir.mkdir(parents=True, exist_ok=True)
    features = None
    if any(c == REFERENCE or c in COMPACT for c in candidates):
        features, _ = extract_features(X, batch.sample_rate_hz)
    results = []
    final_artifacts = {}
    run_info = {"epochs": epochs, "batch_size": batch_size, "augment": augment,
                "representative_limit": representative_limit, "seed": seed,
                "window_count": int(len(X)), "subjects": int(len(np.unique(subjects))),
                "labels": labels, "window_shape": list(X.shape[1:])}
    neural_wanted = [c for c in candidates if c not in (REFERENCE, *COMPACT)]
    cache = {}
    for family in CNN_FAMILIES:
        if any(c.startswith(family + "_") for c in neural_wanted):
            t0 = time.time()
            quantize = any(c == f"{family}_int8" for c in neural_wanted)
            fp_probs, q_probs, folds = loso_neural(
                family, X, y_idx, subjects, K, epochs=epochs, batch_size=batch_size, seed=seed,
                augment=augment, limit=representative_limit, quantize=quantize)
            cache[family] = (fp_probs, q_probs, folds, time.time() - t0)
    for name in candidates:
        if name == REFERENCE:
            probs = np.zeros((len(y), K))
            folds = []
            for subject, train, test in loso_splits(subjects):
                est = make_estimator("rf", seed).fit(features[train], y[train])
                fold_probs = np.zeros((len(test), K))
                for column, label in enumerate(est.classes_):
                    fold_probs[:, labels.index(label)] = est.predict_proba(features[test])[:, column]
                probs[test] = fold_probs
                folds.append({"held_out_subject": subject, "train_windows": len(train),
                              "test_windows": len(test),
                              "accuracy": float(np.mean(fold_probs.argmax(1) == y_idx[test]))})
            final = make_estimator("rf", seed).fit(features, y)
            import joblib
            joblib_path = output_dir / "rf_reference_final.joblib"
            joblib.dump(final, joblib_path)
            results.append(_summary(name, "rf", False, probs, y_idx, labels, folds, "float64",
                                    flash_bytes=None, joblib_bytes=joblib_path.stat().st_size,
                                    hyperparameters={"n_estimators": 64, "max_depth": 6, "min_samples_leaf": 2}))
        elif name in COMPACT:
            probs, folds, extra = loso_compact(name, features, y_idx, subjects, K, seed)
            kind, n_estimators, depth = COMPACT[name]
            final = _compact_estimator(kind, n_estimators, depth, seed).fit(features, y_idx)
            converted = _emlearn_convert(final, name)
            c_path = output_dir / f"{name}.c"
            c_path.write_text(_emlearn_c_source(converted, name))
            size = _c_object_bytes(c_path)
            parity = float(np.mean(_emlearn_proba(converted, features, K).argmax(1) == final.predict_proba(features).argmax(1)))
            final_artifacts[name] = {"c": str(c_path)}
            results.append(_summary(name, "rf" if kind == "rf" else "extra_trees", True, probs, y_idx,
                                    labels, folds, "float32 (emlearn)",
                                    flash_bytes=size["bytes"], flash_method=size["method"],
                                    hyperparameters={"n_estimators": n_estimators, "max_depth": depth,
                                                     "min_samples_leaf": 2},
                                    emlearn_final_vs_sklearn_agreement=parity, **extra))
        else:
            family, precision = name.split("_", 1)
            fp_probs, q_probs, folds, seconds = cache[family]
            is_int8 = precision == "int8"
            probs = q_probs if is_int8 else fp_probs
            t0 = time.time()
            rng = np.random.default_rng(seed)
            model, scaling, scaled = _train_cnn(
                family, X, y_idx, K, epochs=epochs, batch_size=batch_size, seed=seed,
                augment=augment, rng=rng)
            if is_int8:
                binary = convert_int8_bytes(model, scale_windows(X, scaling)[
                    np.random.default_rng(seed).choice(len(X), min(representative_limit, len(X)), replace=False)])
                flash = len(binary)
                arena = _activation_arena_estimate(binary)
                latency = _host_latency_us(binary, scale_windows(X[:1], scaling))
                final_artifacts[name] = {"binary": binary, "scaling": scaling, "model": model}
            else:
                import tensorflow as tf
                fp32_binary = tf.lite.TFLiteConverter.from_keras_model(model).convert()
                flash = len(fp32_binary)
                arena = None
                latency = None
            results.append(_summary(
                name, family, is_int8, probs, y_idx, labels, folds,
                "int8" if is_int8 else "float32", acc_key=f"{precision}_accuracy", flash_bytes=flash,
                flash_method="tflite_file_bytes", tensor_arena_estimate_bytes=(arena or {}).get("peak_live_activation_bytes"),
                arena_detail=arena, macs=_cnn_macs(model), host_us_per_window=latency,
                training_seconds_full_fit=time.time() - t0, loso_seconds=seconds,
                hyperparameters={"epochs": epochs, "batch_size": batch_size, "augment": augment,
                                 "architecture": family}))
    selection = _select(results)
    _write_outputs(output_dir, exercise, manifest, results, selection, run_info, final_artifacts,
                   X, y, y_idx, labels, batch, config, seed=seed,
                   parity_count=parity_count, candidates=candidates)
    return {"exercise": exercise, "results": results, "selection": selection}


def _emlearn_c_source(converted, name):
    import tempfile as tf_
    with tf_.TemporaryDirectory() as directory:
        path = Path(directory) / f"{name}.c"
        converted.save(file=str(path), name=name)
        return path.read_text()


def _select(results):
    deployable = [r for r in results if r["deployable_on_esp32"] and r["candidate"] != REFERENCE]
    reference = next((r for r in results if r["candidate"] == REFERENCE), None)
    if reference is None:
        return {"chosen": None, "message": "No RF reference was run; selection not evaluated."}
    qualifying = [r for r in deployable
                  if r["macro_f1"] >= reference["macro_f1"] - F1_MARGIN
                  and (r["false_accept_rate"] or 0) <= (reference["false_accept_rate"] or 0)]
    if not qualifying:
        best = max(deployable, key=lambda r: r["macro_f1"]) if deployable else None
        msg = ("No deployable candidate qualifies (within "
               f"{F1_MARGIN:.0%} macro-F1 of the RF reference with no worse false-accept rate)."
               + (f" Best deployable macro-F1: {best['candidate']} at {best['macro_f1']:.3f}."
                  if best else ""))
        return {"chosen": None, "message": msg}
    chosen = min(qualifying, key=lambda r: (r["flash_bytes"], -r["macro_f1"]))
    return {"chosen": chosen["candidate"],
            "message": f"Chosen {chosen['candidate']}: smallest qualifying deployable candidate "
                       f"({chosen['flash_bytes']:,} bytes, macro-F1 {chosen['macro_f1']:.3f}, "
                       f"false-accept {chosen['false_accept_rate']:.2%})."}


def _write_outputs(output_dir, exercise, manifest, results, selection, run_info, final_artifacts,
                   X, y, y_idx, labels, batch, config, seed, parity_count, candidates):
    chosen = selection["chosen"]
    chosen_name = chosen
    deployable_artifacts = {}
    if chosen and chosen.endswith("_int8"):
        art = final_artifacts[chosen]
        tflite_path = output_dir / "final.int8.tflite"
        tflite_path.write_bytes(art["binary"])
        header = write_c_array(output_dir / f"model_{exercise}.h", art["binary"], f"model_{exercise}")
        scaling_path = output_dir / "scaling.json"
        write_json(scaling_path, art["scaling"])
        parity = _parity_vectors(art["binary"], X, y, y_idx, labels, art["scaling"], parity_count, seed)
        write_json(output_dir / "parity_vectors.json", parity)
        deployment = {
            "exercise": exercise, "candidate": chosen, "tflite_model_bytes": len(art["binary"]),
            "model_sha256": hashlib.sha256(art["binary"]).hexdigest(),
            "c_header": header, "labels": labels, "correct_label": CORRECT_LABEL,
            "window_shape": list(X.shape[1:]), "sample_rate_hz": batch.sample_rate_hz,
            "channel_names": list(batch.channel_names),
            "preprocessing": config.__dict__ if hasattr(config, "__dict__") else str(config),
            "arena_estimate": _activation_arena_estimate(art["binary"]),
            "parity_vectors": {"file": "parity_vectors.json", "count": len(parity["vectors"]),
                               "caveat": "Windows come from the final all-subject fit; they test "
                                         "Python/C parity, not accuracy."},
            "deployment_ready": False,
            "deployment_status": "Host-side only. Requires ESP32 TFLM build, measured arena and "
                                 "latency, and hardware parity (check_resources gate).",
            "operators_expected": ["CONV_2D", "DEPTHWISE_CONV_2D", "MAX_POOL_2D", "FULLY_CONNECTED",
                                   "RESHAPE", "SOFTMAX", "ADD", "MEAN"],
            "scaling_file": "scaling.json",
        }
        write_json(output_dir / "deployment.json", deployment)
        deployable_artifacts = {"final.int8.tflite": tflite_path.name, "scaling": "scaling.json",
                                "header": header["path"]}
    elif chosen:
        deployable_artifacts = {"note": "Chosen candidate is not an int8 CNN; C export for compact RF "
                                        f"is in {output_dir}/{chosen}.c"}
    report = {
        "exercise": exercise, "manifest": str(manifest.resolve()),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "candidates_requested": list(candidates), "run": run_info,
        "environment": {"python": platform.python_version()},
        "evaluation": "Window-level LOSO; no threshold tuning; raw argmax predictions.",
        "limitations": EVALUATION_LIMITATIONS,
        "results": results, "selection": selection, "deployable_artifacts": deployable_artifacts,
    }
    write_json(output_dir / "results.json", report)
    _write_comparison(output_dir / "comparison.md", exercise, results, selection)
    return report


def _parity_vectors(binary, X, y, y_idx, labels, scaling, count, seed):
    import tensorflow as tf
    rng = np.random.default_rng(seed)
    picks = rng.choice(len(X), min(count, len(X)), replace=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        interpreter = tf.lite.Interpreter(model_content=binary)
    inp, out = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    in_scale, in_zero = inp["quantization"]
    out_scale, out_zero = out["quantization"]
    interpreter.resize_tensor_input(inp["index"], [len(picks), *X.shape[1:]])
    interpreter.allocate_tensors()
    scaled = scale_windows(X[picks], scaling)
    q = np.clip(np.round(scaled / in_scale) + in_zero, -128, 127).astype(np.int8)
    interpreter.set_tensor(inp["index"], q)
    interpreter.invoke()
    raw = interpreter.get_tensor(out["index"]).astype(np.int8)
    vectors = []
    for row, index in enumerate(picks):
        vectors.append({"index_in_dataset": int(index), "label": str(y[index]),
                        "input_int8": q[row].reshape(-1).tolist(),
                        "expected_output_int8": raw[row].tolist(),
                        "expected_argmax": labels[int(np.argmax(raw[row]))]})
    return {"input_shape": list(X.shape[1:]), "input_scale": float(in_scale),
            "input_zero_point": int(in_zero), "output_scale": float(out_scale),
            "output_zero_point": int(out_zero), "labels": labels, "vectors": vectors}


def main(argv=None):
    import os
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exercise")
    parser.add_argument("--candidates", default=",".join(ALL_CANDIDATES),
                        help="Comma-separated subset of: " + ", ".join(ALL_CANDIDATES))
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-augment", action="store_true")
    parser.add_argument("--representative-limit", type=int, default=REPRESENTATIVE_LIMIT)
    args = parser.parse_args(argv)
    if args.output_dir.exists() and any(p.name != ".gitkeep" for p in args.output_dir.iterdir()):
        parser.error("--output-dir must be new or empty")
    candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
    result = run_benchmark(args.manifest, args.output_dir, exercise=args.exercise,
                           candidates=candidates, epochs=args.epochs, batch_size=args.batch_size,
                           seed=args.seed, augment=not args.no_augment,
                           representative_limit=args.representative_limit)
    print(result["selection"]["message"])
    print(f"Wrote {args.output_dir / 'results.json'} and {args.output_dir / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    main()
