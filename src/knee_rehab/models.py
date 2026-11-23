"""Optional compact temporal CNN using Conv2D TFLM builtins; TF imports are lazy."""
import numpy as np

from .evaluate import evaluate_predictions, loso_splits


def fit_scaling(windows):
    """Per-channel standardization; call with training windows ONLY."""
    mean = np.mean(windows, axis=(0, 1), dtype=np.float64)
    std = np.std(windows, axis=(0, 1), dtype=np.float64)
    return {"mean": mean.tolist(), "scale": np.maximum(std, 1e-6).tolist(),
            "formula": "scaled[t,c] = (raw[t,c] - mean[c]) / scale[c]"}


def scale_windows(windows, scaling):
    return ((np.asarray(windows, dtype=np.float32) -
             np.asarray(scaling["mean"], dtype=np.float32)) /
            np.asarray(scaling["scale"], dtype=np.float32)).astype(np.float32)


def build_cnn(input_shape, n_classes, seed=42):
    """Temporal convolutions expressed as Conv2D, without normalization operators."""
    import tensorflow as tf
    tf.keras.utils.set_random_seed(seed)
    time, channels = input_shape
    if time < 4:
        raise ValueError("CNN requires at least four time samples per window")
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=input_shape),
        tf.keras.layers.Reshape((time, channels, 1)),
        tf.keras.layers.Conv2D(8, (5, 1), padding="same", activation="relu"),
        tf.keras.layers.MaxPooling2D((2, 1)),
        tf.keras.layers.Conv2D(12, (3, 1), padding="same", activation="relu"),
        tf.keras.layers.MaxPooling2D((2, 1)),
        tf.keras.layers.Flatten(),
        tf.keras.layers.Dense(n_classes, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy")
    return model


def fit_loso(windows, y, subject_ids, labels, correct_label, epochs=15,
             batch_size=32, threshold=None, seed=42):
    """Fixed epochs, no held-out-subject selection or early stopping.

    Return (CV report, final Keras model, final training-only scaling).
    """
    import tensorflow as tf
    if epochs < 1 or batch_size < 1:
        raise ValueError("epochs and batch_size must be positive")
    y = np.asarray(y)
    indices = np.asarray([list(labels).index(label) for label in y])
    probabilities = np.zeros((len(y), len(labels)))
    folds = []
    for subject, train, test in loso_splits(subject_ids):
        if len(np.unique(y[train])) < 2:
            raise ValueError(f"LOSO fold holding out {subject!r} has fewer than two training classes")
        tf.keras.backend.clear_session()
        scaling = fit_scaling(windows[train])
        model = build_cnn(windows.shape[1:], len(labels), seed)
        model.fit(scale_windows(windows[train], scaling), indices[train],
                  epochs=epochs, batch_size=batch_size, verbose=0)
        fold_prob = model.predict(scale_windows(windows[test], scaling), verbose=0)
        probabilities[test] = fold_prob
        folds.append({"held_out_subject": subject, "train_windows": len(train),
                      "test_windows": len(test),
                      "missing_training_classes": sorted(set(labels) - set(y[train])),
                      "missing_test_classes": sorted(set(labels) - set(y[test])),
                      "metrics": evaluate_predictions(y[test], fold_prob, labels,
                                                      correct_label, threshold)})
    tf.keras.backend.clear_session()
    scaling = fit_scaling(windows)
    final = build_cnn(windows.shape[1:], len(labels), seed)
    final.fit(scale_windows(windows, scaling), indices, epochs=epochs,
              batch_size=batch_size, verbose=0)
    report = {"folds": folds, "pooled": evaluate_predictions(
        y, probabilities, labels, correct_label, threshold), "epochs": epochs,
        "final_fit": "ALL input subjects; deployment model only, not evaluation"}
    return report, final, scaling
