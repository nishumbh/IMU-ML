"""Feature-based LOSO baselines. No TensorFlow dependency."""
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from .evaluate import evaluate_predictions, loso_splits


def make_estimator(kind="rf", seed=42):
    if kind not in ("rf", "svm"):
        raise ValueError("Baseline must be rf or svm")
    if kind == "rf":
        classifier = RandomForestClassifier(
            n_estimators=64, max_depth=6, min_samples_leaf=2,
            class_weight="balanced", random_state=seed, n_jobs=-1)
    else:
        classifier = SVC(C=1.0, kernel="rbf", probability=True,
                         class_weight="balanced", random_state=seed)
    # The scaler is fitted only by each training-fold Pipeline.fit call.
    return Pipeline([("scale", StandardScaler() if kind == "svm" else "passthrough"),
                     ("classifier", classifier)])


def aligned_probabilities(estimator, features, labels):
    probabilities = np.zeros((len(features), len(labels)), dtype=np.float64)
    local = estimator.predict_proba(features)
    for column, label in enumerate(estimator.classes_):
        probabilities[:, list(labels).index(label)] = local[:, column]
    return probabilities


def fit_loso(features, y, subject_ids, labels, correct_label, kind="rf",
             threshold=None, seed=42):
    """Return (CV report, final estimator). Final fit is NOT an evaluation."""
    features, y = np.asarray(features), np.asarray(y)
    predictions = np.zeros((len(y), len(labels)))
    folds = []
    for subject, train, test in loso_splits(subject_ids):
        if len(np.unique(y[train])) < 2:
            raise ValueError(f"LOSO fold holding out {subject!r} has fewer than two training classes")
        model = make_estimator(kind, seed)
        model.fit(features[train], y[train])
        probabilities = aligned_probabilities(model, features[test], labels)
        predictions[test] = probabilities
        folds.append({"held_out_subject": subject, "train_windows": len(train),
                      "test_windows": len(test),
                      "missing_training_classes": sorted(set(labels) - set(y[train])),
                      "missing_test_classes": sorted(set(labels) - set(y[test])),
                      "metrics": evaluate_predictions(y[test], probabilities, labels,
                                                      correct_label, threshold)})
    final = make_estimator(kind, seed).fit(features, y)
    return {"folds": folds, "pooled": evaluate_predictions(
        y, predictions, labels, correct_label, threshold),
        "final_fit": "ALL input subjects; deployment model only, not evaluation"}, final
