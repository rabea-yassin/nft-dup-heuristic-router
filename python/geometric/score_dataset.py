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
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orb_match import OrbMatcher  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["original_image", "copy_image", "manipulation_type", "is_copy", "orb_inliers"]


def _score_chunk(args: tuple[str, list[tuple[str, str, str, str]]]) -> list[tuple]:
    """Worker: (images_dir, rows) -> scored rows. Each process builds its OWN OrbMatcher
    (cv2 objects are not picklable), and pins OpenCV to one thread so N processes don't
    oversubscribe the cores. Chunks are contiguous in metadata order, and the generator
    writes all of a base image's rows together, so the matcher's per-original descriptor
    cache still hits within a chunk."""
    import cv2

    cv2.setNumThreads(1)
    images_dir, rows = args
    matcher = OrbMatcher(images_dir)
    out = []
    for orig, copy, cat, is_copy in rows:
        out.append((orig, copy, cat, is_copy, matcher.score(orig, copy)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="train",
                        choices=["train", "test", "multi_train", "multi_test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N rows")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel ORB workers (default 1 = serial; 0 = all cores)")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    with open(split_dir / "metadata.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]
    workers = args.workers if args.workers > 0 else (os.cpu_count() or 1)

    triples = [(r["original_image"].strip(), r["copy_image"].strip(),
                r["manipulation_type"].strip(), r["is_copy"].strip()) for r in rows]
    out_path = split_dir / "orb_scores.csv"
    started = time.time()

    if workers <= 1:
        results = _score_chunk((str(split_dir / "images"), triples))
    else:
        # split into `workers` contiguous chunks so each original's block stays intact
        n = len(triples)
        size = (n + workers - 1) // workers
        chunks = [(str(split_dir / "images"), triples[i:i + size]) for i in range(0, n, size)]
        results = []
        done = 0
        with Pool(workers) as pool:
            for chunk_out in pool.imap(_score_chunk, chunks):
                results.extend(chunk_out)
                done += len(chunk_out)
                rate = (time.time() - started) / done
                print(f"  [{args.split}] {done}/{n}  {rate*1000:.0f} ms/pair  "
                      f"eta {rate*(n-done)/60:.1f} min", flush=True)

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        writer.writerows(results)

    print(f"{len(rows)} pairs scored in {(time.time()-started)/60:.1f} min "
          f"(workers={workers}) -> {out_path}")


if __name__ == "__main__":
    main()
