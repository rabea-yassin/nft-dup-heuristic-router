"""Evaluate the router -- per category and per reliability decision, never as one
headline accuracy number.

Three views, in order of what actually matters for Phase D:

  1. The 8x8 confusion matrix + per-class precision/recall/confidence. This
     surfaces the one confusion we EXPECT and accept: exact_copy vs non_duplicate.
     From the query image alone both are pristine real NFTs -- provably
     indistinguishable -- and it does not matter, because both mean "every signal
     is trustworthy". We quantify that confusion rather than hide it.

  2. The reliability headline: the two soft-mass decisions the detector consumes,
     "is detail broken?" (=> distrust ORB) and "did colour change?" (=> distrust
     hsvHash). The dangerous error is calling a truly-pixelated image "detail
     intact", because that wrongly trusts ORB; it is reported explicitly.

  3. Per collection, because CryptoPunks are 24x24 pixel art and behave unlike
     the 256 px azuki/bayc artwork -- the router's hard case, same as ORB's.

--reference runs the SAME model against the authors' test_manipulations set (a
different, non-PIL generator) to check that the features learned real forensic
traces and not just how our own PIL pipeline makes images (PROGRESS.md 7).

Usage:
    training/.venv/bin/python python/router/evaluate_router.py --split test
    training/.venv/bin/python python/router/evaluate_router.py --reference
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import features_from_path  # noqa: E402
from train_router import (  # noqa: E402
    COLOUR_CHANGING,
    DETAIL_BREAKING,
    load_features,
    reliability_scores,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

ABBREV = {
    "background_color_change": "bg_change",
    "color_swap_modify_saturate": "color_swap",
    "exact_copy": "exact",
    "flip_rotate_mirror": "flip_rot",
    "non_duplicate": "non_dup",
    "pixelated": "pixel",
    "resize_crop_reposition": "crop",
    "text_logo_emoji": "text",
}

# Reference-set manipulation is encoded in the copy filename; map to our classes.
REF_TOKEN_TO_CLASS = {
    "rotation": "flip_rotate_mirror",
    "left_to_right": "flip_rotate_mirror",
    "top_to_bottom": "flip_rotate_mirror",
    "crop": "resize_crop_reposition",
    "pixelated": "pixelated",
    "darkness": "color_swap_modify_saturate",
    "brightness": "color_swap_modify_saturate",
    "textOrEmoji": "text_logo_emoji",
    "background": "background_color_change",
}


def load_model(path: Path):
    bundle = joblib.load(path)
    return bundle["model"], np.array(bundle["classes"]), bundle["feature_names"]


def print_confusion(y_true, y_pred, classes) -> None:
    idx = {c: i for i, c in enumerate(classes)}
    m = np.zeros((len(classes), len(classes)), dtype=int)
    for t, p in zip(y_true, y_pred):
        m[idx[t], idx[p]] += 1
    labels = [ABBREV.get(c, c) for c in classes]
    print("\nConfusion matrix (rows = true, cols = predicted):")
    print("  " + " " * 10 + "".join(f"{l:>10}" for l in labels))
    for i, c in enumerate(classes):
        print(f"  {ABBREV.get(c, c):<10}" + "".join(f"{m[i, j]:>10}" for j in range(len(classes))))


def print_per_class(y_true, y_pred, proba, classes) -> None:
    conf = proba.max(axis=1)
    print("\nPer class (true):")
    print(f"  {'class':<12}{'n':>6}{'precision':>11}{'recall':>9}{'mean_conf':>11}")
    for c in classes:
        true_mask = y_true == c
        pred_mask = y_pred == c
        support = int(true_mask.sum())
        tp = int((true_mask & pred_mask).sum())
        precision = tp / max(1, int(pred_mask.sum()))
        recall = tp / max(1, support)
        mean_conf = float(conf[true_mask].mean()) if support else 0.0
        print(f"  {ABBREV.get(c, c):<12}{support:>6}{precision:>10.1%}{recall:>9.1%}{mean_conf:>11.3f}")


def report_expected_confusion(y_true, y_pred) -> None:
    """Quantify the pristine-class confusion we designed for and accept.

    The headline is not multiclass recall on the pristine classes (which is low,
    and provably must be), but whether their misclassifications stay RELIABILITY-
    SAFE: a pristine image predicted as text/crop/flip still yields "trust every
    signal", which is the correct instruction. Only a prediction of pixelated or a
    colour class would mislead the detector.
    """
    pristine = {"exact_copy", "non_duplicate"}
    reliability_unsafe = set(DETAIL_BREAKING) | set(COLOUR_CHANGING)
    mask = np.isin(y_true, list(pristine))
    n = int(mask.sum())
    if not n:
        return
    into_pristine = int(np.isin(y_pred[mask], list(pristine)).sum())
    safe = int((~np.isin(y_pred[mask], list(reliability_unsafe))).sum())
    print(
        f"\nExpected/harmless confusion -- exact_copy vs non_duplicate:\n"
        f"  {n} truly-pristine images. Separating them from one image is impossible (identical\n"
        f"  pixels), so multiclass recall on them is low BY DESIGN -- {into_pristine} ({into_pristine/n:.1%})"
        f" land in one of the two\n  pristine classes. What matters instead: {safe} ({safe/n:.1%}) get a"
        f" RELIABILITY-SAFE verdict\n  (predicted class implies 'trust every signal'), so the confusion"
        f" costs the detector nothing."
    )


def binary_decision(name, truth_label, scores, truth_mask) -> None:
    """Report a soft-mass reliability decision as a threshold sweep + operating point.

    truth_mask marks the images the decision should fire on (e.g. truly pixelated
    for 'detail broken'). We report the best-F1 threshold and the natural tau=0.5,
    and call out the dangerous miss explicitly.
    """
    pos = truth_mask
    neg = ~truth_mask
    n_pos, n_neg = int(pos.sum()), int(neg.sum())

    def row(tau):
        flagged = scores > tau
        tp = int((flagged & pos).sum())
        fp = int((flagged & neg).sum())
        fn = n_pos - tp
        precision = tp / max(1, tp + fp)
        recall = tp / max(1, n_pos)
        f1 = 2 * precision * recall / max(1e-9, precision + recall)
        miss = fn / max(1, n_pos)  # truth-positive called "safe" -- the dangerous one
        false_alarm = fp / max(1, n_neg)
        return precision, recall, f1, miss, false_alarm

    taus = np.round(np.linspace(0.05, 0.95, 19), 2)
    best_tau = max(taus, key=lambda t: row(t)[2])
    print(f"\nReliability decision: \"{name}\"   (truth = {truth_label};  {n_pos} pos / {n_neg} neg)")
    print(f"  {'tau':>6}{'P':>9}{'R':>9}{'F1':>9}{'miss(dangerous)':>18}{'false_alarm':>13}")
    for tag, tau in (("0.50", 0.50), (f"best={best_tau:g}", best_tau)):
        p, r, f1, miss, fa = row(tau)
        print(f"  {tag:>6}{p:>8.1%}{r:>8.1%}{f1:>8.1%}{miss:>17.1%}{fa:>13.1%}")


def print_reliability(y_true, proba, classes) -> None:
    rel = reliability_scores(proba, classes)
    binary_decision(
        "is detail broken? -> distrust ORB",
        " | ".join(DETAIL_BREAKING),
        rel["p_detail_broken"],
        np.isin(y_true, list(DETAIL_BREAKING)),
    )
    binary_decision(
        "did colour change? -> distrust hsvHash",
        " | ".join(COLOUR_CHANGING),
        rel["p_colour_changed"],
        np.isin(y_true, list(COLOUR_CHANGING)),
    )


def print_per_collection(y_true, y_pred, proba, collections, classes) -> None:
    rel = reliability_scores(proba, classes)
    detail_truth = np.isin(y_true, list(DETAIL_BREAKING))
    colour_truth = np.isin(y_true, list(COLOUR_CHANGING))
    print("\nPer collection (multiclass acc, and reliability recall at tau=0.5):")
    print(f"  {'collection':<12}{'n':>7}{'acc':>8}{'detail R':>11}{'colour R':>11}")
    for coll in sorted(set(collections)):
        m = collections == coll
        acc = float((y_true[m] == y_pred[m]).mean())
        dt = detail_truth & m
        ct = colour_truth & m
        dr = float((rel["p_detail_broken"][dt] > 0.5).mean()) if dt.any() else float("nan")
        cr = float((rel["p_colour_changed"][ct] > 0.5).mean()) if ct.any() else float("nan")
        print(f"  {coll:<12}{int(m.sum()):>7}{acc:>8.1%}{dr:>11.1%}{cr:>11.1%}")


# --------------------------------------------------------------------------- #
# Reference set (authors' test_manipulations): a different, non-PIL generator. #
# --------------------------------------------------------------------------- #
def ref_label(copy_filename: str) -> str | None:
    for token, cls in REF_TOKEN_TO_CLASS.items():
        if token in copy_filename:
            return cls
    return None


def evaluate_reference(model, classes, feature_names, data_dir: Path, limit: int | None) -> None:
    ref_dir = data_dir / "reference" / "test_manipulations"
    images_dir = ref_dir / "mamipulations"  # upstream typo, kept verbatim
    meta = ref_dir / "final_test_metadata.csv"

    seen: dict[str, str] = {}
    skipped_token = 0
    with open(meta, newline="") as f:
        for row in csv.DictReader(f):
            copy = row["copy_image"].strip()
            if not copy or copy in seen:
                continue
            label = ref_label(copy)
            if label is None:
                skipped_token += 1
                continue
            seen[copy] = label
    items = list(seen.items())
    if limit:
        items = items[:limit]

    print(f"\n=== Reference set (authors' test_manipulations, a different generator) ===")
    print(f"punk-only, RGB, no exact_copy/non_duplicate classes; {len(items)} labeled images"
          f"  ({skipped_token} rows had no recognised manipulation token)")

    X, y, missing = [], [], 0
    for copy, label in items:
        path = images_dir / copy
        if not path.exists():
            missing += 1
            continue
        feats = features_from_path(path)
        X.append([feats[name] for name in feature_names])
        y.append(label)
    if missing:
        print(f"  note: {missing} image files not found on disk, skipped")
    X = np.array(X, dtype=np.float64)
    y = np.array(y)

    proba = model.predict_proba(X)
    y_pred = classes[proba.argmax(axis=1)]

    present = [c for c in classes if c in set(y)]
    print_confusion(y, y_pred, np.array(sorted(present)))
    print_per_class(y, y_pred, proba, np.array(sorted(present)))
    print_reliability(y, proba, classes)
    print("\nCaveat: the transparent-corner feature is a PIL artifact of our own rotations;"
          " it reads ~0 here, so rotation recall on this set is the honest test of whether the"
          " router leaned on that artifact.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--reference", action="store_true", help="evaluate the authors' set")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    model_path = args.model or (args.data_dir / "train" / "router_model.pkl")
    model, classes, feature_names = load_model(model_path)

    if args.reference:
        evaluate_reference(model, classes, feature_names, args.data_dir, args.limit)
        return

    images, y_true, collections, X, feat_names = load_features(
        args.data_dir / args.split / "router_features.csv"
    )
    if feat_names != feature_names:
        raise ValueError("feature columns differ between model and features CSV")
    proba = model.predict_proba(X)
    y_pred = classes[proba.argmax(axis=1)]

    print(f"split={args.split}   {len(y_true)} images   overall multiclass acc="
          f"{(y_true == y_pred).mean():.1%}  (not the headline -- see reliability below)")
    print_confusion(y_true, y_pred, classes)
    print_per_class(y_true, y_pred, proba, classes)
    report_expected_confusion(y_true, y_pred)
    print_reliability(y_true, proba, classes)
    print_per_collection(y_true, y_pred, proba, collections, classes)


if __name__ == "__main__":
    main()
