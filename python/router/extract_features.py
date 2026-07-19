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
import os
import sys
import time
from multiprocessing import Pool
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


def _features_one(args: tuple[str, str, str]) -> tuple[str, str, str, dict[str, float]]:
    """Worker: (images_dir, image, label) -> (image, label, collection, feats). Top-level
    so multiprocessing can pickle it; features_from_path is a pure function of the file.
    Pin OpenCV to one thread per worker so N processes don't oversubscribe the cores."""
    import cv2

    cv2.setNumThreads(1)
    images_dir, image, label = args
    feats = features_from_path(Path(images_dir) / image)
    return image, label, collection_of(image), feats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="train",
                        choices=["train", "test", "multi_train", "multi_test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="only the first N images")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel feature-extraction workers (default 1 = serial; 0 = all cores)")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    images_dir = split_dir / "images"
    items = unique_labeled_copies(split_dir / "metadata.csv")
    if args.limit:
        items = items[: args.limit]
    workers = args.workers if args.workers > 0 else (os.cpu_count() or 1)

    out_path = split_dir / "router_features.csv"
    started = time.time()
    tasks = [(str(images_dir), image, label) for image, label in items]

    # Rows are keyed by image downstream (router_signal maps image -> proba), so the
    # write order does not matter and a process pool can stream results as they finish.
    # The header comes from the first result's feature keys (deterministic across images).
    def emit(writer_state, result):
        image, label, collection, feats = result
        if writer_state["writer"] is None:
            header = ["image", "manipulation_type", "collection", *feats.keys()]
            writer_state["writer"] = csv.DictWriter(writer_state["f"], fieldnames=header)
            writer_state["writer"].writeheader()
        writer_state["writer"].writerow(
            {"image": image, "manipulation_type": label, "collection": collection, **feats}
        )

    with open(out_path, "w", newline="") as f:
        state = {"f": f, "writer": None}
        if workers <= 1:
            for i, task in enumerate(tasks, start=1):
                emit(state, _features_one(task))
                if i % 2000 == 0 or i == len(tasks):
                    rate = (time.time() - started) / i
                    print(f"  [{args.split}] {i}/{len(tasks)}  {rate*1000:.1f} ms/img  "
                          f"eta {rate*(len(tasks)-i)/60:.1f} min", flush=True)
        else:
            with Pool(workers) as pool:
                for i, result in enumerate(pool.imap_unordered(_features_one, tasks, chunksize=16),
                                           start=1):
                    emit(state, result)
                    if i % 2000 == 0 or i == len(tasks):
                        rate = (time.time() - started) / i
                        print(f"  [{args.split}] {i}/{len(tasks)}  {rate*1000:.1f} ms/img  "
                              f"eta {rate*(len(tasks)-i)/60:.1f} min", flush=True)

    print(f"{len(items)} images -> {out_path}  ({(time.time()-started)/60:.1f} min, workers={workers})")


if __name__ == "__main__":
    main()
