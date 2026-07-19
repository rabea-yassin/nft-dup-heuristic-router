"""Derive the static detector's train-side knobs, write detector_config.json.

Two things are tuned on TRAIN here, nothing on test (the protocol whose violation
invalidated the imported baseline, PROGRESS 4 / 8.3):

  1. **sHash's threshold** -- the one operating point Phase D still has to derive.
     The three hashes come from hash_thresholds.json (8.3) and ORB from its
     established t=16 (5); sHash's slot was previously given an *oracle* test-swept
     threshold (5), which is not admissible for a baseline we must beat. We place it
     at the SAME operating point the other four signals sit at: the largest distance
     whose false-positive rate on the pristine train negatives stays within the 8.3
     <=10% budget (ORB's t=16 sits at ~13% FP there). So the swap comparison (A vs B)
     differs only in the signal, not in how its threshold was chosen.
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
FP_BUDGET = 0.10  # the §8.3 operating point shared by aHash/pHash/hsvHash (and ~ORB's t=16)


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

    thresholds = load_config(args.data_dir)
    thresholds["shash"] = float(shash_t)

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
        "panels": result,
        "note": "hashes from hash_thresholds.json (8.3); ORB t=16 (5); sHash derived here.",
    }
    out = args.data_dir / "train" / "detector_config.json"
    out.write_text(json.dumps(config, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
