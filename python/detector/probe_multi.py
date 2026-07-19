"""Chunk 3 probe (Phase E): does the EXISTING router's soft mass already fire the
right reliability flags on COMPOSED images, and how does that vary with the
manipulation ORDER? This is the yes/no gate before the detector (Chunk 4).

The router is single-label (Phase C) and never saw a composite. But the detector
consumes soft probability MASS, not the argmax (router_signal.reliability_flags):
a router torn between `pixelated` and `color_swap` can put mass on both and fire
BOTH flags. So the question is empirical, not a retrain decision:

  * DETAIL flag  P(pixelated)                         -> distrust ORB *and* sHash
  * COLOUR flag  P(color_swap)+P(background_change)   -> distrust hsvHash

For the deployable win the quorum only relaxes when TWO signals are distrusted at
once, i.e. when BOTH flags fire -- which is exactly the pixelate x colour state.
The ORDER split tests the tell-erasure risk (PROGRESS 7.1 combing): recolour's
histogram combing survives an EARLIER pixelation (colour-LAST) but is smoothed
away by a LATER one (colour-FIRST), so the colour flag should fire on
`pixelated__color_*` and collapse on `color_*__pixelated`.

No retrain, no thresholds fitted here -- it reads the shipped router's predict_proba.

Usage:
    training/.venv/bin/python python/detector/probe_multi.py --split multi_test
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from router_signal import reliability_flags, router_proba

REPO_ROOT = Path(__file__).resolve().parents[2]

DETAIL_MANIP = "pixelated"
COLOUR_MANIPS = ("color_swap_modify_saturate", "background_color_change")


def load_composite_copies(split_dir: Path) -> list[tuple[str, str, str]]:
    """(copy_image, manip_first, manip_second) for the positive (composite) rows."""
    out = []
    with open(split_dir / "metadata.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r["is_copy"].strip() == "1":
                out.append((r["copy_image"].strip(),
                            r["manip_first"].strip(), r["manip_second"].strip()))
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def rate(xs: list[float], tau: float) -> float:
    return sum(x > tau for x in xs) / len(xs) if xs else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="multi_test",
                        choices=["multi_train", "multi_test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--tau", type=float, default=0.5, help="soft-mass flag threshold")
    args = parser.parse_args()

    split_dir = args.data_dir / args.split
    proba, classes = router_proba(args.split, args.data_dir)
    rows = load_composite_copies(split_dir)
    print(f"[{args.split}] {len(rows)} composite positives   tau={args.tau}")

    agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"detail": [], "colour": [], "both": 0})
    for copy, first, second in rows:
        pr = proba.get(copy)
        if pr is None:
            continue
        d, c = reliability_flags(pr, classes)
        a = agg[(first, second)]
        a["detail"].append(d)
        a["colour"].append(c)
        if d > args.tau and c > args.tau:
            a["both"] += 1

    # --- full per-composition table -------------------------------------------
    print(f"\n[A] Per composition: mean soft mass and fire rate (detail=distrust ORB/sHash,")
    print(f"    colour=distrust hsvHash, both=quorum can relax to the 2 survivors)")
    print(f"  {'first__second':<52}{'n':>5}{'P(det)':>8}{'det%':>7}{'P(col)':>8}{'col%':>7}{'both%':>7}")
    for (first, second) in sorted(agg):
        a = agg[(first, second)]
        n = len(a["detail"])
        print(f"  {first + '__' + second:<52}{n:>5}"
              f"{mean(a['detail']):>8.2f}{rate(a['detail'], args.tau):>7.0%}"
              f"{mean(a['colour']):>8.2f}{rate(a['colour'], args.tau):>7.0%}"
              f"{a['both'] / n:>7.0%}")

    # --- the ORDER effect on the colour flag (the tell-erasure test) -----------
    print(f"\n[B] ORDER effect on the COLOUR flag -- pixelate x colour, both directions.")
    print(f"    Hypothesis (PROGRESS 7.1): colour-LAST keeps combing (flag fires);")
    print(f"    colour-FIRST is smoothed by the later pixelation (flag collapses).")
    print(f"  {'composition':<52}{'n':>5}{'col%':>8}{'role':>14}")
    for colour in COLOUR_MANIPS:
        for first, second in ((DETAIL_MANIP, colour), (colour, DETAIL_MANIP)):
            a = agg.get((first, second))
            if not a:
                continue
            role = "colour-LAST" if second == colour else "colour-FIRST"
            print(f"  {first + '__' + second:<52}{len(a['colour']):>5}"
                  f"{rate(a['colour'], args.tau):>8.0%}{role:>14}")

    # --- the gate verdict ------------------------------------------------------
    print(f"\n[C] GATE: for each pixelate x colour composition, the fraction where BOTH flags")
    print(f"    fire is the fraction where the quorum can relax. If that is healthy on the")
    print(f"    colour-LAST compositions, the shipped router already carries the multi signal")
    print(f"    -> proceed to Chunk 4. If it is ~0 even colour-LAST, soft mass is insufficient")
    print(f"    -> the tell-erasure finding (and only then consider a multi-label retrain).")
    for colour in COLOUR_MANIPS:
        for first, second in ((DETAIL_MANIP, colour), (colour, DETAIL_MANIP)):
            a = agg.get((first, second))
            if not a:
                continue
            role = "colour-LAST" if second == colour else "colour-FIRST"
            n = len(a["detail"])
            print(f"    {first + '__' + second:<52} both-fire {a['both'] / n:>5.0%}  ({role})")


if __name__ == "__main__":
    main()
