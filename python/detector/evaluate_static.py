"""Evaluate the STATIC detector on test -- the two baselines Phase D must beat.

Applies the train-derived config (tune_static.py) to the test split and produces:

  * The **k-sweep** for both panels, k in {1,2,3,4}, full-set F1 on test with the
    train-selected k marked. If best-k moves between the sHash panel and the ORB
    panel, that is a finding on its own (PLAN 2) -- it retroactively justifies the
    swap a second way.
  * **Comparison row A** -- static 4-hash WITH sHash, the paper's baseline.
  * **Comparison row B** -- static WITH ORB replacing sHash, isolating the swap's gain.
  Each at its own train-selected k, per category and per collection, every F1 beside
  its computed flag-everything floor.

Row C (the router's gain) lives in evaluate_routed.py; keeping the static baselines
in their own script mirrors python/geometric/ (tune.py / evaluate.py split).

Usage:
    training/.venv/bin/python python/detector/evaluate_static.py --split test
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from detector_common import (
    ORB_PANEL,
    SHASH_PANEL,
    evaluate,
    flag_everything_f1,
    load_config,
    load_pairs,
    per_category_detection,
    verdict_static,
    collection_of,
    REPO_ROOT,
)


def k_sweep_table(pairs, panel, thresholds, selected_k) -> None:
    n_pos = sum(p.is_copy for p in pairs)
    floor = flag_everything_f1(n_pos, len(pairs) - n_pos)
    print(f"  {'k':>3}{'precision':>12}{'recall':>9}{'F1':>8}{'flag-all':>10}")
    for k in (1, 2, 3, 4):
        m = evaluate(pairs, lambda p, k=k: verdict_static(p, panel, thresholds, k))
        mark = " <- train-selected" if k == selected_k else ""
        print(f"  {k:>3}{m.precision:>11.1%}{m.recall:>9.1%}{m.f1:>8.1%}{floor:>10.1%}{mark}")


def per_collection(pairs, panel, thresholds, k) -> None:
    print(f"  {'collection':<12}{'n':>7}{'precision':>12}{'recall':>9}{'F1':>8}")
    for coll in sorted({p.collection for p in pairs}):
        sub = [p for p in pairs if p.collection == coll]
        m = evaluate(sub, lambda p: verdict_static(p, panel, thresholds, k))
        print(f"  {coll:<12}{len(sub):>7}{m.precision:>11.1%}{m.recall:>9.1%}{m.f1:>8.1%}")


def report_baseline(name, pairs, panel, thresholds, k) -> None:
    m = evaluate(pairs, lambda p: verdict_static(p, panel, thresholds, k))
    n_pos = sum(p.is_copy for p in pairs)
    floor = flag_everything_f1(n_pos, len(pairs) - n_pos)
    print(f"\n=== {name}  (panel: {'+'.join(panel)}, k={k}) ===")
    print(f"  overall: precision {m.precision:.1%}  recall {m.recall:.1%}  F1 {m.f1:.1%}"
          f"   (flag-everything floor {floor:.1%})")
    print(f"\n  per category:")
    per_category_detection(pairs, lambda p: verdict_static(p, panel, thresholds, k))
    print(f"\n  per collection:")
    per_collection(pairs, panel, thresholds, k)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    cfg_path = args.data_dir / "train" / "detector_config.json"
    if not cfg_path.exists():
        raise SystemExit("run tune_static.py first -- k and the sHash threshold come from train")
    cfg = json.loads(cfg_path.read_text())
    thresholds = load_config(args.data_dir)

    pairs = load_pairs(args.split, args.data_dir)
    have_shash = any(p.scores["shash"] is not None for p in pairs)

    k_shash = cfg["panels"]["shash_panel"]["best_k"]
    k_orb = cfg["panels"]["orb_panel"]["best_k"]

    # C1: does the best k MOVE when we fire sHash and hire ORB? Train-selected k is
    # marked; the sweep is reported on test.
    print("\n--- C1 k-sweep, sHash panel (paper's panel) ---")
    if have_shash:
        k_sweep_table(pairs, SHASH_PANEL, thresholds, k_shash)
    else:
        print("  (sHash scores absent on this split -- skipped)")
    print("\n--- C1 k-sweep, ORB panel (the swap) ---")
    k_sweep_table(pairs, ORB_PANEL, thresholds, k_orb)
    if k_shash != k_orb:
        print(f"\n** best-k MOVED: sHash panel k={k_shash}, ORB panel k={k_orb} (PLAN 2) **")
    else:
        print(f"\n(best-k unchanged at k={k_orb} across both panels; note the natural base rate")
        print(f" 82.6% rewards recall, so this is a base-rate effect as much as a panel one --")
        print(f" evaluate_routed.py's prevalence sweep separates them)")

    # The three-way BASELINES are the paper's rule: >=2 of 4. Fixed k=2 keeps A vs B
    # a pure swap comparison and matches how evaluate_routed.py compares row C.
    if have_shash:
        report_baseline("A: static + sHash (paper's baseline, k=2)", pairs, SHASH_PANEL, thresholds, 2)
    else:
        print("\n=== A: static + sHash -- SKIPPED (sHash cache absent; run shash_baseline --split train/test) ===")
    report_baseline("B: static + ORB (isolates the swap, k=2)", pairs, ORB_PANEL, thresholds, 2)


if __name__ == "__main__":
    main()
