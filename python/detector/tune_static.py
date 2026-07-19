"""Derive the static detector's train-side knobs, write detector_config.json.

Two things are tuned on TRAIN here, nothing on test (the protocol whose violation
invalidated the imported baseline, PROGRESS 4 / 8.3):

  1. **sHash's and ORB's panel thresholds**, both at the SAME <=10% FP budget on the
     pristine train negatives that the three hashes use (8.3) -- so all four votes in a
     panel sit at one operating point and the swap comparison (A vs B) differs only in
     the signal. sHash's slot was previously an *oracle* test-swept threshold (5),
     inadmissible for a baseline. ORB's §5 point t=16 was tuned on the geometric SUBSET
     for signal quality and sits at ~14% FP on the full negatives -- looser than the
     other signals, which would silently inflate the swap; so the detector re-points ORB
     to its own iso-FP budget point here. (t=16 stays the signal-quality number in
     python/geometric/; it is not the panel-vote number.)
     NB: tuning sHash by full-set F1 instead degenerates to flag-everything at our
     82.6%-positive base rate -- it just reproduces the trivial classifier (dist<=62,
     100% FP), which is not a usable vote. The FP budget avoids that.

  2. **The quorum k** for each panel, k in {1,2,3,4}, by full-set train F1. The
     paper fixed k=2 for its panel {aHash,pHash,hsvHash,sHash}. We then fired sHash
     and hired ORB (5) and kept k=2 without re-testing it. If the best k MOVES when
     the panel changes, that is a finding (PLAN 2). We select on train, report the
     sweep on test in evaluate_static.py.

Guardrail (PROGRESS 8.5): a threshold that lands on its grid boundary is a red
flag, not a result -- we abort rather than trust it.

Usage:
    training/.venv/bin/python python/detector/tune_static.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from detector_common import (
    ORB_PANEL,
    SHASH_PANEL,
    Metrics,
    evaluate,
    fires,
    flag_everything_f1,
    load_config,
    load_pairs,
    verdict_static,
    REPO_ROOT,
)

SHASH_GRID = list(range(0, 65))  # sHash distance grid; mean-of-mins is unbounded, 64 covers it
ORB_GRID = list(range(0, 60))    # ORB inlier-count grid; fires on inliers>t
FP_BUDGET = 0.10  # the §8.3 operating point shared by aHash/pHash/hsvHash


def derive_shash_threshold(pairs) -> tuple[int, float, dict]:
    """Largest sHash distance whose FP on the pristine train negatives stays within
    the §8.3 <=10% budget -- the same operating point the other four signals sit at,
    so baseline A's four votes are comparable. Detection and FP both rise monotonically
    with the distance cutoff, so the largest t within budget maximises detection there.
    Returns (threshold, standalone_f1, diagnostics)."""
    have = [p for p in pairs if p.scores["shash"] is not None]
    pos = [p for p in have if p.is_copy]
    neg = [p for p in have if not p.is_copy]
    if not pos or not neg:
        raise SystemExit("sHash scores absent on train -- run shash_baseline.py --split train first")
    chosen = 0
    for t in SHASH_GRID:
        fp = sum(fires("shash", p.scores["shash"], t) for p in neg) / len(neg)
        if fp <= FP_BUDGET:
            chosen = t
        else:
            break
    tp = sum(fires("shash", p.scores["shash"], chosen) for p in pos)
    fp = sum(fires("shash", p.scores["shash"], chosen) for p in neg)
    m = Metrics(tp, fp, len(pos) - tp, len(neg) - fp)
    return chosen, m.f1, {"precision": m.precision, "recall": m.recall,
                          "fp_on_pristine": fp / len(neg), "n_pos": len(pos), "n_neg": len(neg)}


def derive_orb_threshold(pairs) -> tuple[int, float, dict]:
    """Most-permissive ORB inlier cutoff whose FP on the pristine train negatives stays
    within the same <=10% budget. ORB fires on inliers>t (high=duplicate), so *smaller* t
    is more permissive and FP falls monotonically as t rises -- we take the smallest t
    within budget, the ORB analogue of the largest distance for a hash.

    This is DELIBERATELY DISTINCT from §5's t=16. That point was tuned on the geometric
    SUBSET for signal quality and sits at ~14% FP on the full pristine negatives; using it
    in the panel would give ORB a looser operating point than the other three signals and
    silently inflate the swap comparison (A vs B). For a fair panel every signal must vote
    at one FP, so the detector uses this iso-FP point instead. t=16 stays the signal-quality
    number in python/geometric/; it is not the panel-vote number."""
    have = [p for p in pairs if p.scores["orb"] is not None]
    pos = [p for p in have if p.is_copy]
    neg = [p for p in have if not p.is_copy]
    chosen = ORB_GRID[-1]
    for t in ORB_GRID:
        fp = sum(fires("orb", p.scores["orb"], t) for p in neg) / len(neg)
        if fp <= FP_BUDGET:
            chosen = t
            break
    tp = sum(fires("orb", p.scores["orb"], chosen) for p in pos)
    fp = sum(fires("orb", p.scores["orb"], chosen) for p in neg)
    m = Metrics(tp, fp, len(pos) - tp, len(neg) - fp)
    return chosen, m.f1, {"precision": m.precision, "recall": m.recall,
                          "fp_on_pristine": fp / len(neg), "n_pos": len(pos), "n_neg": len(neg)}


def best_k(pairs, panel, thresholds) -> tuple[int, dict[int, float]]:
    """Best quorum k on train by full-set F1, plus the full sweep."""
    sweep = {}
    for k in (1, 2, 3, 4):
        m = evaluate(pairs, lambda p, k=k: verdict_static(p, panel, thresholds, k))
        sweep[k] = m.f1
    return max(sweep, key=sweep.get), sweep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    pairs = load_pairs("train", args.data_dir)

    shash_t, shash_f1, diag = derive_shash_threshold(pairs)
    if shash_t in (SHASH_GRID[0], SHASH_GRID[-1]):
        raise SystemExit(f"sHash threshold {shash_t} hit grid boundary -- either no cutoff "
                         f"meets the {FP_BUDGET:.0%} FP budget or all do; do not trust it (PROGRESS 8.5)")
    print(f"\nsHash train threshold: dist<={shash_t}  (<=10% FP-budget point; "
          f"FP {diag['fp_on_pristine']:.1%}, standalone F1 {shash_f1:.1%}, "
          f"P {diag['precision']:.1%}, R {diag['recall']:.1%})")

    orb_t, orb_f1, orb_diag = derive_orb_threshold(pairs)
    if orb_t in (ORB_GRID[0], ORB_GRID[-1]):
        raise SystemExit(f"ORB threshold {orb_t} hit grid boundary -- do not trust it (PROGRESS 8.5)")
    print(f"ORB   train threshold: inliers>{orb_t}  (<=10% FP-budget point; "
          f"FP {orb_diag['fp_on_pristine']:.1%}, standalone F1 {orb_f1:.1%}, "
          f"P {orb_diag['precision']:.1%}, R {orb_diag['recall']:.1%})   "
          f"[§5's t=16 was the geometric-subset signal point, ~14% FP -- not iso-FP]")

    thresholds = load_config(args.data_dir)
    thresholds["shash"] = float(shash_t)
    thresholds["orb"] = float(orb_t)

    print("\nBest quorum k on train (full-set F1):")
    result = {}
    for name, panel in (("shash_panel", SHASH_PANEL), ("orb_panel", ORB_PANEL)):
        k, sweep = best_k(pairs, panel, thresholds)
        result[name] = {"panel": list(panel), "best_k": k,
                        "sweep": {str(kk): round(v, 4) for kk, v in sweep.items()}}
        marks = "  ".join(f"{'*' if kk == k else ' '}k={kk}:{v:.1%}" for kk, v in sweep.items())
        print(f"  {name:<12} {marks}")

    n_pos = sum(p.is_copy for p in pairs)
    floor = flag_everything_f1(n_pos, len(pairs) - n_pos)
    print(f"\n(train flag-everything floor = {floor:.1%})")

    config = {
        "shash_threshold": shash_t,
        "shash_train_f1": round(shash_f1, 4),
        "orb_threshold": orb_t,
        "orb_train_f1": round(orb_f1, 4),
        "panels": result,
        "note": "hashes from hash_thresholds.json (8.3); sHash and ORB derived here at the "
                "same <=10% FP budget so the panel votes iso-FP (§5's ORB t=16 is the "
                "geometric-subset signal point, not the panel-vote point).",
    }
    out = args.data_dir / "train" / "detector_config.json"
    out.write_text(json.dumps(config, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
