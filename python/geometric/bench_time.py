"""How long do ORB and sHash take? -- the time half of the cost comparison.

PROGRESS.md section 6 measured what ORB costs in *bytes*. This measures what
it costs in *time*, on the same two tasks both signals perform:

  (a) fingerprint a new image -- ORB finds and describes its landmarks; sHash
      segments the image into its main objects and dHashes each one. For ORB
      we also time the query side, which describes the copy four times (the
      mirror variants in OrbMatcher.score).
  (b) compare one pair -- ORB: cross-checked brute-force match + RANSAC, once
      per mirror variant; sHash: the paper's mean-of-mins distance.

Index/search time is deliberately not measured (neither the sHash BK-tree nor
an ORB LSH index is benchmarked here).

TWO COMPARISONS, BOTH LABELLED
------------------------------
  as used        imagehash.crop_resistant_hash (Python + Pillow, the library
                 the paper's numbers came from)  vs  cv2 ORB (C++)
  like for like  our bit-exact C port of sHash (src/hashes/shash, driven by
                 src/bench/bench_shash.c)        vs  cv2 ORB (C++)

The as-used row mixes Python with C++, so it answers "how fast is each system
as built", not "which algorithm is cheaper" -- that is what the like-for-like
row is for. Even there the bias runs against sHash: OpenCV is SIMD-optimised,
while our port is a correctness-first -O2 build.

FAIRNESS
--------
* Decode is excluded on both sides and reported once separately (Pillow):
  every side decodes with a different library, so including it would not be
  symmetric. ORB keeps the pipeline's own IMREAD_GRAYSCALE pixels (so scores
  equal the cached ones); the RGB->grey step sHash does internally is timed
  separately for ORB with cvtColor, and reported, not folded in.
* Single-threaded everywhere (cv2.setNumThreads(1), BLAS/OMP pinned to 1).
* One warm-up, then `--repeats` timed runs per item; the per-item median is
  kept, and the summary reports median and p95 across items.
* Correctness: every timed pair's sHash distance must equal the cached
  data/<split>/shash_scores.csv, its ORB inliers must equal OrbMatcher.score()
  on this machine, and the C port's segment lists must equal imagehash's -- so
  the timed code is the measured code. (Agreement with the cached
  orb_scores.csv is reported, not asserted: it was computed with a different
  OpenCV build, and inlier counts drift by a few between builds.)

Usage:
    training/.venv/bin/python python/geometric/bench_time.py --split test
    # pilot
    training/.venv/bin/python python/geometric/bench_time.py --split test --pairs-per-type 10
    # native-resolution sensitivity (fingerprints only, data/raw)
    training/.venv/bin/python python/geometric/bench_time.py --raw-per-collection 50

Output: data/<split>/timing_images.csv and timing_pairs.csv (or
data/raw_timing_images.csv), plus a summary table on stdout.
"""

from __future__ import annotations

import os

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import csv
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from categories import collection_of  # noqa: E402
from orb_match import MIRROR_FLIPS, OrbMatcher, normalize_for_orb  # noqa: E402
from shash_baseline import paper_distance  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_BIN = REPO_ROOT / "build" / "bench_shash"
BENCH_SOURCES = [
    "src/hashes/common/hashes_common.c",
    "src/hashes/common/pil_ops.c",
    "src/hashes/dhash/dhash.c",
    "src/hashes/shash/shash.c",
    "third_party/stb_image_impl.c",
    "src/bench/bench_shash.c",
]
BENCH_INCLUDES = ["src/hashes/common", "src/hashes/dhash", "src/hashes/shash", "third_party"]
ORB_BUDGETS = (500, 128)  # default, and the size curve's sweet spot (PROGRESS section 6)


def timed(fn, repeats: int):
    """(result, median ns) over `repeats` runs after one warm-up."""
    result = fn()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        result = fn()
        samples.append(time.perf_counter_ns() - start)
    return result, statistics.median(samples)


