"""Canonical Genesis metrics and clipped terminal reward."""


def metrics(tp: int, fp: int, fn: int, inspection_steps: int) -> dict:
    predicted = tp + fp
    expected = tp + fn
    precision = tp / predicted if predicted else 0.0
    recall = tp / expected if expected else 0.0
    denominator = 2 * tp + fp + fn
    f1 = 2 * tp / denominator if denominator else 1.0
    result = 2 * f1 - 1 - 0.002 * inspection_steps
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "return": max(-1.0, min(1.0, result)),
    }


def invalid_metrics() -> dict:
    return {
        "tp": None, "fp": None, "fn": None,
        "precision": None, "recall": None, "f1": None, "return": -1.0,
    }
