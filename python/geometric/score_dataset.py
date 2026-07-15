"""Compute the pairwise ORB geometric score for every row of a split, once.

Scoring is the expensive part (~60 ms/pair, so ~55 min for the 55k-row train
split); threshold tuning and evaluation are then pure arithmetic over the cached
scores. Keeping them separate means we pay for the vision pass once and can
re-tune, re-evaluate, and feed the router (Phase C/D) for free.

Output: data/<split>/orb_scores.csv with columns
    original_image, copy_image, manipulation_type, is_copy, orb_inliers

`data/` is gitignored, so this is a regenerable local artifact, like the split
itself.

Usage:
    training/.venv/bin/python python/geometric/score_dataset.py --split train
    training/.venv/bin/python python/geometric/score_dataset.py --split test
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orb_match import OrbMatcher  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["original_image", "copy_image", "manipulation_type", "is_copy", "orb_inliers"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="train", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N rows")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    with open(split_dir / "metadata.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    matcher = OrbMatcher(split_dir / "images")
    out_path = split_dir / "orb_scores.csv"

    started = time.time()
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, start=1):
            score = matcher.score(row["original_image"].strip(), row["copy_image"].strip())
            writer.writerow(
                {
                    "original_image": row["original_image"].strip(),
                    "copy_image": row["copy_image"].strip(),
                    "manipulation_type": row["manipulation_type"].strip(),
                    "is_copy": row["is_copy"].strip(),
                    "orb_inliers": score,
                }
            )
            if i % 2000 == 0 or i == len(rows):
                elapsed = time.time() - started
                rate = elapsed / i
                remaining = rate * (len(rows) - i)
                print(
                    f"  [{args.split}] {i}/{len(rows)}  "
                    f"{rate*1000:.0f} ms/pair  eta {remaining/60:.1f} min",
                    flush=True,
                )

    print(f"{len(rows)} pairs scored in {(time.time()-started)/60:.1f} min -> {out_path}")


if __name__ == "__main__":
    main()
