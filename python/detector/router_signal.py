"""Router probabilities joined to detector pairs -- the bridge from Phase C to D.

The routed detector consults the router's read of the QUERY image (the copy in a
pair; original_image is the candidate reference). This maps copy_image -> the
router's class-probability vector, and derives the two soft reliability flags
Phase C defined (train_router.reliability_scores):

    P(detail_broken)  = proba[pixelated]                     -> distrust ORB
    P(colour_changed) = proba[color_swap] + proba[bg_change] -> distrust hsvHash

TRAIN vs TEST probabilities come from DIFFERENT sources on purpose:
  * test  -> model.predict_proba (the honest deployment path)
  * train -> model.oob_decision_function_ (out-of-bag). Tuning tau/quorum on the
    model's OWN in-bag predict_proba would be fitting policy to memorised training
    rows -- a leak. OOB gives each train row a prediction from the trees that did
    NOT see it, which is the closest thing to held-out probabilities on train.
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
from train_router import COLOUR_CHANGING, DETAIL_BREAKING, load_features  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def router_proba(split: str, data_dir: Path = REPO_ROOT / "data"):
    """-> (image -> proba row dict lookup, classes array). Test uses predict_proba;
    train uses the fitted model's out-of-bag decision function."""
    bundle = joblib.load(data_dir / "train" / "router_model.pkl")
    model, classes = bundle["model"], np.array(bundle["classes"])
    feat_names = bundle["feature_names"]

    images, _, _, X, names = load_features(data_dir / split / "router_features.csv")
    if names != feat_names:
        raise ValueError("feature columns differ between model and features CSV")

    if split == "train":
        proba = model.oob_decision_function_
        if proba.shape[0] != len(images):
            raise ValueError("oob_decision_function_ rows != train feature rows")
    else:
        proba = model.predict_proba(X)

    lookup = {img: proba[i] for i, img in enumerate(images)}
    return lookup, classes


def reliability_flags(proba_row: np.ndarray, classes: np.ndarray) -> tuple[float, float]:
    """(P(detail_broken), P(colour_changed)) as summed class-probability mass."""
    idx = {c: i for i, c in enumerate(classes)}
    detail = sum(proba_row[idx[c]] for c in DETAIL_BREAKING if c in idx)
    colour = sum(proba_row[idx[c]] for c in COLOUR_CHANGING if c in idx)
    return float(detail), float(colour)


def geometry_mass(proba_row: np.ndarray, classes: np.ndarray) -> float:
    """P(flip_rotate_mirror) + P(resize_crop_reposition) -- the trigger for the
    REJECTED geometry flag (PROGRESS 8.5). Used only to COST that decision: it is
    the lever that would relax the quorum on flips, and the one that cannot
    generalise cross-generator (8.4)."""
    idx = {c: i for i, c in enumerate(classes)}
    geo = 0.0
    for c in ("flip_rotate_mirror", "resize_crop_reposition"):
        if c in idx:
            geo += proba_row[idx[c]]
    return float(geo)
