import unittest
from unittest.mock import patch

import numpy as np
from sklearn.dummy import DummyClassifier

from knee_rehab.baseline import aligned_probabilities, fit_loso
from knee_rehab.evaluate import REJECT, evaluate_predictions, loso_splits, predict_labels
from knee_rehab.models import fit_scaling, scale_windows


class EvaluationTests(unittest.TestCase):
    labels = ["Correct Form", "Incomplete Extension", "Incorrect Compensation"]

    def test_false_acceptance_denominator_and_rejection(self):
        y = np.array([self.labels[0], self.labels[1], self.labels[2], self.labels[1]])
        probabilities = np.array([[0.8, 0.1, 0.1], [0.7, 0.2, 0.1], [0.95, 0.03, 0.02], [0.1, 0.8, 0.1]])
        report = evaluate_predictions(y, probabilities, self.labels, self.labels[0], 0.9)
        self.assertEqual(report["raw"]["false_acceptance"]["rate"], 2 / 3)
        self.assertEqual(report["conservative"]["false_acceptance"]["rate"], 1 / 3)
        self.assertEqual(len(report["conservative"]["confusion_matrix"]), 4)
        self.assertEqual(report["conservative"]["per_class"][REJECT]["support"], 0)

    def test_rejection_never_promotes_a_wrong_winner(self):
        result = predict_labels([[0.45, 0.5, 0.05]], self.labels, self.labels[0], 0.4)
        self.assertEqual(result[0], self.labels[1])

    def test_no_incorrect_examples_returns_null_rate(self):
        report = evaluate_predictions([self.labels[0]], [[1, 0, 0]], self.labels, self.labels[0])
        self.assertIsNone(report["raw"]["false_acceptance"]["rate"])
        self.assertEqual(np.shape(report["raw"]["confusion_matrix"]), (3, 3))

    def test_fold_disjointness_and_fold_local_fitting(self):
        subjects = np.repeat(["p1", "p2", "p3"], 6)
        y = np.tile(np.repeat(self.labels, 2), 3)
        x = np.column_stack([np.arange(18), np.arange(18) ** 2]).astype(float)
        fits = []

        class SpyClassifier(DummyClassifier):
            def fit(self, X, y):
                fits.append(set(X[:, 0].tolist()))
                return super().fit(X, y)

        with patch("knee_rehab.baseline.make_estimator", side_effect=lambda *args: SpyClassifier(strategy="prior")):
            report, _ = fit_loso(x, y, subjects, self.labels, self.labels[0])
        self.assertEqual(len(fits), 4)  # 3 CV fits + final all-input fit
        for i, (_, train, test) in enumerate(loso_splits(subjects)):
            self.assertFalse(set(subjects[train]) & set(subjects[test]))
            self.assertEqual(fits[i], set(x[train, 0]))
        self.assertEqual(len(report["folds"]), 3)

    def test_missing_training_class_probability_alignment(self):
        model = DummyClassifier(strategy="prior").fit([[0], [1]], self.labels[:2])
        probabilities = aligned_probabilities(model, [[2]], self.labels)
        np.testing.assert_allclose(probabilities, [[0.5, 0.5, 0]])

    def test_training_only_channel_scaling(self):
        train = np.arange(24).reshape(2, 6, 2)
        scaling = fit_scaling(train)
        transformed = scale_windows(train, scaling)
        np.testing.assert_allclose(transformed.mean(axis=(0, 1)), 0, atol=1e-7)
        before = dict(scaling)
        scale_windows(np.full((1, 6, 2), 1e5), scaling)
        self.assertEqual(before, scaling)

    def test_single_subject_and_single_class_training_fold_rejected(self):
        with self.assertRaises(ValueError):
            list(loso_splits(["p1", "p1"]))
        with self.assertRaisesRegex(ValueError, "two training classes"):
            fit_loso(np.ones((4, 2)), ["Correct Form", "Correct Form", "Wrong", "Wrong"],
                     ["p1", "p1", "p2", "p2"], ["Correct Form", "Wrong"], "Correct Form")

    def test_invalid_probabilities_rejected(self):
        for probabilities in ([[1.2, -0.1, -0.1]], [[0, 0, 0]], [[np.nan, 0, 0]]):
            with self.assertRaises(ValueError):
                predict_labels(probabilities, self.labels, self.labels[0])


if __name__ == "__main__":
    unittest.main()