def build_bench() -> Path:
    """Compile the C sHash bench into build/ (gitignored) when missing or stale."""
    sources = [REPO_ROOT / s for s in BENCH_SOURCES] + [
        REPO_ROOT / "src/hashes/shash/shash.h", REPO_ROOT / "src/hashes/common/pil_ops.h"]
    if BENCH_BIN.exists() and BENCH_BIN.stat().st_mtime > max(s.stat().st_mtime for s in sources):
        return BENCH_BIN
    BENCH_BIN.parent.mkdir(exist_ok=True)
    cmd = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra",
           *[f"-I{REPO_ROOT / i}" for i in BENCH_INCLUDES],
           *[str(REPO_ROOT / s) for s in BENCH_SOURCES], "-lm", "-o", str(BENCH_BIN)]
    subprocess.run(cmd, check=True)
    return BENCH_BIN


def run_c_bench(images_dir: Path, images: list[str], pairs: list[tuple[str, str]], repeats: int):
    """Run bench_shash; returns ({image: row}, {(orig, copy): row})."""
    binary = build_bench()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "images.txt").write_text("".join(f"{n}\n" for n in images))
        (tmp / "pairs.txt").write_text("".join(f"{a},{b}\n" for a, b in pairs))
        subprocess.run([str(binary), str(images_dir), str(tmp / "images.txt"), str(tmp / "pairs.txt"),
                        str(tmp / "img.csv"), str(tmp / "pair.csv"), str(repeats)], check=True)
        with open(tmp / "img.csv", newline="") as f:
            img_rows = {r["image"]: r for r in csv.DictReader(f)}
        with open(tmp / "pair.csv", newline="") as f:
            pair_rows = {(r["original_image"], r["copy_image"]): r for r in csv.DictReader(f)}
    return img_rows, pair_rows


def bench_images(images_dir: Path, images: list[str], repeats: int):
    """Per-image fingerprint timings for every Python-side method.

    Returns (rows, fingerprints) where fingerprints[name] holds what the pair
    stage compares: imagehash segments, and per ORB budget the original-side
    descriptors and the four query-side mirror variants.
    """
    matchers = {n: OrbMatcher(images_dir, orb_features=n) for n in ORB_BUDGETS}
    rows, fingerprints = {}, {}
    started = time.time()
    for i, name in enumerate(images, start=1):
        path = images_dir / name

        # decode, once, outside every timed region (reported on its own)
        def decode():
            img = Image.open(path)
            img.load()
            return img
        pil, decode_ns = timed(decode, repeats)
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        unchanged = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        code = {4: cv2.COLOR_BGRA2GRAY, 3: cv2.COLOR_BGR2GRAY}.get(
            unchanged.shape[2] if unchanged.ndim == 3 else 1)
        grey_ns = timed(lambda: cv2.cvtColor(unchanged, code), repeats)[1] if code is not None else 0

        segs, shash_py_ns = timed(lambda: imagehash.crop_resistant_hash(pil).segment_hashes, repeats)
        row = {"image": name, "collection": collection_of(name), "width": pil.width,
               "height": pil.height, "decode_pillow_ns": decode_ns, "rgb_to_grey_ns": grey_ns,
               "shash_py_ns": shash_py_ns, "segments": len(segs)}
        fp = {"segs": segs}

        for n, m in matchers.items():
            def fingerprint():
                return m._orb.detectAndCompute(normalize_for_orb(gray, m.working_edge), None)

            def query():
                img = normalize_for_orb(gray, m.working_edge)
                return [m._orb.detectAndCompute(img if f is None else cv2.flip(img, f), None)
                        for f in MIRROR_FLIPS]
            fp[f"orb{n}"], row[f"orb{n}_ns"] = timed(fingerprint, repeats)
            fp[f"orb{n}_query"], row[f"orb{n}_query_ns"] = timed(query, repeats)
            row[f"orb{n}_keypoints"] = len(fp[f"orb{n}"][0])
        rows[name], fingerprints[name] = row, fp
        if i % 100 == 0 or i == len(images):
            elapsed = time.time() - started
            print(f"  images {i}/{len(images)}  eta {elapsed / i * (len(images) - i) / 60:.1f} min",
                  flush=True)
    return rows, fingerprints


