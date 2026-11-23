"""Fixed-label LOSO reporting; incorrect-form false acceptance is safety-critical."""
import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

REJECT = "__REJECT__"


def predict_labels(probabilities, labels, correct_label, threshold=None):
    """Reject a winning Correct Form prediction below threshold; never promote others."""
    labels = np.asarray(labels, dtype=str)
    probabilities = np.asarray(probabilities)
    if len(labels) < 2 or len(set(labels)) != len(labels) or correct_label not in labels or REJECT in labels:
        raise ValueError("Need unique class labels including correct_label; __REJECT__ is reserved")
    if probabilities.ndim != 2 or probabilities.shape[1] != len(labels):
        raise ValueError("Probability columns must match fixed labels")
    if not np.all(np.isfinite(probabilities)):
        raise ValueError("Nonfinite probabilities")
    if np.any(probabilities < 0) or np.any(probabilities > 1) or not np.allclose(probabilities.sum(axis=1), 1, atol=1e-5):
        raise ValueError("Expected normalized class probabilities in [0, 1]")
    pred = labels[np.argmax(probabilities, axis=1)].astype(object)
    if threshold is not None:
        if not 0 <= threshold <= 1:
            raise ValueError("Correct-class threshold must be in [0, 1]")
        confidence = probabilities[:, list(labels).index(correct_label)]
        pred[(pred == correct_label) & (confidence < threshold)] = REJECT
    return pred


def classification_report(y_true, y_pred, labels, correct_label):
    """All non-correct ground truths, including unknown labels, are unsafe."""
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    labels = list(labels)
    if len(set(labels)) != len(labels) or correct_label not in labels:
        raise ValueError("Report labels must be unique and include correct_label")
    if len(y_true) != len(y_pred) or not len(y_true):
        raise ValueError("Expected nonempty, equally sized predictions and truths")
    if not set(y_true).union(y_pred).issubset(labels):
        raise ValueError("Fixed report labels must include every truth and prediction")
    p, r, f, s = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0)
    incorrect = y_true != correct_label
    denominator = int(incorrect.sum())
    false_accepts = int(((y_pred == correct_label) & incorrect).sum())
    return {
        "labels": labels,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        "accuracy": float(np.mean(y_true == y_pred)),
        "per_class": {label: {"precision": float(p[i]), "recall": float(r[i]),
                              "f1": float(f[i]), "support": int(s[i])}
                      for i, label in enumerate(labels)},
        "false_acceptance": {"count": false_accepts, "actual_incorrect": denominator,
                             "rate": false_accepts / denominator if denominator else None},
    }


def evaluate_predictions(y, probabilities, labels, correct_label, threshold=None):
    """Return raw and, if requested, conservative rejection-policy metrics."""
    result = {"raw": classification_report(
        y, predict_labels(probabilities, labels, correct_label), labels, correct_label)}
    if threshold is not None:
        result["conservative"] = classification_report(
            y, predict_labels(probabilities, labels, correct_label, threshold),
            list(labels) + [REJECT], correct_label)
        result["correct_probability_threshold"] = threshold
    return result


def loso_splits(subject_ids):
    subjects = np.asarray(subject_ids)
    if len(np.unique(subjects)) < 2:
        raise ValueError("LOSO requires at least two actual participants")
    for subject in np.unique(subjects):
        yield str(subject), np.flatnonzero(subjects != subject), np.flatnonzero(subjects == subject)


EVALUATION_LIMITATIONS = (
    "Window-level LOSO; correlated windows are not independent observations. "
    "Thresholds and hyperparameters must be prespecified: using these same folds "
    "for model comparison/selection is not unbiased performance estimation. "
    "An independent prospective participant test is required. Unknown/unsafe "
    "ground-truth labels count as incorrect, not Correct Form; this is not an "
    "out-of-distribution detector and unknown motion can still be falsely accepted."
)
