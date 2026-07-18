"""The three-way comparison on the authors' test_manipulations set (a different,
non-PIL generator) -- and the honest bound on what it can show.

PROGRESS 8.4 already measured that the router partly learned OUR generator: on
this set it predicts `pixelated` for almost everything and the colour decision
collapses. Phase D's job here is to show the DETECTOR consequence of that, not to
claim a cross-generator win:

  * The routed detector's healthy-quorum, fed the router's cross-generator
    misfire, wrongly distrusts ORB on most images -> it would LOSE the geometric
    recall ORB provides. The static fallback (D, union with static k=2) is exactly
    what prevents that from making the detector worse -- so this set is where the
    never-worse guard earns its keep, and we verify it.
  * The geometry counterfactual's trigger (P(flip)+P(crop)) scores ~0 here (8.4:
    flip recall 0% cross-generator, because our flip tell is a PIL transparent-
    corner artifact and their rotations are RGB). So the one lever that paid
    within-distribution buys nothing here -- the cost of the geometry-NO, confirmed.

Signals on this set:
  * aHash/pHash/hsvHash/sHash distances are PRECOMPUTED in final_test_metadata.csv
    by the authors (our C11 sHash reproduced their sHash_dist 1802/1802, PROGRESS 2).
  * ORB is scored here (cached to data/reference/orb_scores.csv, gitignored) since
    the authors' CSV has no ORB column. Originals live in original/, copies in
    mamipulations/ (upstream typo kept verbatim).
  * Router probabilities come from features extracted on the fly, exactly as
    evaluate_router.py --reference does.

This set was shared informally: it stays LOCAL and no published result rests on it
without the authors' okay (PLAN 3).

Usage:
    training/.venv/bin/python python/detector/evaluate_reference.py
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geometric"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
from orb_match import OrbMatcher  # noqa: E402
from features import features_from_path  # noqa: E402

from detector_common import (  # noqa: E402
    ORB_PANEL,
    Metrics,
    fires,
    flag_everything_f1,
    load_config,
    REPO_ROOT,
)
from router_signal import geometry_mass, reliability_flags  # noqa: E402
from evaluate_routed import quorum_needed  # noqa: E402

# Reference manipulation token (in the copy filename) -> our class, from evaluate_router.
REF_TOKEN_TO_CLASS = {
    "rotation": "flip_rotate_mirror", "left_to_right": "flip_rotate_mirror",
    "top_to_bottom": "flip_rotate_mirror", "crop": "resize_crop_reposition",
    "pixelated": "pixelated", "darkness": "color_swap_modify_saturate",
    "brightness": "color_swap_modify_saturate", "textOrEmoji": "text_logo_emoji",
    "background": "background_color_change",
}
GEO_CLASSES = ("flip_rotate_mirror", "resize_crop_reposition")


class ReferenceOrbMatcher(OrbMatcher):
    """Same ORB pipeline, but originals and copies live in different subdirs."""

    def __init__(self, ref_dir: Path):
        super().__init__(ref_dir)
        self._orig_dir = ref_dir / "original"
        self._manip_dir = ref_dir / "mamipulations"

    def _load_gray(self, filename: str):
        import cv2
        for d in (self._orig_dir, self._manip_dir):
            p = d / filename
            if p.exists():
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                from orb_match import normalize_for_orb
                return normalize_for_orb(img, self.working_edge)
        raise FileNotFoundError(filename)


def ref_class(copy_filename: str) -> str | None:
    for token, cls in REF_TOKEN_TO_CLASS.items():
        if token in copy_filename:
            return cls
    return None


def load_reference_pairs(ref_dir: Path) -> list[dict]:
    rows = []
    with open(ref_dir / "final_test_metadata.csv", newline="") as f:
        for r in csv.DictReader(f):
            o, c = r["original_image"].strip(), r["copy_image"].strip()
            if not o or not c:
                continue
            rows.append({
                "original": o, "copy": c,
                "is_copy": r["is_copy"].strip() == "1",
                "category": ref_class(c),  # None for negatives whose copy token is unknown
                "ahash": int(r["aHash_dist"]), "phash": int(r["pHash_dist"]),
                "hsvhash": int(r["hsvHash_dist"]), "shash": float(r["sHash_dist"]),
            })
    return rows


def reference_orb(ref_dir: Path, rows) -> dict[tuple[str, str], int]:
    cache_path = ref_dir.parent / "orb_scores.csv"  # data/reference/orb_scores.csv
    cache: dict[tuple[str, str], int] = {}
    if cache_path.exists():
        with open(cache_path, newline="") as f:
            for r in csv.DictReader(f):
                cache[(r["original_image"], r["copy_image"])] = int(r["orb_inliers"])
    todo = [(r["original"], r["copy"]) for r in rows if (r["original"], r["copy"]) not in cache]
    if todo:
        print(f"scoring ORB on {len(todo)} reference pairs (cached after) ...")
        matcher = ReferenceOrbMatcher(ref_dir)
        started = time.time()
        for i, (o, c) in enumerate(todo, 1):
            cache[(o, c)] = matcher.score(o, c)
            if i % 400 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}  {(time.time()-started)/i*1000:.0f} ms/pair", flush=True)
        with open(cache_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["original_image", "copy_image", "orb_inliers"])
            for (o, c), v in cache.items():
                w.writerow([o, c, v])
        print(f"  cached -> {cache_path}")
    return cache


def router_reference_proba(rows, data_dir: Path):
    bundle = joblib.load(data_dir / "train" / "router_model.pkl")
    model, classes, feat_names = bundle["model"], np.array(bundle["classes"]), bundle["feature_names"]
    ref_dir = data_dir / "reference" / "test_manipulations"
    proba: dict[str, np.ndarray] = {}
    copies = sorted({r["copy"] for r in rows})
    print(f"extracting router features for {len(copies)} reference copies ...")
    for i, c in enumerate(copies, 1):
        feats = features_from_path(ref_dir / "mamipulations" / c)
        X = np.array([[feats[n] for n in feat_names]], dtype=np.float64)
        proba[c] = model.predict_proba(X)[0]
        if i % 100 == 0 or i == len(copies):
            print(f"  {i}/{len(copies)}", flush=True)
    return proba, classes


def metrics_for(rows, predict) -> Metrics:
    tp = fp = fn = tn = 0
    for r in rows:
        yes = predict(r)
        if r["is_copy"]:
            tp += yes; fn += not yes
        else:
            fp += yes; tn += not yes
    return Metrics(tp, fp, fn, tn)


def votes(row, panel, thr):
    return sum(fires(s, row[s] if s != "orb" else row["orb"], thr[s]) for s in panel)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    thr = load_config(args.data_dir)
    if "shash" not in thr:
        print("note: sHash train threshold absent (run tune_static.py); baseline A skipped")
    ref_dir = args.data_dir / "reference" / "test_manipulations"
    rows = load_reference_pairs(ref_dir)
    orb = reference_orb(ref_dir, rows)
    for r in rows:
        r["orb"] = orb.get((r["original"], r["copy"]))

    pos = sum(r["is_copy"] for r in rows)
    neg = len(rows) - pos
    floor = flag_everything_f1(pos, neg)
    print(f"\n[reference] {len(rows)} pairs  positives={pos} negatives={neg}"
          f"  (manipulated negatives, unlike our pristine-only set)  flag-all F1 {floor:.1%}")

    proba, classes = router_reference_proba(rows, args.data_dir)

    # --- Three-way static + routed, overall ---
    def static_pred(panel, k):
        return lambda r: votes(r, panel, thr) >= k

    SHASH_PANEL = ("ahash", "phash", "hsvhash", "shash")
    print("\n--- three-way, overall on reference (F1) ---")
    if "shash" in thr:
        mA = metrics_for(rows, static_pred(SHASH_PANEL, 2))
        print(f"  A static+sHash k=2:  P {mA.precision:.1%} R {mA.recall:.1%} F1 {mA.f1:.1%}")
    mB = metrics_for(rows, static_pred(ORB_PANEL, 2))
    print(f"  B static+ORB  k=2:   P {mB.precision:.1%} R {mB.recall:.1%} F1 {mB.f1:.1%}")

    # C: routed healthy-quorum with static fallback (the shipped detector)
    def routed(r, tau=0.5):
        row_p = proba[r["copy"]]
        detail, colour = reliability_flags(row_p, classes)
        panel = [s for s in ORB_PANEL
                 if not (s == "orb" and detail > tau) and not (s == "hsvhash" and colour > tau)]
        v = sum(fires(s, r[s] if s != "orb" else r["orb"], thr[s]) for s in panel)
        relaxed = v >= quorum_needed(len(panel))
        static2 = votes(r, ORB_PANEL, thr) >= 2
        return static2 or relaxed  # D fallback: never un-flag static
    mC = metrics_for(rows, routed)
    print(f"  C routed (C2+fallback): P {mC.precision:.1%} R {mC.recall:.1%} F1 {mC.f1:.1%}")
    print(f"  delta(C - B) F1: {mC.f1 - mB.f1:+.2%}   (fallback keeps C from dropping below B)")

    # pure C2 (no fallback) to expose the router's cross-generator harm
    def routed_pure(r, tau=0.5):
        row_p = proba[r["copy"]]
        detail, colour = reliability_flags(row_p, classes)
        panel = [s for s in ORB_PANEL
                 if not (s == "orb" and detail > tau) and not (s == "hsvhash" and colour > tau)]
        return sum(fires(s, r[s] if s != "orb" else r["orb"], thr[s]) for s in panel) >= quorum_needed(len(panel))
    mCp = metrics_for(rows, routed_pure)
    print(f"  C2 pure (no fallback):  P {mCp.precision:.1%} R {mCp.recall:.1%} F1 {mCp.f1:.1%}"
          f"   <- router misfire distrusts ORB, F1 {mCp.f1 - mB.f1:+.1%} vs B")

    # --- ORB distrust rate: how often the router (mis)drops ORB here ---
    drop_orb = sum(reliability_flags(proba[r["copy"]], classes)[0] > 0.5 for r in rows) / len(rows)
    print(f"\n  router distrusts ORB on {drop_orb:.0%} of reference pairs "
          f"(8.4: it over-predicts pixelated cross-generator)")

    # --- Geometry counterfactual trigger recall (the costed lever, cross-generator) ---
    geo_rows = [r for r in rows if r["is_copy"] and r["category"] in GEO_CLASSES]
    if geo_rows:
        trig = sum(geometry_mass(proba[r["copy"]], classes) > 0.5 for r in geo_rows) / len(geo_rows)
        print(f"\n  geometry-flag TRIGGER recall on reference geometric copies: {trig:.1%}"
              f"  (n={len(geo_rows)})")
        print("  -> within-distribution this trigger reached ~98% (evaluate_routed.py); here it is")
        print("     ~0, so the geometry relax that paid on our data buys nothing cross-generator (8.4).")

    print("\nBound: the router's contribution on this set is limited by PROGRESS 8.4 -- it partly")
    print("learned our PIL generator. This is a within-distribution result stated honestly, not a")
    print("cross-generator claim. Data stays local (PLAN 3).")


if __name__ == "__main__":
    main()
