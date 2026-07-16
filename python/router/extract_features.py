"""Compute the router's per-image feature vector for every image of a split, once.

Feature extraction is the expensive part; training and evaluation are then pure
arithmetic over the cached vectors. Same split of labour as the geometric
pipeline's score_dataset.py: pay for the vision pass once, re-train and
re-evaluate for free.

The router classifies ONE image, so the unit here is the unique `copy_image`, not
the metadata pair -- an image that appears as several rows' copy (a real NFT used
as the negative partner of several originals) is one training example. The dedup
is label-clean: no copy_image ever carries two manipulation labels (verified:
47,963 unique train images, 0 conflicts).

Output: data/<split>/router_features.csv with columns
    image, manipulation_type, collection, <93 feature columns...>

`data/` is gitignored, so this is a regenerable local artifact, like orb_scores.csv.

Usage:
    training/.venv/bin/python python/router/extract_features.py --split train
    training/.venv/bin/python python/router/extract_features.py --split test
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geometric"))
from categories import collection_of  # noqa: E402
from features import features_from_path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def unique_labeled_copies(metadata_path: Path) -> list[tuple[str, str]]:
    """(copy_image, manipulation_type) for each unique copy image, order-stable."""
    seen: dict[str, str] = {}
    with open(metadata_path, newline="") as f:
        for row in csv.DictReader(f):
            copy = row["copy_image"].strip()
            label = row["manipulation_type"].strip()
            if copy in seen and seen[copy] != label:
                raise ValueError(f"{copy} has conflicting labels: {seen[copy]} vs {label}")
            seen.setdefault(copy, label)
    return list(seen.items())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="train", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="only the first N images")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    images_dir = split_dir / "images"
    items = unique_labeled_copies(split_dir / "metadata.csv")
    if args.limit:
        items = items[: args.limit]

    out_path = split_dir / "router_features.csv"
    started = time.time()
    header: list[str] | None = None
    with open(out_path, "w", newline="") as f:
        writer: csv.DictWriter | None = None
        for i, (image, label) in enumerate(items, start=1):
            feats = features_from_path(images_dir / image)
            if header is None:
                header = ["image", "manipulation_type", "collection", *feats.keys()]
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
            writer.writerow(
                {"image": image, "manipulation_type": label, "collection": collection_of(image), **feats}
            )
            if i % 2000 == 0 or i == len(items):
                elapsed = time.time() - started
                rate = elapsed / i
                print(
                    f"  [{args.split}] {i}/{len(items)}  "
                    f"{rate*1000:.1f} ms/img  eta {rate*(len(items)-i)/60:.1f} min",
                    flush=True,
                )

    print(f"{len(items)} images -> {out_path}  ({(time.time()-started)/60:.1f} min)")


if __name__ == "__main__":
    main()