def bench_pairs(pairs, fingerprints, images_dir: Path, repeats: int):
    """Per-pair comparison timings, from already-computed fingerprints."""
    matchers = {n: OrbMatcher(images_dir, orb_features=n) for n in ORB_BUDGETS}
    rows = {}
    for original, copy in pairs:
        fo, fc = fingerprints[original], fingerprints[copy]
        dist, py_ns = timed(lambda: paper_distance(fo["segs"], fc["segs"]), repeats)
        row = {"shash_py_ns": py_ns, "shash_dist_py": dist}
        for n, m in matchers.items():
            kp_o, des_o = fo[f"orb{n}"]

            def compare():
                if des_o is None:
                    return 0
                return max(m._inliers(kp_c, des_c, kp_o, des_o) for kp_c, des_c in fc[f"orb{n}_query"])
            row[f"orb{n}_inliers"], row[f"orb{n}_ns"] = timed(compare, repeats)
            if n == ORB_BUDGETS[0] and des_o is not None:
                # breakdown: the descriptor match alone, without RANSAC
                row["orb500_match_only_ns"] = timed(lambda: [
                    m._matcher.match(des_c, des_o) for _, des_c in fc["orb500_query"] if des_c is not None
                ], repeats)[1]
            elif n == ORB_BUDGETS[0]:
                row["orb500_match_only_ns"] = 0
        rows[(original, copy)] = row
    return rows


def sample_pairs(metadata: Path, per_type: int, seed: int):
    """Stratified: up to `per_type` pairs of every manipulation type, negatives included."""
    by_type = defaultdict(list)
    with open(metadata, newline="") as f:
        for r in csv.DictReader(f):
            by_type[r["manipulation_type"].strip()].append(
                (r["original_image"].strip(), r["copy_image"].strip()))
    rng = random.Random(seed)
    chosen = []
    for kind in sorted(by_type):
        for pair in rng.sample(by_type[kind], min(per_type, len(by_type[kind]))):
            chosen.append((pair, kind))
    return chosen


def load_cache(path: Path, value: str) -> dict:
    with open(path, newline="") as f:
        return {(r["original_image"], r["copy_image"]): r[value] for r in csv.DictReader(f)}


def fmt(ns: float) -> str:
    if ns >= 1e6:
        return f"{ns / 1e6:.2f} ms"
    if ns >= 1e3:
        return f"{ns / 1e3:.1f} us"
    return f"{ns:.0f} ns"


