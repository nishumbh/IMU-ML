import unittest

import numpy as np

from knee_rehab.knee_angle import (
    KneeAngleConfig,
    accel_tilt_deg,
    complementary_tilt_deg,
    estimate_knee_angle,
)

G = 9.80665
FS = 50.0


def _rot_y(theta_rad: np.ndarray) -> np.ndarray:
    c, s = np.cos(theta_rad), np.sin(theta_rad)
    z, o = np.zeros_like(c), np.ones_like(c)
    return np.stack(
        [np.stack([c, z, s], -1), np.stack([z, o, z], -1), np.stack([-s, z, c], -1)], -2
    )  # shape (n, 3, 3)


def simulate_segment(angle_deg: np.ndarray, time_s: np.ndarray, gyro_bias_deg_s: float = 0.0,
                     accel_noise: float = 0.0, gyro_noise: float = 0.0,
                     rng: np.random.Generator | None = None) -> np.ndarray:
    """Exact rigid-body IMU output for a segment rotating about its +Y axis.

    Accelerometer reads R^T (0,0,g) (reaction to gravity). The gyro is computed
    from the kinematics (skew(omega) = R^T dR/dt), so the sign is not hand-derived.
    Returns (n, 6): ax, ay, az, gx, gy, gz.
    """
    rng = rng or np.random.default_rng(0)
    theta = np.radians(angle_deg)
    R = _rot_y(theta)
    RT = np.transpose(R, (0, 2, 1))
    accel = np.einsum("nij,j->ni", RT, np.array([0.0, 0.0, G]))
    dt = 1.0 / FS
    dR = np.gradient(R, dt, axis=0)
    skew = np.einsum("nij,njk->nik", RT, dR)
    omega = np.stack([skew[:, 2, 1], skew[:, 0, 2], skew[:, 1, 0]], -1)  # (wx, wy, wz)
    gyro = omega + np.radians(gyro_bias_deg_s) * np.array([0.0, 1.0, 0.0])
    accel = accel + rng.normal(0, accel_noise, accel.shape)
    gyro = gyro + rng.normal(0, gyro_noise, gyro.shape)
    return np.hstack([accel, gyro])


class KinematicConventionTests(unittest.TestCase):
    def test_accel_tilt_matches_true_angle_when_static(self):
        t = np.arange(0, 2, 1 / FS)
        truth = np.full_like(t, 25.0)
        seg = simulate_segment(truth, t)
        np.testing.assert_allclose(accel_tilt_deg(seg[:, 0], seg[:, 2]), truth, atol=1e-6)

    def test_gyro_sign_tracks_angle_without_noise(self):
        # With exact sensors, the gyro path alone must follow the true angle.
        t = np.arange(0, 6, 1 / FS)
        truth = 30 * np.sin(2 * np.pi * 0.2 * t)  # slow enough for the discrete integrator
        seg = simulate_segment(truth, t)
        cfg = KneeAngleConfig(alpha=1.0)  # gyro only
        est = complementary_tilt_deg(seg[:, 0], seg[:, 2], seg[:, 4], t, cfg)
        # Gyro-only starts from the first accelerometer reading, so compare changes.
        # Sign errors would show as a mirrored curve (error ~ 2x amplitude), so a
        # 1 degree tolerance is a strict sign check.
        np.testing.assert_allclose(est - est[0], truth - truth[0], atol=1.0)

    def test_gyro_bias_drift_is_bounded_by_accelerometer(self):
        t = np.arange(0, 120, 1 / FS)
        truth = 20 * np.sin(2 * np.pi * 0.2 * t)
        seg = simulate_segment(truth, t, gyro_bias_deg_s=1.0)
        est = complementary_tilt_deg(seg[:, 0], seg[:, 2], seg[:, 4], t, KneeAngleConfig())
        self.assertLess(np.max(np.abs(est - truth)), 3.0)


class KneeEstimateTests(unittest.TestCase):
    def _two_segment(self, thigh_deg, shank_deg, **kw):
        t = np.arange(0, len(thigh_deg)) / FS
        return t, simulate_segment(thigh_deg, t, **kw), simulate_segment(shank_deg, t, **kw)

    def test_recovers_flexion_with_noise_and_bias(self):
        rng = np.random.default_rng(1)
        n = int(20 * FS)
        t = np.arange(n) / FS
        thigh_true = 8 + 25 * np.clip(np.sin(2 * np.pi * 0.25 * t), 0, None)
        shank_true = 3 + 2 * np.sin(2 * np.pi * 0.25 * t)
        # Standing reference: first 2 s are straight (thigh and shank equal and constant).
        thigh_true[t < 2] = 8
        shank_true[t < 2] = 3
        flex_true = (thigh_true - shank_true) - (thigh_true[0] - shank_true[0])
        thigh = simulate_segment(thigh_true, t, gyro_bias_deg_s=0.5, accel_noise=0.05,
                                 gyro_noise=0.01, rng=rng)
        shank = simulate_segment(shank_true, t, gyro_bias_deg_s=-0.5, accel_noise=0.05,
                                 gyro_noise=0.01, rng=rng)
        res = estimate_knee_angle(t, thigh, shank)
        rms = np.sqrt(np.mean((res.flexion_deg - flex_true) ** 2))
        self.assertLess(rms, 1.5, f"RMS flexion error {rms:.2f} deg")
        standing = t < 2
        self.assertLess(abs(np.median(res.flexion_deg[standing])), 1.0)
        self.assertGreater(res.peak_flexion_deg, 15.0)

    def test_flexion_increases_when_thigh_rotates_forward_relative_to_shank(self):
        t = np.arange(int(6 * FS)) / FS
        thigh = np.full_like(t, 0.0)
        shank = np.full_like(t, 0.0)
        thigh[t >= 3] = 20.0  # thigh moves 20 deg, shank stays still
        th = simulate_segment(thigh, t)
        sh = simulate_segment(shank, t)
        res = estimate_knee_angle(t, th, sh)
        self.assertGreater(res.flexion_deg[-1], 15.0)

    def test_standing_offset_is_removed(self):
        t = np.arange(int(4 * FS)) / FS
        thigh = np.full_like(t, 12.0)  # standing with 12 deg mounting offset on the thigh
        shank = np.full_like(t, 2.0)
        res = estimate_knee_angle(t, simulate_segment(thigh, t), simulate_segment(shank, t))
        self.assertLess(np.max(np.abs(res.flexion_deg)), 0.5)

    def test_gap_restarts_filter_from_accelerometer(self):
        t = np.arange(int(4 * FS)) / FS
        t = np.concatenate([t[: len(t) // 2], t[len(t) // 2 :] + 5.0])  # 5 s gap
        truth = np.full_like(t, 15.0)
        seg = simulate_segment(truth, np.arange(len(t)) / FS)  # gyro-free truth
        est = complementary_tilt_deg(seg[:, 0], seg[:, 2], seg[:, 4], t, KneeAngleConfig())
        np.testing.assert_allclose(est, truth, atol=0.5)

    def test_rejects_bad_shapes(self):
        t = np.arange(10) / FS
        with self.assertRaises(ValueError):
            estimate_knee_angle(t, np.zeros((10, 5)), np.zeros((10, 6)))


if __name__ == "__main__":
    unittest.main()
