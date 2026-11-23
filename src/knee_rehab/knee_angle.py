"""Knee flexion angle and angular speed from two IMUs (thigh and shank).

Method
------
Each IMU gives the orientation of its segment in the sagittal plane:

* accelerometer tilt: with the sensor at rest the accelerometer reads the
  reaction to gravity, so the tilt is ``atan2(-ax, az)``. Accurate on average,
  but noisy and disturbed by real movement.
* gyro integration: the gyro reports the rotation rate about the flexion axis.
  Smooth and fast, but it drifts when integrated.

A complementary filter blends the two (``alpha`` on the gyro path). The knee
angle is the signed difference of the two segment angles, zeroed by averaging a
standing reference period at the start of the recording.

Conventions (checked by ``tests/test_knee_angle.py``)
-----------------------------------------------------
* Rotation is about the sensor's +Y axis (``sagittal_axis='y'``). The
  accelerometer tilt is ``atan2(-ax, az)`` and the gyro rate about +Y equals
  the derivative of that tilt. The sign is verified by simulating a rotating
  rigid body with exact kinematics, not by hand-derived formulas.
* Flexion is positive when the thigh-to-shank angle increases. Mounting both
  sensors the same way is assumed. If a flexion test moves the number the wrong
  way, set ``flexion_sign=-1`` in ``KneeAngleConfig``.

Limits
------
* Assumes the flexion axis is the sensor Y axis and both sensors share the same
  mounting. Mounting errors and skin movement are not modelled.
* The zero point is the mean of the standing reference window. This assumes the
  person really is straight at the start. Nothing here is clinically validated;
  check against a goniometer or video before using it on a patient.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DEG_PER_RAD = 180.0 / math.pi

THIGH_COLUMNS = ("thigh_ax", "thigh_ay", "thigh_az", "thigh_gx", "thigh_gy", "thigh_gz")
SHANK_COLUMNS = ("shank_ax", "shank_ay", "shank_az", "shank_gx", "shank_gy", "shank_gz")


@dataclass(frozen=True)
class KneeAngleConfig:
    """Tunable parameters. Every value here is an assumption until validated."""

    alpha: float = 0.98  # gyro weight in the complementary filter
    flexion_sign: float = 1.0  # +1: thigh-minus-shank increases with flexion
    gyro_sign: float = 1.0  # multiply gyro rate by this before integration
    standing_seconds: float = 2.0  # zero reference window at the start
    max_gap_s: float = 0.1  # longer gaps restart the filter from the accelerometer
    speed_smoothing_samples: int = 5  # moving-median window for angular speed


@dataclass
class KneeAngleResult:
    time_s: np.ndarray
    thigh_deg: np.ndarray  # filtered thigh angle, raw sensor zero
    shank_deg: np.ndarray  # filtered shank angle, raw sensor zero
    flexion_deg: np.ndarray  # calibrated knee flexion (0 = standing reference)
    speed_deg_s: np.ndarray  # angular speed of the knee, smoothed
    zero_offset_deg: float
    notes: list[str] = field(default_factory=list)

    @property
    def peak_flexion_deg(self) -> float:
        return float(np.max(self.flexion_deg)) if self.flexion_deg.size else float("nan")

    @property
    def min_flexion_deg(self) -> float:
        return float(np.min(self.flexion_deg)) if self.flexion_deg.size else float("nan")


def accel_tilt_deg(ax: np.ndarray, az: np.ndarray) -> np.ndarray:
    """Sagittal tilt from the accelerometer (valid when the sensor is not accelerating much)."""
    return np.degrees(np.arctan2(-ax, az))


def complementary_tilt_deg(
    ax: np.ndarray,
    az: np.ndarray,
    gy_rad_s: np.ndarray,
    time_s: np.ndarray,
    cfg: KneeAngleConfig,
) -> np.ndarray:
    """Complementary-filter tilt for one segment, one value per sample (degrees).

    Gaps longer than ``cfg.max_gap_s`` (for example radio loss) restart the filter
    from the accelerometer, so a stale gyro integral is never carried across a gap.
    """
    accel = accel_tilt_deg(ax, az)
    out = np.empty_like(accel)
    angle = accel[0] if accel.size else 0.0
    gyro_deg_s = np.degrees(gy_rad_s) * cfg.gyro_sign
    for i in range(accel.size):
        if i == 0:
            angle = accel[i]
        else:
            dt = time_s[i] - time_s[i - 1]
            if not np.isfinite(dt) or dt <= 0 or dt > cfg.max_gap_s:
                angle = accel[i]
            else:
                angle = cfg.alpha * (angle + gyro_deg_s[i] * dt) + (1.0 - cfg.alpha) * accel[i]
        out[i] = angle
    return out


def _moving_median(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or values.size < window:
        return values.copy()
    half = window // 2
    padded = np.pad(values, (half, half), mode="edge")
    return np.array([np.median(padded[i : i + window]) for i in range(values.size)])


def estimate_knee_angle(
    time_s: np.ndarray,
    thigh: np.ndarray,
    shank: np.ndarray,
    cfg: KneeAngleConfig = KneeAngleConfig(),
) -> KneeAngleResult:
    """Knee flexion and angular speed from IMU arrays.

    ``thigh`` and ``shank`` are ``(n, 6)`` arrays in the column order
    ``ax, ay, az, gx, gy, gz`` with accelerometer in m/s^2 and gyro in rad/s.
    ``time_s`` is seconds from the start of the recording.
    """
    time_s = np.asarray(time_s, dtype=float)
    thigh = np.asarray(thigh, dtype=float)
    shank = np.asarray(shank, dtype=float)
    if thigh.shape != shank.shape or thigh.shape[0] != time_s.size or thigh.shape[1] != 6:
        raise ValueError("thigh and shank must be (n, 6) arrays matching time_s")
    if time_s.size < 2:
        raise ValueError("need at least two samples")

    t_angle = complementary_tilt_deg(thigh[:, 0], thigh[:, 2], thigh[:, 4], time_s, cfg)
    s_angle = complementary_tilt_deg(shank[:, 0], shank[:, 2], shank[:, 4], time_s, cfg)

    raw_flex = cfg.flexion_sign * (t_angle - s_angle)

    notes: list[str] = []
    standing = time_s - time_s[0] <= cfg.standing_seconds
    if standing.sum() < 2:
        zero = float(raw_flex[0])
        notes.append("standing reference window too short; zeroed on first sample")
    else:
        zero = float(np.median(raw_flex[standing]))
    flexion = raw_flex - zero

    dt = np.gradient(time_s)
    dt[dt <= 0] = np.nan
    speed = np.gradient(flexion) / dt
    speed = _moving_median(np.nan_to_num(speed), cfg.speed_smoothing_samples)

    return KneeAngleResult(
        time_s=time_s,
        thigh_deg=t_angle,
        shank_deg=s_angle,
        flexion_deg=flexion,
        speed_deg_s=speed,
        zero_offset_deg=zero,
        notes=notes,
    )


def read_firmware_csv(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a two_imu_logger / knee_pod CSV (SI units) into time, thigh and shank arrays."""
    with Path(path).open(newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path}: no data rows")
    time_s = np.array([float(r["time_ms"]) for r in rows]) / 1000.0
    thigh = np.array([[float(r[c]) for c in THIGH_COLUMNS] for r in rows])
    shank = np.array([[float(r[c]) for c in SHANK_COLUMNS] for r in rows])
    return time_s - time_s[0], thigh, shank


