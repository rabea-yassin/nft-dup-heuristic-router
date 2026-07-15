"""Tune the ORB inlier threshold -- on the geometric subset, not on everything.

This script also *demonstrates* why that distinction matters, by tuning twice:

  * "geometric"  -- ORB judged only on what it is for (crop/reposition, flip/
    rotate/mirror) against the non-duplicate negatives.
  * "global"     -- ORB judged on every positive category at once, which is what
    the original pipeline did. Chasing pixelated and colour-swapped images (which
    ORB fundamentally cannot detect) drags the threshold down and costs precision.

Printing both side by side turns "ORB should be a specialist" from an assertion
into a measurement.

Reads the cached scores from score_dataset.py; no image decoding here.

Usage:
    training/.venv/bin/python python/geometric/tune.py --split train
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from categories import ALL_POSITIVES, GEOMETRIC_POSITIVES, NEGATIVES  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_scores(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return [
            {
                "category": r["manipulation_type"],
                "is_copy": r["is_copy"] == "1",
                "score": int(r["orb_inliers"]),
                "original": r["original_image"],
            }
            for r in csv.DictReader(f)
        ]


def confusion(rows: list[dict], threshold: int) -> dict:
    cm = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for r in rows:
        flagged = r["score"] > threshold
        if r["is_copy"]:
            cm["tp" if flagged else "fn"] += 1
        else:
            cm["fp" if flagged else "tn"] += 1
    return cm


def metrics(cm: dict) -> dict:
    tp, fn, fp, tn = cm["tp"], cm["fn"], cm["fp"], cm["tn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / sum(cm.values()) if sum(cm.values()) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "accuracy": accuracy, **cm}


def best_threshold(rows: list[dict], grid: range) -> tuple[int, dict]:
    best_t, best_m = grid[0], None
    for t in grid:
        m = metrics(confusion(rows, t))
        if best_m is None or m["f1"] > best_m["f1"]:
            best_t, best_m = t, m
    return best_t, best_m


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="train", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    rows = load_scores(args.data_dir / args.split / "orb_scores.csv")
    negatives = [r for r in rows if r["category"] in NEGATIVES]
    grid = range(0, 151)

    subsets = {
        "geometric (ORB's actual job)": [
            r for r in rows if r["category"] in GEOMETRIC_POSITIVES
        ] + negatives,
        "global (every category, as before)": [
            r for r in rows if r["category"] in ALL_POSITIVES
        ] + negatives,
    }

    results = {}
    print(f"split={args.split}   negatives={len(negatives)}\n")
    print(f"{'tuned on':<36}{'thresh':>7}{'P':>9}{'R':>9}{'F1':>9}{'acc':>9}")
    for name, subset in subsets.items():
        t, m = best_threshold(subset, grid)
        results[name] = (t, m)
        print(
            f"{name:<36}{t:>7}{m['precision']*100:>8.1f}%{m['recall']*100:>8.1f}%"
            f"{m['f1']*100:>8.1f}%{m['accuracy']*100:>8.1f}%"
        )

    # What does the globally-tuned threshold cost us on the geometric job?
    geo_rows = subsets["geometric (ORB's actual job)"]
    t_geo = results["geometric (ORB's actual job)"][0]
    t_glob = results["global (every category, as before)"][0]
    m_geo = metrics(confusion(geo_rows, t_geo))
    m_glob_on_geo = metrics(confusion(geo_rows, t_glob))

    print(f"\nCost of tuning globally, measured on the geometric job:")
    print(f"{'threshold':<36}{'P':>9}{'R':>9}{'F1':>9}")
    print(
        f"{f'geometric-tuned (t={t_geo})':<36}{m_geo['precision']*100:>8.1f}%"
        f"{m_geo['recall']*100:>8.1f}%{m_geo['f1']*100:>8.1f}%"
    )
    print(
        f"{f'global-tuned (t={t_glob})':<36}{m_glob_on_geo['precision']*100:>8.1f}%"
        f"{m_glob_on_geo['recall']*100:>8.1f}%{m_glob_on_geo['f1']*100:>8.1f}%"
    )
    delta = (m_geo["f1"] - m_glob_on_geo["f1"]) * 100
    print(f"{'':<36}{'':>9}{'':>9}{delta:>+8.1f} F1 to specialising")

    print(f"\nChosen threshold for evaluate.py: {t_geo}")


if __name__ == "__main__":
    main()
