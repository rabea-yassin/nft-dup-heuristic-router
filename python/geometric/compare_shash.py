"""ORB vs sHash on the manipulations sHash was built for.

This is the measurement that justifies replacing sHash with ORB. sHash exists to
survive cropping; if ORB does not beat it on crop/rotate/scale/reposition, the
swap is not defensible and we should say so.

The comparison is deliberately unfair TO US:

  * ORB uses the threshold tuned on the TRAIN split (see tune.py), the honest
    protocol -- it never sees test labels.
  * sHash is given its BEST-CASE threshold, swept over the TEST split itself.
    That is an oracle no real deployment gets. It is an upper bound on sHash.

So the reported sHash numbers are the best sHash could possibly do, and the ORB
numbers are what ORB actually does. If ORB wins anyway, it wins for real.

Polarity differs and is easy to invert: sHash is a DISTANCE (duplicate when
`dist <= t`), ORB is an INLIER COUNT (duplicate when `inliers > t`).

Usage:
    training/.venv/bin/python python/geometric/compare_shash.py --split test
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from categories import GEOMETRIC_POSITIVES, NEGATIVES, collection_of

REPO_ROOT = Path(__file__).resolve().parents[2]
ORB_TRAIN_TUNED_THRESHOLD = 16


def load_scores(path: Path, value_field: str, cast) -> dict:
    """Map (original, copy) -> {row fields, "score": value}."""
    scores = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["original_image"].strip(), row["copy_image"].strip())
            scores[key] = {
                "manipulation_type": row["manipulation_type"].strip(),
                "is_copy": row["is_copy"].strip() == "1",
                "score": cast(row[value_field]),
            }
    return scores


def confusion(pairs, flagged_fn):
    """pairs: iterable of (is_copy, score). flagged_fn: score -> bool."""
    cm = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for is_copy, score in pairs:
        flagged = flagged_fn(score)
        if is_copy:
            cm["tp" if flagged else "fn"] += 1
        else:
            cm["fp" if flagged else "tn"] += 1
    return cm


def prf(cm):
    tp, fn, fp = cm["tp"], cm["fn"], cm["fp"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def geometric_pairs(scores):
    """The subset ORB claims: geometric positives + the negatives it must reject."""
    out = []
    for entry in scores.values():
        category = entry["manipulation_type"]
        if category in GEOMETRIC_POSITIVES:
            out.append((True, entry["score"]))
        elif category in NEGATIVES:
            out.append((False, entry["score"]))
    return out


def sweep_best_f1(pairs, candidates, flagged_fn_factory):
    """Best-case threshold: the one maximising F1 on this very data (oracle)."""
    best = None
    for t in candidates:
        cm = confusion(pairs, flagged_fn_factory(t))
        _, _, f1 = prf(cm)
        if best is None or f1 > best[1]:
            best = (t, f1)
    return best[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    orb = load_scores(split_dir / "orb_scores.csv", "orb_inliers", int)
    shash = load_scores(split_dir / "shash_scores.csv", "shash_dist", float)

    shared = set(orb) & set(shash)
    if len(shared) != len(orb) or len(shared) != len(shash):
        print(f"note: comparing {len(shared)} pairs common to both "
              f"(orb={len(orb)}, shash={len(shash)})")

    orb_geo = geometric_pairs({k: orb[k] for k in shared})
    shash_geo = geometric_pairs({k: shash[k] for k in shared})

    # sHash gets the oracle threshold; ORB gets the honest train-tuned one.
    shash_t = sweep_best_f1(
        shash_geo,
        [x * 0.5 for x in range(0, 81)],
        lambda t: (lambda d: d <= t),
    )
    orb_t = ORB_TRAIN_TUNED_THRESHOLD

    orb_cm = confusion(orb_geo, lambda s: s > orb_t)
    shash_cm = confusion(shash_geo, lambda d: d <= shash_t)

    print(f"\nsplit={args.split}   geometric subset: "
          f"{sum(1 for c, _ in orb_geo if c)} positives, "
          f"{sum(1 for c, _ in orb_geo if not c)} negatives\n")

    print("ORB vs sHash on crop/rotate/scale/reposition -- the job sHash was built for")
    print(f"  {'signal':<34}{'thresh':>8}{'P':>9}{'R':>9}{'F1':>9}")
    for name, cm, t in (
        (f"sHash (BEST-CASE, tuned on test)", shash_cm, f"<={shash_t:g}"),
        (f"ORB (honest, tuned on train)", orb_cm, f">{orb_t}"),
    ):
        p, r, f1 = prf(cm)
        print(f"  {name:<34}{t:>8}{p:>8.1%}{r:>8.1%}{f1:>8.1%}")

    _, _, orb_f1 = prf(orb_cm)
    _, _, shash_f1 = prf(shash_cm)
    delta = (orb_f1 - shash_f1) * 100
    print(f"\n  ORB - sHash = {delta:+.1f} F1  (and sHash had the oracle advantage)")

    print("\nPer geometric category (detection rate at the thresholds above):")
    print(f"  {'category':<28}{'n':>6}{'sHash':>10}{'ORB':>10}")
    for category in sorted(GEOMETRIC_POSITIVES):
        rows = [k for k in shared if orb[k]["manipulation_type"] == category]
        if not rows:
            continue
        s_hit = sum(1 for k in rows if shash[k]["score"] <= shash_t) / len(rows)
        o_hit = sum(1 for k in rows if orb[k]["score"] > orb_t) / len(rows)
        print(f"  {category:<28}{len(rows):>6}{s_hit:>9.1%}{o_hit:>9.1%}")

    print("\nPer collection (geometric positives only):")
    print(f"  {'collection':<28}{'n':>6}{'sHash':>10}{'ORB':>10}")
    collections = sorted({collection_of(k[1]) for k in shared})
    for collection in collections:
        rows = [
            k for k in shared
            if orb[k]["manipulation_type"] in GEOMETRIC_POSITIVES
            and collection_of(k[1]) == collection
        ]
        if not rows:
            continue
        s_hit = sum(1 for k in rows if shash[k]["score"] <= shash_t) / len(rows)
        o_hit = sum(1 for k in rows if orb[k]["score"] > orb_t) / len(rows)
        print(f"  {collection:<28}{len(rows):>6}{s_hit:>9.1%}{o_hit:>9.1%}")


if __name__ == "__main__":
    main()