def write_result_csv(result: KneeAngleResult, path: Path) -> None:
    with Path(path).open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_s", "thigh_deg", "shank_deg", "flexion_deg", "speed_deg_s"])
        for row in zip(
            result.time_s, result.thigh_deg, result.shank_deg, result.flexion_deg, result.speed_deg_s
        ):
            writer.writerow([f"{v:.4f}" for v in row])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Knee flexion angle from two-IMU CSV logs.")
    parser.add_argument("--input", type=Path, required=True, help="firmware CSV (SI units)")
    parser.add_argument("--output", type=Path, required=True, help="output CSV path (must not exist)")
    parser.add_argument("--alpha", type=float, default=KneeAngleConfig.alpha)
    parser.add_argument("--flexion-sign", type=float, default=KneeAngleConfig.flexion_sign)
    parser.add_argument("--gyro-sign", type=float, default=KneeAngleConfig.gyro_sign)
    parser.add_argument("--standing-seconds", type=float, default=KneeAngleConfig.standing_seconds)
    args = parser.parse_args(argv)

    if args.output.exists():
        parser.error(f"{args.output} exists; refusing to overwrite")
    cfg = KneeAngleConfig(
        alpha=args.alpha,
        flexion_sign=args.flexion_sign,
        gyro_sign=args.gyro_sign,
        standing_seconds=args.standing_seconds,
    )
    time_s, thigh, shank = read_firmware_csv(args.input)
    result = estimate_knee_angle(time_s, thigh, shank, cfg)
    write_result_csv(result, args.output)
    print(f"samples: {time_s.size}, duration: {time_s[-1]:.1f} s")
    print(f"zero offset (raw): {result.zero_offset_deg:.2f} deg")
    print(f"flexion range: {result.min_flexion_deg:.1f} to {result.peak_flexion_deg:.1f} deg (uncalibrated against a goniometer)")
    for note in result.notes:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
