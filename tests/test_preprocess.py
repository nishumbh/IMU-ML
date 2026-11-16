from dataclasses import replace
from pathlib import Path
import unittest

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from knee_rehab.data_loader import Recording
from knee_rehab.preprocess import (
    PreprocessConfig, extract_features, lowpass_filter, prepare_windows,
    resample_signal, sliding_windows,
)


class PreprocessTests(unittest.TestCase):
    def test_exact_window_count_overlap_and_tail(self):
        x = np.arange(22).reshape(11, 2)
        windows = sliding_windows(x, 4, 2)
        self.assertEqual(windows.shape, (4, 4, 2))
        np.testing.assert_array_equal(windows[-1], x[6:10])

    def test_invalid_config(self):
        for config in [PreprocessConfig(overlap=1), PreprocessConfig(cutoff_hz=25),
                       PreprocessConfig(window_seconds=0), PreprocessConfig(target_rate_hz=float("nan")),
                       PreprocessConfig(filter_order=2.5), PreprocessConfig(overlap=-0.1)]:
            with self.assertRaises(ValueError):
                config.validate()

    def test_filter_preserves_dc_and_causal_prefix(self):
        x = np.full((1000, 2), 3.0)
        np.testing.assert_allclose(lowpass_filter(x, 100, 10), x, atol=1e-12)
        x[500:] += 2
        y = lowpass_filter(x, 100, 10)
        np.testing.assert_allclose(y[:500], lowpass_filter(x[:500], 100, 10))

    def test_filter_attenuates_high_frequency(self):
        t = np.arange(3000) / 100
        low = np.sin(2 * np.pi * 2 * t)
        high = np.sin(2 * np.pi * 35 * t)
        y = lowpass_filter((low + high)[:, None], 100, 10)[:, 0]
        high_component = 2 * abs(np.mean(y[1000:] * np.exp(-2j * np.pi * 35 * t[1000:])))
        low_component = 2 * abs(np.mean(y[1000:] * np.exp(-2j * np.pi * 2 * t[1000:])))
        self.assertLess(high_component, 0.01)
        self.assertGreater(low_component, 0.99)

    def test_resampling_uses_antialiasing(self):
        t = np.arange(4000) / 200
        x = np.sin(2 * np.pi * 40 * t)[:, None]
        y = resample_signal(x, 200, 50)
        self.assertEqual(len(y), 1000)
        self.assertLess(np.std(y[30:-30]), 0.01)
        np.testing.assert_allclose(resample_signal(np.ones((40, 2)), 100, 50), 1, atol=1e-12)

    def test_filter_can_be_run_in_streaming_chunks(self):
        rng = np.random.default_rng(15)
        x = rng.normal(size=(250, 3))
        sos = butter(4, 10, fs=50, btype="lowpass", output="sos")
        state = sosfilt_zi(sos)[:, :, None] * x[0][None, None, :]
        out = []
        for chunk in np.array_split(x, 5):
            y, state = sosfilt(sos, chunk, axis=0, zi=state)
            out.append(y)
        np.testing.assert_allclose(np.concatenate(out), lowpass_filter(x, 50, 10))

    def test_recording_boundaries_and_subjects_are_preserved(self):
        first = Recording(np.ones((200, 2)), ("acc_x", "gyro_x"), "p1", "synthetic",
                          "Correct Form", "r1", 50, Path("a.npy"))
        second = replace(first, data=np.full((200, 2), 8.0), subject_id="p2", label="Wrong", recording_id="r2")
        batch = prepare_windows([first, second])
        self.assertEqual(batch.X.shape, (6, 100, 2))
        np.testing.assert_allclose(batch.X[:3], 1)
        np.testing.assert_allclose(batch.X[3:], 8)
        self.assertEqual(batch.subject_ids.tolist(), ["p1"] * 3 + ["p2"] * 3)
        self.assertEqual(batch.y.tolist(), ["Correct Form"] * 3 + ["Wrong"] * 3)

    def test_constant_features_are_finite(self):
        values, names = extract_features(np.full((2, 100, 3), 2.0), 50)
        self.assertEqual(values.shape, (2, 42))
        self.assertEqual(len(names), 42)
        self.assertEqual(len(set(names)), 42)
        np.testing.assert_allclose(values[0, :9], [2, 0, 0, 0, 0, 2, 2, 2, 0])
        self.assertTrue(np.isfinite(values).all())

    def test_goertzel_matches_direct_complex_projection(self):
        rng = np.random.default_rng(13)
        x = rng.normal(size=(2, 103, 2))
        frequencies = (0.5, 1.0, 2.0, 3.0, 5.0)
        features, _ = extract_features(x, 50, frequencies)
        centered = x - x.mean(axis=1, keepdims=True)
        for channel in range(2):
            for j, frequency in enumerate(frequencies):
                projection = np.sum(centered[:, :, channel] * np.exp(-2j * np.pi * frequency * np.arange(103) / 50), axis=1)
                expected = abs(projection) ** 2 / 103 ** 2
                np.testing.assert_allclose(features[:, channel * 14 + 9 + j], expected, rtol=1e-5, atol=1e-8)

    def test_bad_input_and_out_of_band_probe(self):
        with self.assertRaises(ValueError):
            extract_features(np.ones((1, 100, 3)), 10)
        with self.assertRaises(ValueError):
            extract_features(np.ones((100, 3)), 50)
        with self.assertRaises(ValueError):
            resample_signal(np.array([[1.0], [np.nan]]), 100, 50)

    def test_short_recording_is_reported_not_padded(self):
        record = Recording(np.ones((10, 2)), ("a", "g"), "p", "synthetic", "Wrong", "r", 50, Path("a"))
        with self.assertWarns(UserWarning):
            with self.assertRaisesRegex(ValueError, "No full windows"):
                prepare_windows([record])


if __name__ == "__main__":
    unittest.main()
