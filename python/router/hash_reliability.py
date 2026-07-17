"""Per-hash x per-category reliability map -- measure it, don't assume it.

Phase D wants to drop signals known-broken for a predicted manipulation. We have
only ever scored ORB and sHash per category (PROGRESS.md 5). Before Phase D
hardcodes which hashes each router flag gates, this does the cheap analogous pass
for the three binary hashes so the gating is grounded in measurement.

Why this matters concretely: pixelation destroys ORB (28.5% detection), so it is
tempting to also distrust pHash under pixelation. But pHash coarsens the image to
32x32 -> low-frequency DCT the same way pixelation does, so it may well SURVIVE
pixelation. That is an empirical question, and this answers it.

The hashes are computed with Buchner's imagehash -- the exact oracle our C11
ports were validated bit-exact against -- so this is Python-only and adds no C11:
    aHash    = imagehash.average_hash
    pHash    = imagehash.phash
    hsvHash  = imagehash.colorhash
All three are DISTANCES: a pair is flagged a duplicate when dist <= threshold
(opposite polarity to ORB's inlier count). Unlike ORB, the hashes resize
internally, so they impose no minimum resolution -- no normalisation needed.

Two stages, like score_dataset.py (cache) + evaluate.py (report):
  * caches data/<split>/hash_scores.csv (per-pair Hamming distances), computed
    once; hashes are memoised per filename since originals recur across rows.
  * prints a per-hash x per-category detection table at each hash's best (oracle)
    threshold, with the cached ORB column alongside for a complete four-signal map.

Usage:
    training/.venv/bin/python python/router/hash_reliability.py --split test
    training/.venv/bin/python python/router/hash_reliability.py --split test --recompute
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import imagehash
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geometric"))
from categories import (  # noqa: E402
    ALL_POSITIVES,
    CONTROL_POSITIVES,
    GEOMETRIC_POSITIVES,
    NEGATIVES,
    NON_GEOMETRIC_POSITIVES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["original_image", "copy_image", "manipulation_type", "is_copy",
          "ahash_dist", "phash_dist", "hsvhash_dist"]
# Distance grids: aHash/pHash are 64-bit, hsvHash is 42-bit.
GRIDS = {"ahash_dist": range(0, 65), "phash_dist": range(0, 65), "hsvhash_dist": range(0, 43)}
HASH_LABEL = {"ahash_dist": "aHash", "phash_dist": "pHash", "hsvhash_dist": "hsvHash"}
ORB_THRESHOLD = 16  # the geometric signal's train-tuned operating point (PROGRESS.md 5)


def compute_cache(split_dir: Path) -> Path:
    images_dir = split_dir / "images"
    with open(split_dir / "metadata.csv", newline="") as f:
        rows = list(csv.DictReader(f))

    cache: dict[str, tuple] = {}

    def hashes(filename: str) -> tuple:
        if filename not in cache:
            with Image.open(images_dir / filename) as im:
                cache[filename] = (
                    imagehash.average_hash(im),
                    imagehash.phash(im),
                    imagehash.colorhash(im),
                )
        return cache[filename]

    out_path = split_dir / "hash_scores.csv"
    started = time.time()
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, start=1):
            o = row["original_image"].strip()
            c = row["copy_image"].strip()
            ao, po, ho = hashes(o)
            ac, pc, hc = hashes(c)
            writer.writerow({
                "original_image": o, "copy_image": c,
                "manipulation_type": row["manipulation_type"].strip(),
                "is_copy": row["is_copy"].strip(),
                "ahash_dist": ao - ac, "phash_dist": po - pc, "hsvhash_dist": ho - hc,
            })
            if i % 5000 == 0 or i == len(rows):
                rate = (time.time() - started) / i
                print(f"  {i}/{len(rows)}  {rate*1000:.1f} ms/pair  "
                      f"eta {rate*(len(rows)-i)/60:.1f} min", flush=True)
    print(f"{len(rows)} pairs -> {out_path}  ({(time.time()-started)/60:.1f} min)")
    return out_path


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        # Derived keys go AFTER the **r splat: is_copy is itself a CSV column, so
        # splatting last would overwrite the parsed bool with the raw "0"/"1"
        # string -- and both are truthy, which silently makes every row a positive.
        return [
            {**r, "category": r["manipulation_type"], "is_copy": r["is_copy"] == "1"}
            for r in csv.DictReader(f)
        ]


FP_BUDGET = 0.10  # operating point: at most this false-positive rate on non_duplicate


def operating_threshold(rows, field, budget=FP_BUDGET) -> tuple[int, float]:
    """Most-permissive threshold whose FP rate on negatives stays within budget.

    A fixed FP budget rather than an F1 sweep, so that every signal is read at the
    SAME operating point and the per-category detection rates are comparable across
    hashes and against ORB (whose t=16 sits at ~13% FP). An F1 sweep would also be
    a poor objective here: this split is 82.6% positive, which rewards flagging
    everything.

    Detection and FP both rise monotonically with t (flag when dist <= t), so the
    largest t within budget maximises detection at that FP.
    """
    neg = [int(r[field]) for r in rows if not r["is_copy"]]
    n_neg = max(1, len(neg))
    chosen, chosen_fp = GRIDS[field].start, 0.0
    for t in GRIDS[field]:
        fp = sum(d <= t for d in neg) / n_neg
        if fp <= budget:
            chosen, chosen_fp = t, fp
        else:
            break
    return chosen, chosen_fp


def detection_rate(rows, field, threshold) -> float:
    return sum(int(r[field]) <= threshold for r in rows) / len(rows) if rows else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--recompute", action="store_true")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    cache_path = split_dir / "hash_scores.csv"
    if args.recompute or not cache_path.exists():
        compute_cache(split_dir)

    rows = load_rows(cache_path)

    # Optional ORB column, for a complete four-signal map next to §5.
    orb_by_pair, orb_present = {}, (split_dir / "orb_scores.csv").exists()
    if orb_present:
        for r in load_rows(split_dir / "orb_scores.csv"):
            orb_by_pair[(r["original_image"], r["copy_image"])] = int(r["orb_inliers"])

    operating = {field: operating_threshold(rows, field) for field in GRIDS}
    thresholds = {field: t for field, (t, _) in operating.items()}
    print(f"\nsplit={args.split}   operating points at <={FP_BUDGET:.0%} FP on non_duplicate: " +
          "  ".join(f"{HASH_LABEL[f]}<={t} (FP {fp:.1%})" for f, (t, fp) in operating.items()) +
          (f"   ORB>{ORB_THRESHOLD}" if orb_present else ""))

    print("\nDetection rate by category (each signal at its own <=10% FP operating point):")
    header = f"  {'category':<28}{'n':>6}" + "".join(f"{HASH_LABEL[f]:>9}" for f in GRIDS)
    if orb_present:
        header += f"{'ORB':>9}"
    print(header)

    groups = [
        ("-- ORB's job (geometric) --", sorted(GEOMETRIC_POSITIVES)),
        ("-- control --", sorted(CONTROL_POSITIVES)),
        ("-- not ORB's job --", sorted(NON_GEOMETRIC_POSITIVES)),
        ("-- negatives (FP rate) --", sorted(NEGATIVES)),
    ]
    for title, cats in groups:
        print(f"  {title}")
        for cat in cats:
            sub = [r for r in rows if r["category"] == cat]
            if not sub:
                continue
            line = f"  {cat:<28}{len(sub):>6}"
            for field in GRIDS:
                line += f"{detection_rate(sub, field, thresholds[field]):>8.1%}"
            if orb_present:
                orb_hits = [orb_by_pair.get((r["original_image"], r["copy_image"]))
                            for r in sub]
                orb_hits = [v for v in orb_hits if v is not None]
                orb_rate = (sum(v > ORB_THRESHOLD for v in orb_hits) / len(orb_hits)
                            if orb_hits else float("nan"))
                line += f"{orb_rate:>8.1%}"
            print(line)

    print("\nReading: a high number = that signal still detects that manipulation (trust it);"
          "\na low number = the manipulation breaks that signal (Phase D should drop it there).")


if __name__ == "__main__":
    main()
