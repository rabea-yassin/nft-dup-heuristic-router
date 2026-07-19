"""Multi-label reliability heads joined to detector pairs (Phase E).

Phase C's single-label router forces the 8 classes to compete, so a strong
signature monopolizes the soft mass and a second flag cannot fire on a composite
(probe_multi.py: colour flag 0% whenever pixelation is present). train_multilabel.py
fits two INDEPENDENT binary heads; this maps copy_image -> (P_detail, P_colour),
read like router_signal.reliability_flags but WITHOUT the winner-take-all
normalization, so both flags can fire on one image.

    P_detail  -> distrust ORB & sHash   (pixelation breaks both -- Chunk 1 / PROGRESS 5)
    P_colour  -> distrust hsvHash        (colour edits make it noise -- PROGRESS 8.3)
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
from train_router import load_features  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def multilabel_proba(split: str, data_dir: Path = REPO_ROOT / "data") -> dict[str, tuple[float, float]]:
    """-> {copy_image: (P_detail, P_colour)}. predict_proba on the split's features;
    the heads never trained on composites, so this is honest on the composite axis
    (mild base-image overlap on multi_train only, used for tuning not reporting)."""
    bundle = joblib.load(data_dir / "train" / "router_multilabel.pkl")
    detail, colour, feat_names = bundle["detail"], bundle["colour"], bundle["feature_names"]
    images, _, _, X, names = load_features(data_dir / split / "router_features.csv")
    if names != feat_names:
        raise ValueError("feature columns differ between model and features CSV")
    p_detail = detail.predict_proba(X)[:, 1]
    p_colour = colour.predict_proba(X)[:, 1]
    return {img: (float(p_detail[i]), float(p_colour[i])) for i, img in enumerate(images)}
