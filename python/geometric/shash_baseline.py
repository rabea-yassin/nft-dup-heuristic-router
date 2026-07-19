"""Score every pair with sHash, so the ORB swap is measured rather than asserted.

sHash is the hash ORB replaces, and crop-resistance was its entire reason for
existing. Replacing it is only defensible if we show ORB does that job better on
the manipulations sHash was built for. This produces the sHash half of that
comparison, over exactly the pairs and images score_dataset.py used.

Two details that matter and are easy to get wrong:

  * **Direction.** The paper's distance is mean-of-mins, which is asymmetric.
    original->copy reproduces the authors' reported sHash_dist on 1802/1802 rows
    of their CSV; copy->original matches only 240/1802. So the original's
    segments are the ones forced to find a match. See PROGRESS.md section 2.
  * **Polarity.** This is a DISTANCE (low = duplicate), the opposite of ORB's
    inlier count (high = duplicate). Downstream code must not assume `> t`.

Hashes are cached per image: there are 12,000 unique images behind the 13,800
test pairs, and crop_resistant_hash costs ~137 ms each -- far and away the
dominant cost, so each image is hashed once. That per-image hashing is the only
expensive part and is embarrassingly parallel, so `--workers` spreads it over a
process pool; the per-pair distance is cheap arithmetic done single-threaded.

Output: data/<split>/shash_scores.csv with columns
    original_image, copy_image, manipulation_type, is_copy, shash_dist

Usage:
    training/.venv/bin/python python/geometric/shash_baseline.py --split test
    training/.venv/bin/python python/geometric/shash_baseline.py --split train --workers 7
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from multiprocessing import Pool
from pathlib import Path

import imagehash
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["original_image", "copy_image", "manipulation_type", "is_copy", "shash_dist"]


def _hash_one(args: tuple[str, str]) -> tuple[str, list]:
    """Worker: (images_dir, filename) -> (filename, segment_hashes).

    Top-level (not a closure/method) so multiprocessing can pickle it. Pure
    function of the image file, so it is safe to run across processes.
    """
    images_dir, filename = args
    with Image.open(Path(images_dir) / filename) as img:
        return filename, imagehash.crop_resistant_hash(img).segment_hashes


def hash_images(images_dir: Path, unique_images: list[str], workers: int, split: str) -> dict[str, list]:
    """crop_resistant_hash for each unique image, once. Serial when workers<=1
    (byte-identical to the old path), otherwise over a process pool. The output
    CSV is independent of order, so parallel hashing does not change any result."""
    cache: dict[str, list] = {}
    n = len(unique_images)
    started = time.time()

    def log(done: int) -> None:
        elapsed = time.time() - started
        eta = (elapsed / done) * (n - done) if done else 0.0
        print(f"  [{split}] hashed {done}/{n} images  {elapsed/done*1000:.0f} ms/img  "
              f"eta {eta/60:.1f} min", flush=True)

    if workers <= 1:
        for done, name in enumerate(unique_images, start=1):
            cache[name] = _hash_one((str(images_dir), name))[1]
            if done % 500 == 0 or done == n:
                log(done)
    else:
        tasks = [(str(images_dir), name) for name in unique_images]
        with Pool(workers) as pool:
            for done, (name, segs) in enumerate(
                pool.imap_unordered(_hash_one, tasks, chunksize=16), start=1
            ):
                cache[name] = segs
                if done % 500 == 0 or done == n:
                    log(done)
    return cache


def paper_distance(source_segments: list, target_segments: list) -> float:
    """Mean over the SOURCE's segments of the closest distance in the target.

    Port of shash_paper_distance() in src/hashes/shash/shash.c, which is
    bit-exact against imagehash and reproduces the authors' CSV.
    """
    if not source_segments or not target_segments:
        return float("inf")
    total = sum(min(s - t for t in target_segments) for s in source_segments)
    return total / len(source_segments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N rows")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel image-hashing workers (default 1 = serial; 0 = all cores)")
    args = parser.parse_args()

    workers = args.workers if args.workers > 0 else (os.cpu_count() or 1)

    split_dir = args.data_dir / args.split
    with open(split_dir / "metadata.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    # Unique images behind the pairs, first-seen order (order does not affect output).
    seen: dict[str, None] = {}
    for row in rows:
        seen[row["original_image"].strip()] = None
        seen[row["copy_image"].strip()] = None
    unique_images = list(seen)

    print(f"[{args.split}] {len(rows)} pairs, {len(unique_images)} unique images, "
          f"workers={workers}", flush=True)
    cache = hash_images(split_dir / "images", unique_images, workers, args.split)

    out_path = split_dir / "shash_scores.csv"
    started = time.time()
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            original = row["original_image"].strip()
            copy = row["copy_image"].strip()
            dist = paper_distance(cache[original], cache[copy])
            writer.writerow(
                {
                    "original_image": original,
                    "copy_image": copy,
                    "manipulation_type": row["manipulation_type"].strip(),
                    "is_copy": row["is_copy"].strip(),
                    "shash_dist": f"{dist:.4f}",
                }
            )

    print(f"{len(rows)} pairs scored in {(time.time()-started)/60:.1f} min -> {out_path}")


if __name__ == "__main__":
    main()
