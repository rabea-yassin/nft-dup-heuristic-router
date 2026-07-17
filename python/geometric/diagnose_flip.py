"""Why is ORB's `flip_rotate_mirror` (81.7%) so much weaker than crop (98.3%)?

It looked like a bug in ORB's mirror handling. It isn't -- it is our dataset, and
this script is the measurement that says so.

`training/manipulations.py:flip_rotate_mirror` applies an optional 50/50 h/v mirror
and then ALWAYS a continuous rotation (`rng.uniform(1, 359)`, expand -> transparent
corner fill -> resize back). The angle is never 0, so the category contains **no
pure mirrors at all**: every sample is a compound mirror-rotate-resize. The label
promises three attacks and delivers one, and the two it fuses have very different
difficulty.

So we score the components separately against the same originals:

  * pure mirror        -- a mirror and nothing else (no rotation, no resample, no fill)
  * rotate + resize    -- the generator's rotation path, without the mirror
  * flip_rotate_mirror -- what the dataset actually contains

If the mirror path were broken, the first row would be poor. It is not: ORB matches
mirrors essentially perfectly, and the whole deficit is CryptoPunks under rotation
(bicubic rotation + LANCZOS resize-back destroys exactly the hard pixel-art edges
ORB keys on, and a 24x24 punk has no detail to spare).

Consequence worth carrying: every "flip" number in this project describes
*rotate+resize*, not mirroring. The authors' own reference set keeps these as
separate categories (`rotation` vs `left_to_right`/`top_to_bottom`); we collapsed a
distinction their data makes.

Writes its variants to a temporary directory and deletes them; nothing is cached.

Usage:
    training/.venv/bin/python python/geometric/diagnose_flip.py --split test
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import statistics
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "training"))
from categories import collection_of  # noqa: E402
from manipulations import flip_rotate_mirror  # noqa: E402
from orb_match import OrbMatcher  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ORB_THRESHOLD = 16  # the geometric signal's train-tuned operating point (PROGRESS.md 5)
VARIANTS = (("m", "pure mirror"), ("r", "rotate+resize only"), ("f", "our flip_rotate_mirror"))


def pick_originals(images_dir: Path, per_collection: int) -> list[str]:
    """Deterministic: the first N unmanipulated files of each collection, sorted."""
    picks: list[str] = []
    for collection in ("azuki", "bayc", "cp"):
        names = sorted(
            os.path.basename(p)
            for p in glob.glob(str(images_dir / f"{collection}_*.png"))
            if "__" not in os.path.basename(p)
        )
        picks += names[:per_collection]
    return picks


def build_variants(images_dir: Path, work: Path, picks: list[str], seed: int) -> None:
    """Write each original plus its three variants into `work`.

    The RNG is shared across the rotate-only and flip_rotate_mirror calls in this
    exact order, so the run is reproducible.
    """
    rng = random.Random(seed)
    for name in picks:
        with Image.open(images_dir / name) as im:
            im.load()
        im.save(work / name)
        # (a) a mirror and nothing else -- no rotation, no resample, no corner fill
        im.transpose(Image.FLIP_LEFT_RIGHT).save(work / f"m__{name}")
        # (b) the generator's rotation path, isolated from the mirror
        rotated = im.convert("RGBA").rotate(
            rng.uniform(1, 359), expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0)
        )
        rotated.resize(im.size, Image.LANCZOS).convert(
            im.mode if im.mode != "P" else "RGBA"
        ).save(work / f"r__{name}")
        # (c) what the dataset actually contains
        flip_rotate_mirror(im, rng).save(work / f"f__{name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--per-collection", type=int, default=40)
    parser.add_argument("--threshold", type=int, default=ORB_THRESHOLD)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()

    images_dir = args.data_dir / args.split / "images"
    picks = pick_originals(images_dir, args.per_collection)

    with tempfile.TemporaryDirectory(prefix="diagnose_flip_") as tmp:
        work = Path(tmp)
        build_variants(images_dir, work, picks, args.seed)
        matcher = OrbMatcher(work)
        scores = {
            tag: [matcher.score(name, f"{tag}__{name}") for name in picks]
            for tag, _ in VARIANTS
        }

    t = args.threshold
    print(f"split={args.split}  n={len(picks)}  ORB flagged when inliers > {t}\n")
    print(f"  {'transform':<26}{'detected':>10}{'median inliers':>16}")
    for tag, label in VARIANTS:
        s = scores[tag]
        detected = sum(v > t for v in s) / len(s)
        print(f"  {label:<26}{detected:>9.1%}{statistics.median(s):>16.0f}")

    print(f"\nPer collection (punks are natively 24x24 pixel art):")
    print(f"  {'collection':<12}" + "".join(f"{label:>22}" for _, label in VARIANTS))
    for collection in ("azuki", "bayc", "cp"):
        idx = [i for i, name in enumerate(picks) if collection_of(name) == collection]
        if not idx:
            continue
        line = f"  {collection:<12}"
        for tag, _ in VARIANTS:
            sub = [scores[tag][i] for i in idx]
            line += f"{sum(v > t for v in sub)/len(sub):>21.1%}"
        print(line)

    print("\nReading: a high 'pure mirror' row means ORB's mirror handling is fine and the"
          "\nflip deficit comes from the rotation path -- which our generator always applies.")


if __name__ == "__main__":
    main()
