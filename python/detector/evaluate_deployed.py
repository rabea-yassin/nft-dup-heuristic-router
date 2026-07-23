"""The paper AS DEPLOYED: its 2-Minimal detector at the paper's OWN published
thresholds (frozen), vs the sHash->ORB swap, on both our test set and the authors'
own reference set. Reuses detector_common so scoring matches PROGRESS 9.

Why this exists (PROGRESS 9.1 "as deployed", 9.5 cross-generator). Sections 9.1-9.5 hold every panel to one
train-derived <=10% FP operating point (iso-FP, 9.8) -- the right *fair* comparison,
but it re-tunes the paper's hashes to our distribution, an advantage a deployed
detector never gets since it ships with fixed thresholds. Here the paper's panel is
frozen at its published operating point (NFT_Duplications, Table V text):

    2-Minimal published thresholds: aHash<=7, pHash<=15, hsvHash<=3, sHash<=17

If those fixed thresholds transfer badly to a new dataset, that failure COUNTS --
it is how the paper's algorithm actually behaves in the wild. The swap keeps the
paper's three hash thresholds and drops ORB in at its own train point, reported at
two ORB thresholds because ORB has no paper equivalent and the threshold is part of
what is being judged.

Run:  training/.venv/bin/python python/detector/evaluate_deployed.py
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import detector_common as dc

# The paper's own published 2-Minimal thresholds (NFT_Duplications.pdf, Table V text).
PAPER_THR = {"ahash": 7, "phash": 15, "hsvhash": 3, "shash": 17}
# ORB has no paper threshold; report at its two train-derived points (both ours).
ORB_POINTS = (16, 23)  # 16 = geometric signal (PROGRESS 5); 23 = iso-FP panel (PROGRESS 9)


def line(tag: str, m: dc.Metrics, n_pos: int, n_neg: int) -> str:
    floor = dc.flag_everything_f1(n_pos, n_neg)
    return (f"  {tag:<38} P {m.precision:5.1%}  R {m.recall:5.1%}  "
            f"F1 {m.f1:5.1%}   (flag-all {floor:.1%})")


def report(pairs: list[dc.Pair], title: str, our_thr: dict[str, float]) -> None:
    npos = sum(p.is_copy for p in pairs)
    nneg = len(pairs) - npos
    print(f"\n=== {title}  ({npos} pos / {nneg} neg) ===")

    a_paper = dc.evaluate(pairs, lambda p: dc.verdict_static(p, dc.SHASH_PANEL, PAPER_THR, 2))
    print("Paper 2-Minimal @ PAPER's published thresholds (frozen, as deployed):")
    print(line("A_paper {a7,p15,h3,s17}", a_paper, npos, nneg))

    a_ours = dc.evaluate(pairs, lambda p: dc.verdict_static(p, dc.SHASH_PANEL, our_thr, 2))
    print("Paper 2-Minimal @ OUR re-tuned thresholds (PROGRESS 9 baseline A):")
    print(line("A_retuned", a_ours, npos, nneg))

    print("Swap (ORB for sHash): PAPER's 3 hash thresholds frozen + ORB at our train point:")
    for ot in ORB_POINTS:
        thr = {**PAPER_THR, "orb": ot}
        b = dc.evaluate(pairs, lambda p: dc.verdict_static(p, dc.ORB_PANEL, thr, 2))
        print(line(f"B_swap {{a7,p15,h3}} + ORB>{ot}", b, npos, nneg))


def load_reference(data_dir: Path) -> list[dc.Pair]:
    """The authors' own set: hash distances are the authors' PRECOMPUTED values
    (final_test_metadata.csv); ORB scored by us (orb_scores.csv). As close to 'the
    paper's algorithm on the paper's data' as we can get."""
    ref = data_dir / "reference" / "test_manipulations"
    orb: dict[tuple[str, str], int] = {}
    with open(data_dir / "reference" / "orb_scores.csv", newline="") as f:
        for r in csv.DictReader(f):
            orb[(r["original_image"], r["copy_image"])] = int(r["orb_inliers"])
    pairs: list[dc.Pair] = []
    with open(ref / "final_test_metadata.csv", newline="") as f:
        for r in csv.DictReader(f):
            o, c = r["original_image"].strip(), r["copy_image"].strip()
            scores = {"ahash": int(r["aHash_dist"]), "phash": int(r["pHash_dist"]),
                      "hsvhash": int(r["hsvHash_dist"]), "shash": float(r["sHash_dist"]),
                      "orb": orb.get((o, c))}
            pairs.append(dc.Pair(o, c, "", r["is_copy"].strip() == "1", "", scores))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=dc.REPO_ROOT / "data")
    args = ap.parse_args()

    our_thr = dc.load_config(args.data_dir)
    print(f"paper published thresholds : {PAPER_THR}")
    print(f"our re-tuned thresholds    : {{" +
          ", ".join(f"'{k}': {v:g}" for k, v in our_thr.items()) + "}")

    report(dc.load_pairs("test", args.data_dir), "OUR test set", our_thr)

    ref_csv = args.data_dir / "reference" / "test_manipulations" / "final_test_metadata.csv"
    if ref_csv.exists():
        report(load_reference(args.data_dir), "AUTHORS' reference set (their precomputed hashes)", our_thr)
    else:
        print("\n(authors' reference set absent -- skipping; it stays local, PLAN 3)")


if __name__ == "__main__":
    main()
