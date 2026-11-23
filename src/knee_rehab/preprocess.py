"""Per-recording DSP, boundary-safe fixed windows, and MCU-portable features.

Filtering is causal; resample_poly is an OFFLINE, noncausal anti-aliasing FIR.
Use the target sample rate on-device, or validate a streaming rate converter
against this reference before deployment. Windows are not repetition detectors.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import warnings

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt, sosfilt_zi

from .data_loader import Recording


@dataclass(frozen=True)
class PreprocessConfig:
    target_rate_hz: float = 50.0
    cutoff_hz: float = 10.0
    filter_order: int = 4
    window_seconds: float = 2.0
    overlap: float = 0.5

    def validate(self) -> None:
        numeric = [self.target_rate_hz, self.cutoff_hz, self.window_seconds, self.overlap]
        if not np.isfinite(numeric).all():
            raise ValueError("DSP settings must be finite")
        if self.target_rate_hz <= 0 or not 0 < self.cutoff_hz < self.target_rate_hz / 2:
            raise ValueError("Require 0 < cutoff_hz < target_rate_hz/2")
        if isinstance(self.filter_order, bool) or not isinstance(self.filter_order, int) or self.filter_order < 1:
            raise ValueError("filter_order must be a positive integer")
        if not 0 <= self.overlap < 1:
            raise ValueError("overlap must lie in [0, 1)")
        count = self.window_seconds * self.target_rate_hz
        if count < 2 or not np.isclose(count, round(count), rtol=0, atol=1e-7):
            raise ValueError("window_seconds * target_rate_hz must be an integer >= 2")
        if round(round(count) * (1 - self.overlap)) < 1:
            raise ValueError("Overlap leaves less than a one-sample hop")


@dataclass(frozen=True)
class WindowBatch:
    X: np.ndarray
    y: np.ndarray
    subject_ids: np.ndarray
    recording_ids: np.ndarray
    channel_names: tuple[str, ...]
    sample_rate_hz: float


def resample_signal(data: np.ndarray, source_rate_hz: float, target_rate_hz: float) -> np.ndarray:
    """Rational polyphase resampling, including anti-aliasing when downsampling."""
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 1 or not np.isfinite(data).all():
        raise ValueError("Expected finite [time, channels] data with at least two samples")
    if not np.isfinite([source_rate_hz, target_rate_hz]).all() or min(source_rate_hz, target_rate_hz) <= 0:
        raise ValueError("Sample rates must be finite and positive")
    if source_rate_hz == target_rate_hz:
        return data.copy()
    ratio = Fraction(target_rate_hz / source_rate_hz).limit_denominator(10000)
    if not np.isclose(float(ratio), target_rate_hz / source_rate_hz, rtol=1e-7, atol=0):
        raise ValueError("Sample-rate ratio cannot be accurately represented; supply calibrated rates")
    return resample_poly(data, ratio.numerator, ratio.denominator, axis=0, padtype="line")


def lowpass_filter(data: np.ndarray, sample_rate_hz: float, cutoff_hz: float, order: int = 4) -> np.ndarray:
    """Causal Butterworth SOS, initialized at the first value of each channel.

    Run continuously across windows, reset only at recording/stream boundaries.
    The corresponding firmware must use the same SOS coefficients/state policy.
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 2 or min(data.shape) < 1 or not np.isfinite(data).all():
        raise ValueError("Expected nonempty finite [time, channels] data")
    if not np.isfinite([sample_rate_hz, cutoff_hz]).all() or not 0 < cutoff_hz < sample_rate_hz / 2:
        raise ValueError("Require 0 < cutoff_hz < sample_rate_hz/2")
    if isinstance(order, bool) or not isinstance(order, int) or order < 1:
        raise ValueError("order must be a positive integer")
    sos = butter(order, cutoff_hz, fs=sample_rate_hz, btype="lowpass", output="sos")
    zi = sosfilt_zi(sos)[:, :, None] * data[0][None, None, :]
    filtered, _ = sosfilt(sos, data, axis=0, zi=zi)
    return filtered


def sliding_windows(data: np.ndarray, window_samples: int, hop_samples: int) -> np.ndarray:
    """Copy full windows only; discard the incomplete tail (no artificial padding)."""
    data = np.asarray(data)
    if data.ndim != 2:
        raise ValueError("data must have shape [samples, channels]")
    for value in (window_samples, hop_samples):
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
            raise ValueError("Window and hop sizes must be positive integers")
    starts = range(0, len(data) - window_samples + 1, hop_samples)
    windows = [data[start:start + window_samples] for start in starts]
    return np.stack(windows) if windows else np.empty((0, window_samples, data.shape[1]), dtype=data.dtype)


