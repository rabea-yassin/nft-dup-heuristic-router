"""Evaluate the ORB geometric signal per category -- never as one global F1.

A single headline number is exactly what hid the original baseline's problems.
ORB is a specialist: it should be strong on crop/reposition and flip/rotate,
and weak on pixelation and colour edits. Reporting per category makes that
visible instead of averaging it away -- and the weakness is not a defect to
apologise for, it is the argument for keeping the other three hashes.

Also breaks results down per collection, because CryptoPunks (24x24 pixel art,
upscaled to reach ORB's minimum patch size) carry far less matchable structure
than the 256 px azuki/bayc artwork.

Reads the cached scores from score_dataset.py; no image decoding here.

Usage:
    training/.venv/bin/python python/geometric/evaluate.py --split test --threshold 20
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from categories import (  # noqa: E402
    CONTROL_POSITIVES,
    GEOMETRIC_POSITIVES,
    NEGATIVES,
    NON_GEOMETRIC_POSITIVES,
    collection_of,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_scores(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return [
            {
                "category": r["manipulation_type"],
                "is_copy": r["is_copy"] == "1",
                "score": int(r["orb_inliers"]),
                "collection": collection_of(r["original_image"]),
            }
            for r in csv.DictReader(f)
        ]


def rate(rows: list[dict], threshold: int) -> float:
    """Fraction flagged as duplicate. Recall for positives; FP-rate for negatives."""
    return sum(r["score"] > threshold for r in rows) / len(rows) if rows else 0.0


def describe(rows: list[dict]) -> str:
    if not rows:
        return "-"
    scores = sorted(r["score"] for r in rows)
    return f"{statistics.median(scores):>5.0f} {scores[len(scores)//10]:>5.0f} {scores[-len(scores)//10]:>5.0f}"


def report_group(title: str, categories, rows: list[dict], threshold: int, positive: bool) -> None:
    label = "detected" if positive else "flagged (FP)"
    print(f"\n{title}")
    print(f"  {'category':<28}{'n':>6}{label:>14}{'  median   p10   p90':>22}")
    for cat in sorted(categories):
        sub = [r for r in rows if r["category"] == cat]
        if not sub:
            continue
        print(f"  {cat:<28}{len(sub):>6}{rate(sub, threshold)*100:>13.1f}%   {describe(sub)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--threshold", type=int, required=True, help="from tune.py")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    rows = load_scores(args.data_dir / args.split / "orb_scores.csv")
    t = args.threshold
    print(f"split={args.split}  threshold={t}  (flagged when inliers > {t})")

    report_group("ORB's job -- expect HIGH detection:", GEOMETRIC_POSITIVES, rows, t, True)
    report_group("Control -- must be near 100%:", CONTROL_POSITIVES, rows, t, True)
    report_group(
        "Not ORB's job -- low is expected and fine (other hashes cover these):",
        NON_GEOMETRIC_POSITIVES, rows, t, True,
    )
    report_group("Negatives -- expect LOW:", NEGATIVES, rows, t, False)

    # Headline: ORB judged only on what it is for.
    geo = [r for r in rows if r["category"] in GEOMETRIC_POSITIVES]
    neg = [r for r in rows if r["category"] in NEGATIVES]
    tp = sum(r["score"] > t for r in geo)
    fn = len(geo) - tp
    fp = sum(r["score"] > t for r in neg)
    tn = len(neg) - fp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    print(f"\nGeometric subset only (the claim we actually make):")
    print(f"  precision {precision*100:.1f}%   recall {recall*100:.1f}%   F1 {f1*100:.1f}%")
    print(f"  tp={tp} fn={fn} fp={fp} tn={tn}")

    # Per collection: punks are upscaled 24x24 pixel art and carry less structure.
    print(f"\nGeometric detection by collection (upscaling cannot create information):")
    print(f"  {'collection':<12}{'n':>6}{'detected':>12}{'  negatives flagged':>20}")
    for coll in sorted({r["collection"] for r in rows}):
        g = [r for r in geo if r["collection"] == coll]
        n = [r for r in neg if r["collection"] == coll]
        if not g:
            continue
        print(f"  {coll:<12}{len(g):>6}{rate(g, t)*100:>11.1f}%{rate(n, t)*100:>18.1f}%")


if __name__ == "__main__":
    main()
