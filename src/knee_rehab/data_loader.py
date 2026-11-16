"""Schema-driven, IMU-only ingestion. No dataset column order is guessed.

A manifest describes one constant-label, single-subject segment per record.
NPY files must be numeric 2-D arrays (never enable pickle for downloaded data).
CSV channel names/indices, units and sampling rates are explicit. See configs/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Recording:
    data: np.ndarray
    channel_names: tuple[str, ...]
    subject_id: str
    dataset: str
    label: str
    recording_id: str
    sample_rate_hz: float
    source_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)


def _text(spec: Mapping[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return value


def _index(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _column(table: np.ndarray | pd.DataFrame, column: Any) -> np.ndarray:
    if isinstance(column, str) and isinstance(table, pd.DataFrame):
        if column not in table.columns:
            raise ValueError(f"CSV column {column!r} not found")
        values = table[column].to_numpy()
    else:
        idx = _index(column, "column")
        if idx >= table.shape[1]:
            raise ValueError(f"column {idx} outside table with {table.shape[1]} columns")
        values = table.iloc[:, idx].to_numpy() if isinstance(table, pd.DataFrame) else table[:, idx]
    try:
        return np.asarray(values, dtype=np.float64)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"column {column!r} must be numeric") from exc


def _load_table(spec: Mapping[str, Any], path: Path) -> np.ndarray | pd.DataFrame:
    fmt = spec.get("format", path.suffix.lstrip(".").lower())
    if fmt == "npy":
        table = np.load(path, mmap_mode="r", allow_pickle=False)
        if not isinstance(table, np.ndarray) or table.ndim != 2 or table.dtype.kind not in "iuf":
            raise ValueError("NPY must be a 2-D, real numeric array; convert nested/object files offline")
        axis = spec.get("time_axis", 0)
        if isinstance(axis, bool) or axis not in (0, 1):
            raise ValueError("time_axis must be 0 or 1")
        return table if axis == 0 else table.T
    if fmt == "csv":
        options = spec.get("csv_options", {})
        if not isinstance(options, dict) or set(options) - {"sep", "header", "skiprows", "encoding"}:
            raise ValueError("csv_options supports only sep, header, skiprows, encoding")
        return pd.read_csv(path, **options)
    raise ValueError(f"Unsupported format {fmt!r}; expected npy or csv")


def load_recording(spec: Mapping[str, Any], base_dir: str | Path = ".") -> Recording:
    """Load an explicitly mapped segment and convert acceleration/rotation to SI.

    ``sample_start`` is inclusive and ``sample_stop`` exclusive, in source samples.
    ``scale`` and ``offset`` apply before conversion from the declared input unit.
    Optional timestamps validate (but never silently repair) the declared rate.
    """
    subject = _text(spec, "subject_id")
    dataset = _text(spec, "dataset")
    label = _text(spec, "label")
    record_id = _text(spec, "recording_id")
    path = (Path(base_dir) / _text(spec, "path")).resolve()
    fs = float(spec["sample_rate_hz"])
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError("sample_rate_hz must be positive and finite")
    table = _load_table(spec, path)
    start = _index(spec.get("sample_start", 0), "sample_start")
    stop = _index(spec.get("sample_stop", len(table)), "sample_stop")
    if not 0 <= start < stop <= len(table):
        raise ValueError(f"Invalid sample interval [{start}, {stop}) for {len(table)} rows")
    table = table.iloc[start:stop] if isinstance(table, pd.DataFrame) else table[start:stop]
    channels = spec.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ValueError("channels must be a nonempty list of explicit IMU mappings")
    units = {
        "accelerometer": {"m/s^2": 1.0, "g": 9.80665},
        "gyroscope": {"rad/s": 1.0, "deg/s": np.pi / 180.0},
    }
    names, arrays, kinds = [], [], []
    for channel in channels:
        name = _text(channel, "name")
        kind = _text(channel, "kind")
        unit = _text(channel, "unit")
        if kind not in units or unit not in units[kind]:
            raise ValueError(f"Unsupported IMU kind/unit: {kind}/{unit}")
        scale, offset = float(channel.get("scale", 1.0)), float(channel.get("offset", 0.0))
        if not np.isfinite([scale, offset]).all() or scale == 0:
            raise ValueError("Channel scale must be finite/nonzero and offset finite")
        values = (_column(table, channel["column"]) * scale + offset) * units[kind][unit]
        if not np.isfinite(values).all():
            raise ValueError(f"{record_id}/{name}: NaN/Inf/missing values; repair explicitly, never drop rows")
        names.append(name)
        kinds.append(kind)
        arrays.append(values)
    if len(set(names)) != len(names):
        raise ValueError("Channel names must be unique")
    if set(kinds) != set(units):
        raise ValueError("Select both accelerometer and gyroscope channels; no EMG/pressure inputs")
    timestamps = spec.get("timestamps")
    if timestamps is not None:
        time_scale = {"s": 1.0, "ms": 1e-3, "us": 1e-6}.get(timestamps.get("unit"))
        if time_scale is None:
            raise ValueError("timestamps.unit must be s, ms or us")
        t = _column(table, timestamps["column"]) * time_scale
        tolerance = float(timestamps.get("relative_tolerance", 0.05))
        if not np.isfinite(tolerance) or not 0 <= tolerance < 1:
            raise ValueError("timestamps.relative_tolerance must lie in [0, 1)")
        if not np.isfinite(t).all() or len(t) < 2 or np.any(np.diff(t) <= 0):
            raise ValueError("Timestamps must be finite, strictly increasing, with at least two samples")
        if np.any(np.abs(np.diff(t) * fs - 1) > tolerance):
            raise ValueError("Irregular timestamps or incorrect sample rate; regrid explicitly before ingestion")
    metadata = dict(spec.get("metadata", {}))
    metadata.update({"sample_start": start, "sample_stop": stop, "channel_kinds": kinds,
                     "units": ["m/s^2" if k == "accelerometer" else "rad/s" for k in kinds]})
    return Recording(np.column_stack(arrays), tuple(names), subject, dataset, label,
                     record_id, fs, path, metadata)


def load_manifest(path: str | Path) -> list[Recording]:
    """Read JSON manifest; relative data paths are resolved from its directory.

    All records must have the same ordered channels and physical IMU kinds. This
    catches mismatched array layouts, not incompatible placement/orientation:
    those require measured alignment and the same semantic channel names.
    """
    path = Path(path).resolve()
    manifest = json.loads(path.read_text())
    specs = manifest.get("records")
    if not isinstance(specs, list) or not specs:
        raise ValueError("Manifest needs a nonempty records list")
    recordings = [load_recording(spec, path.parent) for spec in specs]
    expected = tuple(manifest.get("channel_names", recordings[0].channel_names))
    allowed = manifest.get("allowed_labels")
    ids: set[str] = set()
    owners: dict[Path, str] = {}
    for recording in recordings:
        if recording.recording_id in ids:
            raise ValueError(f"Duplicate recording_id {recording.recording_id!r}")
        ids.add(recording.recording_id)
        if recording.channel_names != expected or recording.metadata["channel_kinds"] != recordings[0].metadata["channel_kinds"]:
            raise ValueError(f"{recording.recording_id}: inconsistent channel layout; explicitly align sensors first")
        if allowed is not None and recording.label not in allowed:
            raise ValueError(f"{recording.recording_id}: label not in allowed_labels")
        # Sessions/segments from one source may not masquerade as independent people.
        if owners.setdefault(recording.source_path, recording.subject_id) != recording.subject_id:
            raise ValueError("A source file has multiple subject IDs; split multi-subject files explicitly first")
    return recordings
