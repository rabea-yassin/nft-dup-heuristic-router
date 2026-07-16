"""How many ORB descriptors do we actually need? -- the accuracy-vs-bytes curve.

WHY THIS EXISTS
---------------
The paper's premise is that detection is "fully self-contained within the
blockchain" (Sec. I): the hashes ride along in the NFT transaction, which it
states is 300-500 bytes, and "each hash value needs several bytes", so the
overhead is "low ... practical and easy to implement" (Sec. VI).

That claim survives their four hashes and dies on ours. Measured over 60 real
originals:

    aHash + pHash + hsvHash   24 bytes   (fixed)
    sHash                     32 bytes   (median; 1-10 segments, 8 bytes each)
    ORB @ nfeatures=500   14,560 bytes   (median 455 descriptors x 32 bytes)

ORB is ~36x an entire transaction. On-chain that is ~455 storage slots, roughly
9M gas for one mint -- about a third of an Ethereum block, against ~50-70k gas
for an ordinary ERC-721 mint. So adopting ORB voids the paper's self-contained
premise, and the report has to say so rather than quietly not mention it.

But 500 was never a considered number. It is `cv2.ORB_create`'s default. The
original pipeline used 500 in its notebooks and 1000 in its scripts -- the same
parameter, two values, inside one repo -- and our own unification to 500 was
equally unargued. Worse, **the cap never binds**: ORB finds ~455 keypoints at
256px, so `nfeatures=500` is a ceiling floating above the real count, doing
nothing.

There is obvious headroom to spend. The verdict threshold is `inliers > 16`,
while median inliers are 200 (crop), 160 (flip), 456 (exact copy) -- roughly
10x slack on ORB's actual job. This script asks the question that turns "we
ignored the premise" into a measurement:

    how few descriptors can ORB keep and still do its job?

PROTOCOL
--------
Each budget gets its own best threshold, swept on the evaluation sample itself.
That is an oracle, and deliberately so: the question is "what is *achievable* at
this budget", so every point gets the same optimistic treatment and the curve's
SHAPE is what's comparable. The optimism is quantified rather than assumed --
the N=500 row is directly comparable to the honest train-tuned number from
tune.py/evaluate.py (t=16 -> F1 90.6% on the full split), so the gap between
that and this script's N=500 row IS the oracle's worth. If it is small, the
curve is trustworthy.

Scored on the geometric subset only (flip/rotate + crop/reposition, plus the
non-duplicates ORB must reject), because that is the job ORB is kept for.

Usage:
    # full curve
    training/.venv/bin/python python/geometric/descriptor_budget.py --split test

    # live demo: just the headline comparison, on a small fast sample
    training/.venv/bin/python python/geometric/descriptor_budget.py --split test \
        --budgets 500,128 --positives 300 --negatives 200

`--budgets` makes the descriptor budget a runnable knob rather than a static
table: it is how we show, at presentation time, that we know the on-chain
storage cost exists, that we characterised it, and exactly what accuracy each
budget buys. ΔF1 is measured against the largest budget in the run.
"""

from __future__ import annotations

import argparse
import csv
import random
import time
from pathlib import Path

import cv2
import numpy as np

from categories import GEOMETRIC_POSITIVES, NEGATIVES
from orb_match import OrbMatcher, normalize_for_orb

REPO_ROOT = Path(__file__).resolve().parents[2]

# Descending, so the expensive baseline runs first and a partial sweep is still
# readable. 500 = today's default; 16 is below the verdict threshold and should
# fail outright -- a curve needs its floor.
BUDGETS = (500, 256, 128, 96, 64, 48, 32, 24, 16)

BYTES_PER_DESCRIPTOR = 32  # 256-bit ORB descriptor
PAPER_TX_BYTES = 400  # midpoint of the paper's stated 300-500