def summarise(title: str, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    collections = sorted({r["collection"] for r in rows})
    print(f"\n{title}  (n={len(rows)}; median [p95]; per-collection medians)")
    for label, col in columns:
        vals = [float(r[col]) for r in rows]
        per = "  ".join(
            f"{c} {fmt(statistics.median(float(r[col]) for r in rows if r['collection'] == c))}"
            for c in collections)
        p95 = float(np.percentile(vals, 95))
        print(f"  {label:<38} {fmt(statistics.median(vals)):>10} [{fmt(p95):>10}]   {per}")


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def environment() -> str:
    cc = subprocess.run(["cc", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    return (f"{platform.platform()} | {platform.processor() or platform.machine()} | "
            f"python {platform.python_version()} | cv2 {cv2.__version__} | "
            f"imagehash {imagehash.__version__} | {cc} -O2 | single-threaded")


IMAGE_COLUMNS = [
    ("decode (Pillow, shared, excluded)", "decode_pillow_ns"),
    ("RGB->grey (cvtColor, for reference)", "rgb_to_grey_ns"),
    ("sHash  imagehash (as used)", "shash_py_ns"),
    ("sHash  C port (like for like)", "shash_c_ns"),
    ("ORB-500 fingerprint", "orb500_ns"),
    ("ORB-500 query side (x4 mirrors)", "orb500_query_ns"),
    ("ORB-128 fingerprint", "orb128_ns"),
    ("ORB-128 query side (x4 mirrors)", "orb128_query_ns"),
]
PAIR_COLUMNS = [
    ("sHash  imagehash distance (as used)", "shash_py_ns"),
    ("sHash  C distance (like for like)", "shash_c_ns"),
    ("ORB-500 match+RANSAC x4", "orb500_ns"),
    ("  of which: descriptor match only x4", "orb500_match_only_ns"),
    ("ORB-128 match+RANSAC x4", "orb128_ns"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="test")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--pairs-per-type", type=int, default=150)
    parser.add_argument("--raw-per-collection", type=int, default=0,
                        help="native-resolution run on data/raw instead (fingerprints only)")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    print(environment(), flush=True)

    if args.raw_per_collection:
        images_dir = args.data_dir / "raw"
        rng = random.Random(args.seed)
        by_col = defaultdict(list)
        for p in sorted(images_dir.glob("*.png")):
            by_col[collection_of(p.name)].append(p.name)
        images = [n for c in sorted(by_col) for n in rng.sample(by_col[c], min(args.raw_per_collection, len(by_col[c])))]
        pairs, kinds = [], {}
        out_images = args.data_dir / "raw_timing_images.csv"
    else:
        split_dir = args.data_dir / args.split
        images_dir = split_dir / "images"
        chosen = sample_pairs(split_dir / "metadata.csv", args.pairs_per_type, args.seed)
        pairs = [p for p, _ in chosen]
        kinds = dict(chosen)
        images = list(dict.fromkeys(n for p in pairs for n in p))
        out_images = split_dir / "timing_images.csv"
    print(f"{len(images)} images, {len(pairs)} pairs, repeats={args.repeats}", flush=True)

    c_img, c_pair = run_c_bench(images_dir, images, pairs, args.repeats)
    img_rows, fingerprints = bench_images(images_dir, images, args.repeats)
    for name, row in img_rows.items():
        c_segs = [int(h, 16) for h in c_img[name]["segments"].split("|")]
        py_segs = [int(str(h), 16) for h in fingerprints[name]["segs"]]
        assert c_segs == py_segs, f"C and imagehash sHash disagree on {name}"
        row["shash_c_ns"] = float(c_img[name]["hash_ns"])
        row["decode_stb_ns"] = float(c_img[name]["decode_ns"])
    img_list = [img_rows[n] for n in images]
    write_csv(out_images, img_list)
    summarise("FINGERPRINT A NEW IMAGE", img_list, IMAGE_COLUMNS)

    if pairs:
        shash_cache = load_cache(split_dir / "shash_scores.csv", "shash_dist")
        orb_cache = load_cache(split_dir / "orb_scores.csv", "orb_inliers")
        py_pairs = bench_pairs(pairs, fingerprints, images_dir, args.repeats)
        live = OrbMatcher(images_dir)  # the scoring path itself, run on this machine
        orb_cache_equal = 0
        pair_list = []
        for pair in pairs:
            r = py_pairs[pair]
            assert c_pair[pair]["shash_dist"] == shash_cache[pair], f"C sHash distance differs on {pair}"
            assert f"{r['shash_dist_py']:.4f}" == shash_cache[pair], f"imagehash distance differs on {pair}"
            assert r["orb500_inliers"] == live.score(*pair), f"ORB inliers differ from OrbMatcher on {pair}"
            orb_cache_equal += str(r["orb500_inliers"]) == orb_cache[pair]
            pair_list.append({"original_image": pair[0], "copy_image": pair[1],
                              "manipulation_type": kinds[pair], "collection": collection_of(pair[0]),
                              "shash_dist": shash_cache[pair], "orb500_inliers": r["orb500_inliers"],
                              "orb128_inliers": r["orb128_inliers"], "shash_py_ns": r["shash_py_ns"],
                              "shash_c_ns": float(c_pair[pair]["compare_ns"]),
                              "orb500_ns": r["orb500_ns"], "orb500_match_only_ns": r["orb500_match_only_ns"],
                              "orb128_ns": r["orb128_ns"]})
        write_csv(split_dir / "timing_pairs.csv", pair_list)
        summarise("COMPARE ONE PAIR", pair_list, PAIR_COLUMNS)
        print(f"\nall {len(pairs)} pairs: C and imagehash sHash distances equal shash_scores.csv; "
              f"ORB-500 inliers equal OrbMatcher.score() here")
        print(f"ORB-500 inliers equal the cached orb_scores.csv on {orb_cache_equal}/{len(pairs)} pairs "
              f"(the cache came from a different OpenCV build; RANSAC/ORB drift by a few inliers)")
    print(f"all {len(images)} images: C sHash segment lists equal imagehash's")


if __name__ == "__main__":
    main()