def prepare_windows(recordings: list[Recording], config: PreprocessConfig | None = None) -> WindowBatch:
    """DSP each constant-label recording independently; never cross a boundary."""
    config = config or PreprocessConfig()
    config.validate()
    if not recordings:
        raise ValueError("No recordings supplied")
    window = round(config.window_seconds * config.target_rate_hz)
    hop = round(window * (1 - config.overlap))
    names = recordings[0].channel_names
    xs, ys, subjects, record_ids = [], [], [], []
    for record in recordings:
        if record.channel_names != names:
            raise ValueError("All recordings must have the same ordered physical channels")
        if record.data.ndim != 2 or record.data.shape[1] != len(names):
            raise ValueError(f"{record.recording_id}: data/channel name mismatch")
        # Resampling removes frequencies above the new Nyquist before decimation.
        values = resample_signal(record.data, record.sample_rate_hz, config.target_rate_hz)
        values = lowpass_filter(values, config.target_rate_hz, config.cutoff_hz, config.filter_order)
        windows = sliding_windows(values, window, hop)
        if not len(windows):
            warnings.warn(f"{record.recording_id}: shorter than one window; skipped", stacklevel=2)
            continue
        xs.append(windows)
        ys.extend([record.label] * len(windows))
        subjects.extend([record.subject_id] * len(windows))
        record_ids.extend([record.recording_id] * len(windows))
    if not xs:
        raise ValueError("No full windows; use longer recordings or a shorter window_seconds")
    return WindowBatch(np.concatenate(xs).astype(np.float32), np.asarray(ys), np.asarray(subjects),
                       np.asarray(record_ids), names, config.target_rate_hz)


def extract_features(
    windows: np.ndarray,
    sample_rate_hz: float,
    probe_frequencies_hz: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 5.0),
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Channel-major features using sums, products, sqrt and fixed Goertzel probes.

    Population variance/skewness. Zero crossings are measured after demeaning;
    exactly zero is nonnegative. Constant channels have zero skew and ZCR.
    Frequency features are rectangular-window, demeaned Goertzel powers |X(f)|²/N²
    at a few fixed frequencies, NOT full band energies or a full-spectrum peak.
    Coefficients 2*cos(2*pi*f/fs) are precomputed constants in firmware; the
    recurrence uses only multiplication/addition. No SciPy stats or runtime FFT.
    Feature names use channel indices; persist channel_names alongside them.
    """
    x = np.asarray(windows, dtype=np.float64)
    if x.ndim != 3 or x.shape[1] < 2 or x.shape[2] < 1 or not np.isfinite(x).all():
        raise ValueError("Expected finite [windows, samples>=2, channels>=1] input")
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be finite and positive")
    frequencies = np.asarray(probe_frequencies_hz, dtype=float)
    if frequencies.ndim != 1 or not np.isfinite(frequencies).all() or np.any(frequencies <= 0) or np.any(frequencies >= sample_rate_hz / 2):
        raise ValueError("Probe frequencies must lie strictly between zero and Nyquist")
    if len(set(frequencies)) != len(frequencies):
        raise ValueError("Probe frequencies must be unique")
    n = x.shape[1]
    mean = x.mean(axis=1)
    centered = x - mean[:, None, :]
    var = np.mean(centered * centered, axis=1)
    std = np.sqrt(var)
    skew = np.zeros_like(var)
    # A float32-scale floor also prevents numerically unstable skew at rest.
    np.divide(np.mean(centered ** 3, axis=1), std ** 3, out=skew, where=std > 1e-8)
    zcr = np.mean((centered[:, 1:] >= 0) != (centered[:, :-1] >= 0), axis=1)
    features = [mean, var, std, skew, zcr, x.min(axis=1), x.max(axis=1),
                np.sqrt(np.mean(x * x, axis=1)), np.ptp(x, axis=1)]
    feature_labels = ["mean", "variance", "std", "skewness", "zcr", "min", "max", "rms", "peak_to_peak"]
    for frequency in frequencies:
        coefficient = 2 * np.cos(2 * np.pi * frequency / sample_rate_hz)
        s1, s2 = np.zeros_like(mean), np.zeros_like(mean)
        for index in range(n):
            s0 = centered[:, index, :] + coefficient * s1 - s2
            s2, s1 = s1, s0
        power = np.maximum(s1 * s1 + s2 * s2 - coefficient * s1 * s2, 0) / n ** 2
        features.append(power)
        feature_labels.append(f"goertzel_power_{frequency:g}hz")
    result = np.stack(features, axis=-1).reshape(x.shape[0], x.shape[2] * len(features))
    names = tuple(f"ch{channel}_{name}" for channel in range(x.shape[2]) for name in feature_labels)
    if not np.isfinite(result).all():
        raise ValueError("Feature overflow; check input units and sensor range")
    result = result.astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Features exceed float32 range; check input units")
    return result, names