def sample_pairs(metadata_csv: Path, n_positive: int, n_negative: int, seed: int):
    """Geometric positives + the negatives they must be told apart from."""
    positives, negatives = [], []
    with open(metadata_csv, newline="") as f:
        for row in csv.DictReader(f):
            pair = (row["original_image"].strip(), row["copy_image"].strip())
            category = row["manipulation_type"].strip()
            if category in GEOMETRIC_POSITIVES:
                positives.append(pair)
            elif category in NEGATIVES:
                negatives.append(pair)

    rng = random.Random(seed)
    positives = rng.sample(positives, min(n_positive, len(positives)))
    negatives = rng.sample(negatives, min(n_negative, len(negatives)))
    return [(p, True) for p in positives] + [(n, False) for n in negatives]


def best_f1(scored):
    """Sweep every sensible threshold; return (threshold, precision, recall, f1)."""
    best = (0, 0.0, 0.0, -1.0)
    highest = max((s for s, _ in scored), default=0)
    for t in range(0, int(highest) + 1):
        tp = sum(1 for s, is_copy in scored if s > t and is_copy)
        fp = sum(1 for s, is_copy in scored if s > t and not is_copy)
        fn = sum(1 for s, is_copy in scored if s <= t and is_copy)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best[3]:
            best = (t, precision, recall, f1)
    return best


def median_descriptors(images_dir: Path, filenames, budget: int) -> float:
    """What ORB *actually* returns at this budget -- the cap may not bind."""
    orb = cv2.ORB_create(nfeatures=budget)
    counts = []
    for name in filenames:
        img = cv2.imread(str(images_dir / name), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        _, desc = orb.detectAndCompute(normalize_for_orb(img), None)
        counts.append(0 if desc is None else len(desc))
    return float(np.median(counts)) if counts else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--positives", type=int, default=1200)
    parser.add_argument("--negatives", type=int, default=800)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--budgets",
        type=lambda s: sorted({int(x) for x in s.split(",")}, reverse=True),
        default=list(BUDGETS),
        help="comma-separated nfeatures values to run (default: the full curve)",
    )
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    images_dir = split_dir / "images"
    pairs = sample_pairs(split_dir / "metadata.csv", args.positives, args.negatives, args.seed)
    n_pos = sum(1 for _, is_copy in pairs if is_copy)

    # A fixed image subset for the size measurement, so byte counts across
    # budgets are measured on identical images.
    size_sample = sorted({original for (original, _), _ in pairs})[:60]

    print(f"split={args.split}   {n_pos} geometric positives + "
          f"{len(pairs)-n_pos} negatives = {len(pairs)} pairs per budget")
    print(f"paper's transaction budget: {PAPER_TX_BYTES} bytes "
          f"(4 hashes fit in ~56)\n")
    print(f"  {'nfeat':>6}{'actual':>8}{'bytes':>9}{'x tx':>7}"
          f"{'thresh':>8}{'P':>8}{'R':>8}{'F1':>8}{'dF1':>7}")

    baseline_f1 = None
    rows = []
    for budget in args.budgets:
        started = time.time()
        matcher = OrbMatcher(images_dir, orb_features=budget)
        scored = [(matcher.score(original, copy), is_copy)
                  for (original, copy), is_copy in pairs]

        actual = median_descriptors(images_dir, size_sample, budget)
        nbytes = actual * BYTES_PER_DESCRIPTOR
        t, precision, recall, f1 = best_f1(scored)
        if baseline_f1 is None:
            baseline_f1 = f1
        delta = (f1 - baseline_f1) * 100

        rows.append({
            "nfeatures": budget, "median_descriptors": actual, "bytes": int(nbytes),
            "threshold": t, "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4),
        })
        print(f"  {budget:>6}{actual:>8.0f}{int(nbytes):>9}{nbytes/PAPER_TX_BYTES:>6.1f}x"
              f"{t:>8}{precision:>7.1%}{recall:>7.1%}{f1:>7.1%}{delta:>+6.1f}"
              f"   ({time.time()-started:.0f}s)", flush=True)

    # Only the full canonical curve owns descriptor_budget.csv; a partial
    # --budgets run (e.g. a live demo) must not overwrite the numbers the report
    # cites.
    if args.budgets == list(BUDGETS):
        out = split_dir / "descriptor_budget.csv"
        with open(out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n-> {out}")
    else:
        print("\n(partial --budgets run: canonical descriptor_budget.csv left untouched)")


if __name__ == "__main__":
    main()
