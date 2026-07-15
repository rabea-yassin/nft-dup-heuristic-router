"""Verify the imported ORB baseline before trusting or building on it (Phase B2).

Finding this reproduces: the reported confusion matrix is a pure function of which
images happen to be indexed in the gallery, not of ORB's matching ability.

The gallery (`build/image_map.pkl`) indexes only the azuki + bayc collections
(2,000 images); all 1,000 CryptoPunk (`cp_`) originals are missing. Not because
the index is stale -- rebuilding it changes nothing -- but because ORB cannot
describe them at all: punks are natively 24x24 px, smaller than ORB's default
31 px patch, so detectAndCompute returns zero descriptors for 100% of them and
the indexer's `if descriptors is not None:` check silently drops every one. See
orb_match.normalize_for_orb for the fix. Because the pipeline reads only
`copy_image` + `is_copy` and queries it against the whole gallery, that one
omission fully determines every cell:

    TP = positives whose ORIGINAL is indexed      (findable at all)
    FN = positives whose original is NOT indexed  (unfindable => forced miss)
    FP = negatives whose QUERY is itself indexed  (finds itself => forced hit)
    TN = negatives not indexed                    (nothing to match; punks look
                                                   nothing like azuki/bayc art)

This script needs no OpenCV and decodes no images -- that is the whole point. If
pure set arithmetic reproduces the published numbers, the vision pipeline was
never what was being measured.

Requires the original project tree (gallery pickle + metadata), which lives
outside this repo; pass its location with --project.

Usage:
    python python/geometric/verify_baseline.py --project /home/ra/nft_project
"""

import argparse
import csv
import pickle
from pathlib import Path

# Reported by the original tune_pipeline.py grid search on data/train -- the best
# row of python/geometric/original/threshold_tuning_results.csv (F1 73.58).
REPORTED_TRAIN = {"tp": 30210, "fn": 15390, "fp": 6306, "tn": 3294}


def load_gallery(project: Path) -> set:
    with open(project / "build" / "image_map.pkl", "rb") as f:
        db = pickle.load(f)
    return set(db["image_filenames"].values())


def predict_from_membership(metadata_csv: Path, gallery: set) -> dict:
    """The confusion matrix implied by gallery membership alone."""
    cm = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    with open(metadata_csv, newline="") as f:
        for row in csv.DictReader(f):
            if row["is_copy"].strip() == "1":
                # A copy is findable only if the image it derives from is indexed.
                cm["tp" if row["original_image"].strip() in gallery else "fn"] += 1
            else:
                # A "non-duplicate" query that is itself in the gallery finds itself.
                cm["fp" if row["copy_image"].strip() in gallery else "tn"] += 1
    return cm


def metrics(cm: dict) -> tuple:
    tp, fn, fp, tn = cm["tp"], cm["fn"], cm["fp"], cm["tn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    accuracy = (tp + tn) / sum(cm.values()) if sum(cm.values()) else 0.0
    return precision * 100, recall * 100, accuracy * 100


def collection_of(filename: str) -> str:
    return filename.split("_")[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("/home/ra/nft_project"))
    args = parser.parse_args()

    gallery = load_gallery(args.project)
    raw = {p.name for p in (args.project / "data" / "raw").glob("*.png")}

    counts = {}
    for name in gallery:
        counts[collection_of(name)] = counts.get(collection_of(name), 0) + 1
    missing = raw - gallery

    print(f"gallery indexes {len(gallery)} of {len(raw)} originals in data/raw")
    print(f"  indexed by collection : {counts}")
    print(f"  MISSING from gallery  : {len(missing)} originals, "
          f"collections={sorted({collection_of(n) for n in missing})}\n")

    predicted = predict_from_membership(
        args.project / "data" / "train" / "metadata.csv", gallery
    )

    print(f"{'cell':<6}{'reported':>10}{'set-math':>10}   match")
    for cell in ("tp", "fn", "fp", "tn"):
        ok = "YES" if predicted[cell] == REPORTED_TRAIN[cell] else "no"
        print(f"{cell:<6}{REPORTED_TRAIN[cell]:>10}{predicted[cell]:>10}   {ok}")

    rep = metrics(REPORTED_TRAIN)
    pre = metrics(predicted)
    print(f"\n{'':<12}{'reported':>10}{'set-math':>10}")
    for label, r, p in zip(("precision", "recall", "accuracy"), rep, pre):
        print(f"{label:<12}{r:>9.2f}%{p:>9.2f}%")

    if predicted == REPORTED_TRAIN:
        print("\nAll four cells reproduced from set membership alone, with no image")
        print("decoding. The reported metrics measure which collection an image")
        print("belongs to, not whether it is a duplicate. ORB caught every findable")
        print("positive; its real matching ability was never tested. Numbers retired.")
    else:
        print("\nCells did not reproduce exactly -- re-examine before drawing conclusions.")


if __name__ == "__main__":
    main()
