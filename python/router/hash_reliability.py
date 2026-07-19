"""Per-signal x per-category reliability map -- measure it, don't assume it.

Phase D wants to drop signals known-broken for a predicted manipulation. We had
only ever scored ORB and sHash per category (PROGRESS.md 5). This does the
analogous pass for the three binary hashes so the gating is grounded in
measurement -- e.g. pixelation destroys ORB, so it is tempting to distrust pHash
too, but pHash coarsens to 32x32 -> low-frequency DCT the same way pixelation
does and may well SURVIVE it. That is an empirical question, and this answers it.

The hashes come from Buchner's imagehash -- the exact oracle our C11 ports were
validated bit-exact against -- so this is Python-only and adds no C11:
    aHash = average_hash, pHash = phash, hsvHash = colorhash
All three are DISTANCES (flag when dist <= t), opposite polarity to ORB's inlier
count. Unlike ORB they resize internally, so they impose no minimum resolution.

TWO MEASUREMENTS, because detection alone cannot tell you whether dropping a
signal helps:

  * detection -- of true category-X copies, how often does the signal fire?
  * FP on MANIPULATED negatives -- when the query is a category-X image and the
    candidate is the WRONG original, how often does the signal fire anyway?

That second axis is the silent/noisy distinction, and it is what decides whether
routing buys anything. A signal that is broken but SILENT (low detection, low FP)
simply abstains; dropping it cannot change a ">=2 agree" verdict, because it was
never voting. A signal that is broken and NOISY (low detection, high FP) votes at
random, and dropping it protects precision. Only the second is worth gating.

The manipulated negatives have to be constructed: the dataset's own negatives are
all *pristine* NFTs paired with an unrelated original, so they can only tell us
how a signal behaves on an untouched query. Pairing each manipulated copy against
a deterministically-chosen wrong original fills that gap. (That our negatives are
pristine-only is itself a dataset-design limitation -- the authors' reference set
does contain manipulated negatives.)

THRESHOLDS ARE DERIVED ON TRAIN, NEVER ON TEST. Picking an operating point on the
split you then report is the same methodology error that invalidated the imported
baseline (PROGRESS.md 4). `--split train` writes the thresholds; `--split test`
loads them and reports. Each signal is placed at a fixed <=10% FP budget on the
pristine negatives rather than by an F1 sweep, so every signal is read at the same
operating point and the columns are comparable (ORB's t=16 sits at ~13% FP).

Usage:
    training/.venv/bin/python python/router/hash_reliability.py --split train
    training/.venv/bin/python python/router/hash_reliability.py --split test
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import imagehash
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geometric"))
from categories import (  # noqa: E402
    CONTROL_POSITIVES,
    GEOMETRIC_POSITIVES,
    NEGATIVES,
    NON_GEOMETRIC_POSITIVES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNALS = ("ahash", "phash", "hsvhash")
LABEL = {"ahash": "aHash", "phash": "pHash", "hsvhash": "hsvHash"}
BITS = {"ahash": 64, "phash": 64, "hsvhash": 42}
FP_BUDGET = 0.10
ORB_THRESHOLD = 16  # the geometric signal's train-tuned operating point (PROGRESS.md 5)
NEGATIVE_PAIR_SEED = 20260717


def to_int(h) -> int:
    v = 0
    for bit in h.hash.flatten():
        v = (v << 1) | int(bit)
    return v


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# --------------------------------------------------------------------------- #
# sHash: crop_resistant_hash segment lists + the paper's mean-of-mins distance #
# --------------------------------------------------------------------------- #
# sHash is dHash-of-segments, so unlike the three fixed-width hashes it is a
# variable-length LIST per image and its distance is the directional mean-of-mins
# (PROGRESS 2). Phase C never measured it; Phase E's fork needs its per-category
# detection AND its silent/noisy FP, at the SAME <=10% FP operating point the other
# signals use (dist<=21, train-derived in detector_config.json). We cache the
# segment hashes per image (the only slow part, ~137 ms/img -- parallelised), then
# the pairwise distance is cheap arithmetic done for arbitrary pairs, exactly like
# `distances()` does Hamming for the binary hashes.

def _segments_one(args: tuple[str, str]) -> tuple[str, list[str]]:
    """Worker: (images_dir, filename) -> (filename, [segment hex, ...]). Top-level
    so multiprocessing can pickle it; a pure function of the image file."""
    images_dir, filename = args
    with Image.open(Path(images_dir) / filename) as img:
        segs = imagehash.crop_resistant_hash(img).segment_hashes
    return filename, [str(s) for s in segs]


def shash_segments(split_dir: Path, images: list[str], workers: int) -> dict[str, list[int]]:
    """image -> its dHash segment hashes as ints. Cached to shash_segments.csv so
    the expensive crop_resistant_hash pass is paid once; parallel when workers>1
    (order-independent output, so it never changes a result)."""
    cache_path = split_dir / "shash_segments.csv"
    cache: dict[str, list[int]] = {}
    if cache_path.exists():
        with open(cache_path, newline="") as f:
            for r in csv.DictReader(f):
                cache[r["image"]] = [int(h, 16) for h in r["segments"].split("|")] if r["segments"] else []
    missing = [i for i in images if i not in cache]
    if missing:
        images_dir = split_dir / "images"
        tasks = [(str(images_dir), n) for n in missing]
        started = time.time()

        def log(done: int) -> None:
            rate = (time.time() - started) / done
            print(f"  sHash-segmenting {done}/{len(missing)}  {rate*1000:.0f} ms/img  "
                  f"eta {rate*(len(missing)-done)/60:.1f} min", flush=True)

        if workers <= 1:
            for done, t in enumerate(tasks, start=1):
                name, hexes = _segments_one(t)
                cache[name] = [int(h, 16) for h in hexes]
                if done % 1000 == 0 or done == len(missing):
                    log(done)
        else:
            with Pool(workers) as pool:
                for done, (name, hexes) in enumerate(
                    pool.imap_unordered(_segments_one, tasks, chunksize=16), start=1
                ):
                    cache[name] = [int(h, 16) for h in hexes]
                    if done % 1000 == 0 or done == len(missing):
                        log(done)
        with open(cache_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["image", "segments"])
            for name, segs in cache.items():
                w.writerow([name, "|".join(f"{v:x}" for v in segs)])
        print(f"  cached {len(cache)} segment lists -> {cache_path}")
    return cache


def shash_distance(src_segments: list[int], tgt_segments: list[int]) -> float:
    """Mean over the SOURCE's segments of the closest Hamming distance in the target
    -- the paper's directional distance (shash_baseline.paper_distance), computed on
    ints via popcount so it needs no ImageHash reconstruction. Same value: dHash
    distance IS popcount of XOR. Direction original->copy (PROGRESS 2)."""
    if not src_segments or not tgt_segments:
        return float("inf")
    return sum(min(bin(s ^ t).count("1") for t in tgt_segments) for s in src_segments) / len(src_segments)


def image_hashes(split_dir: Path, images: list[str]) -> dict[str, tuple[int, int, int]]:
    """image -> (aHash, pHash, hsvHash) as ints. Cached: hashing is the only slow part,
    and caching per IMAGE (not per pair) lets us form arbitrary pairs for free."""
    cache_path = split_dir / "image_hashes.csv"
    cache: dict[str, tuple[int, int, int]] = {}
    if cache_path.exists():
        with open(cache_path, newline="") as f:
            for r in csv.DictReader(f):
                cache[r["image"]] = (int(r["ahash"], 16), int(r["phash"], 16), int(r["hsvhash"], 16))
    missing = [i for i in images if i not in cache]
    if missing:
        images_dir = split_dir / "images"
        started = time.time()
        for n, name in enumerate(missing, start=1):
            with Image.open(images_dir / name) as im:
                cache[name] = (
                    to_int(imagehash.average_hash(im)),
                    to_int(imagehash.phash(im)),
                    to_int(imagehash.colorhash(im)),
                )
            if n % 5000 == 0 or n == len(missing):
                rate = (time.time() - started) / n
                print(f"  hashing {n}/{len(missing)}  eta {rate*(len(missing)-n)/60:.1f} min",
                      flush=True)
        with open(cache_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["image", "ahash", "phash", "hsvhash"])
            for name, (a, p, h) in cache.items():
                w.writerow([name, f"{a:x}", f"{p:x}", f"{h:x}"])
        print(f"  cached {len(cache)} image hashes -> {cache_path}")
    return cache


def load_metadata(split_dir: Path) -> list[dict]:
    with open(split_dir / "metadata.csv", newline="") as f:
        return [
            {
                "original": r["original_image"].strip(),
                "copy": r["copy_image"].strip(),
                "category": r["manipulation_type"].strip(),
                "is_copy": r["is_copy"].strip() == "1",
            }
            for r in csv.DictReader(f)
        ]


def distances(hashes, a: str, b: str) -> dict[str, int]:
    ha, hb = hashes[a], hashes[b]
    return {sig: hamming(ha[i], hb[i]) for i, sig in enumerate(SIGNALS)}


def derive_thresholds(hashes, rows) -> dict:
    """Most-permissive threshold per signal whose FP on PRISTINE negatives stays
    within budget. Detection and FP both rise monotonically with t, so the largest
    t within budget maximises detection at that FP."""
    neg = [distances(hashes, r["original"], r["copy"]) for r in rows if not r["is_copy"]]
    out = {}
    for sig in SIGNALS:
        ds = [d[sig] for d in neg]
        chosen, chosen_fp = 0, 0.0
        for t in range(0, BITS[sig] + 1):
            fp = sum(x <= t for x in ds) / max(1, len(ds))
            if fp <= FP_BUDGET:
                chosen, chosen_fp = t, fp
            else:
                break
        out[sig] = {"threshold": chosen, "fp_on_pristine": chosen_fp}
    return out


def manipulated_negatives(rows) -> list[tuple[str, str, str]]:
    """(wrong_original, manipulated_copy, category) -- each true copy re-paired
    against a deterministically chosen original that is NOT its own."""
    rng = random.Random(NEGATIVE_PAIR_SEED)
    originals = sorted({r["original"] for r in rows})
    out = []
    for r in rows:
        if not r["is_copy"]:
            continue
        wrong = rng.choice(originals)
        while wrong == r["original"] and len(originals) > 1:
            wrong = rng.choice(originals)
        out.append((wrong, r["copy"], r["category"]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel workers for the sHash segment pass (default 1; 0 = all cores)")
    parser.add_argument("--no-shash", action="store_true",
                        help="skip the sHash column (avoids the crop_resistant_hash pass)")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    rows = load_metadata(split_dir)
    referenced = sorted({r["original"] for r in rows} | {r["copy"] for r in rows})
    hashes = image_hashes(split_dir, referenced)

    thr_path = args.data_dir / "train" / "hash_thresholds.json"
    if args.split == "train":
        derived = derive_thresholds(hashes, rows)
        thr_path.write_text(json.dumps(derived, indent=2))
        print(f"\nderived on TRAIN -> {thr_path}")
    else:
        if not thr_path.exists():
            raise SystemExit("run --split train first: thresholds must come from train, not test")
        derived = json.loads(thr_path.read_text())
        print(f"\nthresholds loaded from TRAIN ({thr_path.name}) -- never fitted on test")

    print("  " + "  ".join(
        f"{LABEL[s]}<={derived[s]['threshold']} (train FP {derived[s]['fp_on_pristine']:.1%})"
        for s in SIGNALS) + f"   ORB>{ORB_THRESHOLD}")

    # Optional ORB detection column, for a complete four-signal map next to §5.
    orb = {}
    orb_path = split_dir / "orb_scores.csv"
    if orb_path.exists():
        with open(orb_path, newline="") as f:
            for r in csv.DictReader(f):
                orb[(r["original_image"].strip(), r["copy_image"].strip())] = int(r["orb_inliers"])

    # sHash column (Phase E fork). Its operating point is the SAME <=10% FP budget
    # the three hashes use, already train-derived in detector_config.json (dist<=21);
    # we load it (never fit on test) and build the per-image segment cache.
    shash_cache: dict[str, list[int]] = {}
    shash_t: int | None = None
    if not args.no_shash:
        cfg_path = args.data_dir / "train" / "detector_config.json"
        if cfg_path.exists():
            shash_t = int(json.loads(cfg_path.read_text())["shash_threshold"])
            workers = args.workers if args.workers > 0 else (os.cpu_count() or 1)
            print(f"  sHash<={shash_t} (train-derived, detector_config.json; iso-FP with the hashes)")
            shash_cache = shash_segments(split_dir, referenced, workers)
        else:
            print("  (sHash column skipped: detector_config.json absent -- run tune_static.py first)")

    positives = [r for r in rows if r["is_copy"]]
    pristine_neg = [r for r in rows if not r["is_copy"]]
    manip_neg = manipulated_negatives(rows)

    def rate(pairs, sig):
        t = derived[sig]["threshold"]
        hits = sum(distances(hashes, a, b)[sig] <= t for a, b in pairs)
        return hits / len(pairs) if pairs else float("nan")

    def orb_rate(pairs):
        vals = [orb.get(p) for p in pairs]
        vals = [v for v in vals if v is not None]
        return sum(v > ORB_THRESHOLD for v in vals) / len(vals) if vals else float("nan")

    def shash_rate(pairs):
        if shash_t is None:
            return float("nan")
        hits = sum(shash_distance(shash_cache.get(a, []), shash_cache.get(b, [])) <= shash_t
                   for a, b in pairs)
        return hits / len(pairs) if pairs else float("nan")

    show_shash = shash_t is not None

    groups = [
        ("-- ORB's job (geometric) --", sorted(GEOMETRIC_POSITIVES)),
        ("-- control --", sorted(CONTROL_POSITIVES)),
        ("-- not ORB's job --", sorted(NON_GEOMETRIC_POSITIVES)),
    ]

    print(f"\n[1] DETECTION -- of true category-X copies, how often does the signal fire?")
    print(f"  {'category':<28}{'n':>6}" + "".join(f"{LABEL[s]:>9}" for s in SIGNALS) +
          (f"{'sHash':>9}" if show_shash else "") + (f"{'ORB':>9}" if orb else ""))
    for title, cats in groups:
        print(f"  {title}")
        for cat in cats:
            pairs = [(r["original"], r["copy"]) for r in positives if r["category"] == cat]
            if not pairs:
                continue
            line = f"  {cat:<28}{len(pairs):>6}" + "".join(f"{rate(pairs, s):>8.1%}" for s in SIGNALS)
            if show_shash:
                line += f"{shash_rate(pairs):>8.1%}"
            if orb:
                line += f"{orb_rate(pairs):>8.1%}"
            print(line)
    pairs = [(r["original"], r["copy"]) for r in pristine_neg]
    print(f"  -- pristine negatives (the classic FP rate) --")
    line = f"  {'non_duplicate':<28}{len(pairs):>6}" + "".join(f"{rate(pairs, s):>8.1%}" for s in SIGNALS)
    if show_shash:
        line += f"{shash_rate(pairs):>8.1%}"
    if orb:
        line += f"{orb_rate(pairs):>8.1%}"
    print(line)

    print(f"\n[2] FALSE POSITIVES on MANIPULATED negatives -- query is a category-X image,")
    print(f"    candidate is the WRONG original. Does the signal fire anyway?")
    print(f"  {'category':<28}{'n':>6}" + "".join(f"{LABEL[s]:>9}" for s in SIGNALS) +
          (f"{'sHash':>9}" if show_shash else ""))
    for title, cats in groups:
        print(f"  {title}")
        for cat in cats:
            pairs = [(a, b) for a, b, c in manip_neg if c == cat]
            if not pairs:
                continue
            line = f"  {cat:<28}{len(pairs):>6}" + "".join(f"{rate(pairs, s):>8.1%}" for s in SIGNALS)
            if show_shash:
                line += f"{shash_rate(pairs):>8.1%}"
            print(line)

    print("\nReading [1]: low = the manipulation breaks that signal's detection.")
    print("Reading [2]: high = the signal is NOISY there (fires on the wrong original), so")
    print("  dropping it protects precision. Low = it is merely SILENT, and dropping it")
    print("  cannot change a '>=2 agree' verdict, because it was never voting.")
    if show_shash:
        print("\nsHash fork (Phase E): read STANDALONE sHash here (its own vote), NOT §9.1's 71.1%")
        print("  pixelated figure -- that was the whole 4-hash PANEL's >=2 verdict, a different")
        print("  quantity. If [1] pixelated detection collapses from sHash's crop baseline, the")
        print("  detail flag can gate it (it predicts pixelation). Whether gating helps is a")
        print("  quorum question (drop it -> the >=2 rule can fall to >=1 among survivors), and")
        print("  [2] confirms it is SILENT there (FP ~= pristine), so dropping costs no precision.")


if __name__ == "__main__":
    main()
