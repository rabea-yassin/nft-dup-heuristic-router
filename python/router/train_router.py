"""Train the manipulation-type router: RandomForest -> type + confidence.

Same model family the paper uses (RandomForest), so the router is directly
comparable to it. It reads the cached feature vectors from extract_features.py
and learns to predict the manipulation class of a single query image.

The router's real output is not the 8-way label for its own sake -- it is the
signal-reliability decision Phase D needs. We derive that from the SOFT class
probability *vector*, not the hard argmax: even when no single class wins, the
probability mass sitting on the detail-destroying or colour-changing classes is
the quantity that should make the detector distrust a signal.

  P(detail_broken)  = proba[pixelated]
  P(colour_changed) = proba[color_swap_modify_saturate] + proba[background_color_change]

These groups are grounded in the measured ORB per-category table (PROGRESS.md 5):
ORB's structure matching is destroyed ONLY by pixelation and hsvHash is noise
only under colour edits. Which hashes each flag ultimately gates is settled
empirically by hash_reliability.py, not asserted here -- the router just emits
the flags.

Usage:
    training/.venv/bin/python python/router/train_router.py
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

REPO_ROOT = Path(__file__).resolve().parents[2]
META_COLS = ("image", "manipulation_type", "collection")

# The two reliability groupings, by manipulation class. See module docstring.
DETAIL_BREAKING = ("pixelated",)
COLOUR_CHANGING = ("color_swap_modify_saturate", "background_color_change")


def load_features(path: Path):
    """-> (images, labels, collections, X, feature_names)."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        feature_names = header[len(META_COLS) :]
        images, labels, collections, rows = [], [], [], []
        for r in reader:
            images.append(r[0])
            labels.append(r[1])
            collections.append(r[2])
            rows.append([float(x) for x in r[len(META_COLS) :]])
    return (
        images,
        np.array(labels),
        np.array(collections),
        np.array(rows, dtype=np.float64),
        feature_names,
    )


def reliability_scores(proba: np.ndarray, classes: np.ndarray) -> dict[str, np.ndarray]:
    """Derive the soft reliability flags from the class-probability matrix.

    proba: (n_samples, n_classes) from predict_proba; classes: model.classes_.
    Returns per-sample P(detail_broken) and P(colour_changed) as the summed
    probability mass over each class group.
    """
    index = {c: i for i, c in enumerate(classes)}

    def mass(group):
        cols = [index[c] for c in group if c in index]
        return proba[:, cols].sum(axis=1) if cols else np.zeros(proba.shape[0])

    return {"p_detail_broken": mass(DETAIL_BREAKING), "p_colour_changed": mass(COLOUR_CHANGING)}


def build_model(random_state: int) -> RandomForestClassifier:
    """The router. class_weight balances the pristine classes (~2400) against
    the manipulated ones (7200); oob_score gives an honest in-training estimate."""
    return RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",
        min_samples_leaf=2,
        n_jobs=-1,
        oob_score=True,
        bootstrap=True,
        random_state=random_state,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="train", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--top", type=int, default=25, help="feature importances to print")
    args = parser.parse_args()

    feat_path = args.data_dir / args.split / "router_features.csv"
    images, y, collections, X, feature_names = load_features(feat_path)
    print(f"loaded {len(y)} images x {len(feature_names)} features from {feat_path}")
    classes, counts = np.unique(y, return_counts=True)
    print("class counts: " + "  ".join(f"{c}={n}" for c, n in zip(classes, counts)))

    clf = build_model(args.random_state)
    clf.fit(X, y)
    print(f"\ntrained RandomForest ({clf.n_estimators} trees)   oob_accuracy={clf.oob_score_:.3f}")

    model_path = args.data_dir / args.split / "router_model.pkl"
    joblib.dump(
        {"model": clf, "feature_names": feature_names, "classes": clf.classes_.tolist()},
        model_path,
    )
    print(f"saved -> {model_path}")

    order = np.argsort(clf.feature_importances_)[::-1]
    print(f"\ntop {args.top} feature importances (drivers of the reliability decision):")
    for i in order[: args.top]:
        print(f"  {feature_names[i]:32} {clf.feature_importances_[i]:.4f}")


if __name__ == "__main__":
    main()
