"""Train per-manipulation BINARY reliability heads (Phase E).

The single-label router (Phase C) reads soft mass over 8 COMPETING classes, so a
strong signature monopolizes the probability vector: on a pixelate+colour composite
it puts ~0.95 on `pixelated` and ~0 on the colour classes, and the colour flag never
fires (Chunk 3 probe, probe_multi.py). Two independent binary heads remove that
competition -- each answers "is MY manipulation present?" on its own, so both can
fire on a single composite.

Two heads, trained on the SAME single-manip train features (no new vision pass, and
the deployable story stays "train on single manips, deploy on anything"):
  * detail : P(pixelated)                                        -> distrust ORB & sHash
  * colour : P(color_swap_modify_saturate OR background_change)  -> distrust hsvHash

Same model family (RandomForest) and 93-feature set as the single-label router; the
groupings are Phase C's own (DETAIL_BREAKING / COLOUR_CHANGING). The single-label
router is kept untouched for Phase C/D reproducibility -- this is a separate bundle.

Usage:
    training/.venv/bin/python python/router/train_multilabel.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np

from train_router import COLOUR_CHANGING, DETAIL_BREAKING, build_model, load_features

REPO_ROOT = Path(__file__).resolve().parents[2]

# name -> the manipulation classes that count as a positive for that binary head.
HEADS = {"detail": set(DETAIL_BREAKING), "colour": set(COLOUR_CHANGING)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    images, y, collections, X, feature_names = load_features(args.data_dir / "train" / "router_features.csv")
    print(f"loaded {len(y)} single-manip train images x {len(feature_names)} features")

    heads = {}
    for name, positive in HEADS.items():
        yb = np.array([1 if lbl in positive else 0 for lbl in y], dtype=int)
        clf = build_model(args.random_state)  # RF, class_weight balanced, oob_score
        clf.fit(X, yb)
        heads[name] = clf
        print(f"  head '{name}': positives={int(yb.sum())}/{len(yb)} "
              f"({yb.mean():.1%})   oob_accuracy={clf.oob_score_:.3f}   "
              f"classes={sorted(positive)}")

    bundle = {"detail": heads["detail"], "colour": heads["colour"],
              "feature_names": feature_names}
    out = args.data_dir / "train" / "router_multilabel.pkl"
    joblib.dump(bundle, out)
    print(f"saved -> {out}")

    # --- sanity: per-category fire rate on the single-manip TEST split ----------
    # each head should fire ~100% on ITS category and stay low elsewhere; this is a
    # within-distribution check before the heads are applied to composites.
    ti, ty, tcoll, tX, tnames = load_features(args.data_dir / "test" / "router_features.csv")
    if tnames != feature_names:
        raise ValueError("test feature columns differ from train")
    proba = {name: clf.predict_proba(tX)[:, 1] for name, clf in heads.items()}
    cats = sorted(set(ty))
    print("\nsanity -- fire rate (P>0.5) per single-manip TEST category:")
    print(f"  {'category':<28}{'n':>6}{'detail':>9}{'colour':>9}")
    for c in cats:
        mask = ty == c
        n = int(mask.sum())
        d = (proba["detail"][mask] > 0.5).mean()
        col = (proba["colour"][mask] > 0.5).mean()
        print(f"  {c:<28}{n:>6}{d:>8.1%}{col:>8.1%}")


if __name__ == "__main__":
    main()
