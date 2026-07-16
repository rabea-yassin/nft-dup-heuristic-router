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
dominant cost, so each image is hashed once.

Output: data/<split>/shash_scores.csv with columns
    original_image, copy_image, manipulation_type, is_copy, shash_dist

Usage:
    training/.venv/bin/python python/geometric/shash_baseline.py --split test
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import imagehash
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["original_image", "copy_image", "manipulation_type", "is_copy", "shash_dist"]


class ShashCache:
    """crop_resistant_hash per image, computed once."""

    def __init__(self, images_dir: Path):
        self.images_dir = Path(images_dir)
        self._cache: dict[str, list] = {}

    def segments(self, filename: str) -> list:
        if filename not in self._cache:
            with Image.open(self.images_dir / filename) as img:
                self._cache[filename] = imagehash.crop_resistant_hash(img).segment_hashes
        return self._cache[filename]


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
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    with open(split_dir / "metadata.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    cache = ShashCache(split_dir / "images")
    out_path = split_dir / "shash_scores.csv"

    started = time.time()
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, start=1):
            original = row["original_image"].strip()
            copy = row["copy_image"].strip()
            dist = paper_distance(cache.segments(original), cache.segments(copy))
            writer.writerow(
                {
                    "original_image": original,
                    "copy_image": copy,
                    "manipulation_type": row["manipulation_type"].strip(),
                    "is_copy": row["is_copy"].strip(),
                    "shash_dist": f"{dist:.4f}",
                }
            )
            if i % 500 == 0 or i == len(rows):
                elapsed = time.time() - started
                remaining = (elapsed / i) * (len(rows) - i)
                print(
                    f"  [{args.split}] {i}/{len(rows)}  "
                    f"{elapsed/i*1000:.0f} ms/pair  {len(cache._cache)} images hashed  "
                    f"eta {remaining/60:.1f} min",
                    flush=True,
                )

    print(f"{len(rows)} pairs scored in {(time.time()-started)/60:.1f} min -> {out_path}")


if __name__ == "__main__":
    main()
