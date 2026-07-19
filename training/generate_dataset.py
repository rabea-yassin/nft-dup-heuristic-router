"""
Builds the labeled manipulation-type dataset used to train the router's
decision tree, following the paper's OpenSea-copymint manipulation
categories (Section II-B / III-A) applied to our own real raw/ images
instead of the paper's 10 hand-picked base images.

Usage:
    training/.venv/bin/python training/generate_dataset.py

Output layout (all under data/, which is fully gitignored):
    data/<split>/images/<basename>.png                          (downscaled original)
    data/<split>/images/<basename>__<category>__<i>.png         (manipulated variant)
    data/<split>/metadata.csv  columns: original_image,copy_image,manipulation_type,is_copy

Base images are split into train/test *before* any manipulation is applied,
so a given base image's variants never cross the train/test boundary.
"""

import argparse
import csv
import random
from pathlib import Path

from PIL import Image

from manipulations import MANIPULATIONS, composition_pairs

REPO_ROOT = Path(__file__).resolve().parent.parent


def downscale(img: Image.Image, max_dim: int) -> Image.Image:
    """Resize so the longer edge is max_dim, preserving aspect ratio --
    aspect ratio is one of the router's own features, so it must survive
    this step untouched."""
    w, h = img.size
    if max(w, h) <= max_dim:
        return img.copy()
    scale = max_dim / max(w, h)
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def load_raw_images(raw_dir: Path) -> list[Path]:
    return sorted(p for p in raw_dir.glob("*.png"))


def split_train_test(paths: list[Path], train_frac: float, seed: int) -> tuple[list[Path], list[Path]]:
    rng = random.Random(seed)
    shuffled = paths[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * train_frac)
    return shuffled[:cut], shuffled[cut:]


def generate_split(
    paths: list[Path],
    out_dir: Path,
    variants_per_category: int,
    negatives_per_image: int,
    max_dim: int,
    seed: int,
) -> None:
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    rows = []
    downscaled_names = []

    # pass 1: downscale every original once and save it. Every variant in
    # pass 2 (including exact_copy) is derived from this same in-memory
    # `small`, not re-loaded from data/raw/ -- that's what guarantees an
    # exact_copy pair is pixel-identical (Hamming distance 0) rather than
    # picking up a spurious difference from the downscale step itself.
    # Never compare a file under data/raw/ (native resolution) directly
    # against a file under this split's images/ (resized to max_dim) --
    # that comparison would reintroduce exactly that artifact.
    originals = {}
    total = len(paths)
    for idx, path in enumerate(paths, start=1):
        with Image.open(path) as im:
            im = im.convert("RGBA") if im.mode in ("P", "LA") else im
            small = downscale(im, max_dim)
        name = path.name
        small.save(images_dir / name)
        originals[name] = small
        downscaled_names.append(name)
        if idx % 100 == 0 or idx == total:
            print(f"  [{out_dir.name}] downscaling {idx}/{total}", flush=True)

    # pass 2: manipulated variants, one block per base image
    for idx, name in enumerate(downscaled_names, start=1):
        base = originals[name]
        if idx % 100 == 0 or idx == total:
            print(f"  [{out_dir.name}] generating variants {idx}/{total}", flush=True)
        for category, fn in MANIPULATIONS.items():
            # deterministic functions (e.g. exact_copy) have no randomness --
            # asking for N copies would just produce N byte-identical files
            # with an identical label, so cap those at a single variant.
            count = 1 if getattr(fn, "deterministic", False) else variants_per_category
            for i in range(count):
                variant = fn(base, rng)
                variant_name = f"{Path(name).stem}__{category}__{i}.png"
                variant.convert(base.mode if base.mode != "P" else "RGBA").save(images_dir / variant_name)
                rows.append(
                    {
                        "original_image": name,
                        "copy_image": variant_name,
                        "manipulation_type": category,
                        "is_copy": 1,
                    }
                )

    # pass 3: negatives_per_image non-duplicate (negative) pairs per base
    # image, each against a distinct random partner from elsewhere in the
    # same split -- never across train/test. Kept deliberately subordinate
    # to the manipulation categories (see discussion in README/Dataset):
    # single-image features make "non_duplicate" an inherently fuzzy class,
    # and the router's confidence-gated fallback already absorbs the cost
    # of it being imprecise, so a handful of samples (enough to avoid
    # single-draw noise) is the right proportion, not parity with the
    # positive count.
    if len(downscaled_names) > 1 and negatives_per_image > 0:
        for name in downscaled_names:
            candidates = [n for n in downscaled_names if n != name]
            k = min(negatives_per_image, len(candidates))
            for other in rng.sample(candidates, k):
                rows.append(
                    {
                        "original_image": name,
                        "copy_image": other,
                        "manipulation_type": "non_duplicate",
                        "is_copy": 0,
                    }
                )

    with open(out_dir / "metadata.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["original_image", "copy_image", "manipulation_type", "is_copy"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"{out_dir}: {len(downscaled_names)} base images, {len(rows)} labeled pairs -> {out_dir / 'metadata.csv'}")


def generate_compose_split(
    paths: list[Path],
    out_dir: Path,
    variants_per_composition: int,
    negatives_per_image: int,
    max_dim: int,
    seed: int,
) -> None:
    """Phase E's multi-manipulation split: apply TWO manipulations in sequence to
    each base image, for every ordered pair (composition_pairs()), and record both
    component labels and their order. The metadata keeps the four canonical columns
    (so every existing reader still parses it) plus manip_first / manip_second.

    manipulation_type carries the composite label "{first}__{second}"; the negative
    rows keep the "non_duplicate" label, exactly like the single-manip split."""
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    rows = []
    downscaled_names = []

    # pass 1: downscale every original once (identical to generate_split -- a copy
    # is derived from this same in-memory `small`, never re-loaded from data/raw/).
    originals = {}
    total = len(paths)
    for idx, path in enumerate(paths, start=1):
        with Image.open(path) as im:
            im = im.convert("RGBA") if im.mode in ("P", "LA") else im
            small = downscale(im, max_dim)
        name = path.name
        small.save(images_dir / name)
        originals[name] = small
        downscaled_names.append(name)
        if idx % 100 == 0 or idx == total:
            print(f"  [{out_dir.name}] downscaling {idx}/{total}", flush=True)

    pairs = composition_pairs()
    print(f"  [{out_dir.name}] {len(pairs)} composite categories x {total} bases "
          f"x {variants_per_composition} variant(s)")

    # pass 2: composed variants -- second(first(base)), one block per base image.
    for idx, name in enumerate(downscaled_names, start=1):
        base = originals[name]
        if idx % 100 == 0 or idx == total:
            print(f"  [{out_dir.name}] composing variants {idx}/{total}", flush=True)
        for first, second in pairs:
            fn_first, fn_second = MANIPULATIONS[first], MANIPULATIONS[second]
            label = f"{first}__{second}"
            for i in range(variants_per_composition):
                intermediate = fn_first(base, rng)
                variant = fn_second(intermediate, rng)
                variant_name = f"{Path(name).stem}__{label}__{i}.png"
                variant.convert(base.mode if base.mode != "P" else "RGBA").save(images_dir / variant_name)
                rows.append(
                    {
                        "original_image": name,
                        "copy_image": variant_name,
                        "manipulation_type": label,
                        "manip_first": first,
                        "manip_second": second,
                        "is_copy": 1,
                    }
                )

    # pass 3: non_duplicate negatives -- identical scheme to the single-manip split
    # (unrelated pristine partner from the same split; our negatives are pristine-only,
    # a known limitation, PROGRESS 8.3). No new images written.
    if len(downscaled_names) > 1 and negatives_per_image > 0:
        for name in downscaled_names:
            candidates = [n for n in downscaled_names if n != name]
            k = min(negatives_per_image, len(candidates))
            for other in rng.sample(candidates, k):
                rows.append(
                    {
                        "original_image": name,
                        "copy_image": other,
                        "manipulation_type": "non_duplicate",
                        "manip_first": "",
                        "manip_second": "",
                        "is_copy": 0,
                    }
                )

    fieldnames = ["original_image", "copy_image", "manipulation_type",
                  "manip_first", "manip_second", "is_copy"]
    with open(out_dir / "metadata.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    pos = sum(r["is_copy"] == 1 for r in rows)
    neg = len(rows) - pos
    print(f"{out_dir}: {len(downscaled_names)} base images, {len(rows)} labeled pairs   "
          f"positives={pos} negatives={neg} ({pos/len(rows):.1%} positive)")
    counts: dict[str, int] = {}
    for r in rows:
        if r["is_copy"] == 1:
            counts[r["manipulation_type"]] = counts.get(r["manipulation_type"], 0) + 1
    print(f"  per composition (positives):")
    for comp in sorted(counts):
        print(f"    {comp:<54}{counts[comp]:>6}")
    print(f"  -> {out_dir / 'metadata.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--max-dim", type=int, default=256, help="longer-edge size after downscaling")
    parser.add_argument("--variants-per-category", type=int, default=3)
    parser.add_argument("--negatives-per-image", type=int, default=4)
    parser.add_argument("--train-frac", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mode", choices=["single", "compose"], default="single",
                        help="single = the paper's one-manipulation categories (default); "
                             "compose = Phase E's two-manipulation splits (data/multi_{train,test})")
    parser.add_argument("--variants-per-composition", type=int, default=1,
                        help="compose mode: variants per (base, composite category)")
    parser.add_argument("--max-compose-bases", type=int, default=600,
                        help="compose mode: cap base images per split (train has 2400; capping "
                             "keeps the tuning split's size and cost near the test split's)")
    args = parser.parse_args()

    raw_images = load_raw_images(args.raw_dir)
    if not raw_images:
        raise SystemExit(f"No .png files found under {args.raw_dir}")

    # Same base-image split as the single-manip data, so the router (trained on the
    # train bases' single manipulations) has never seen the TEST bases -- and never
    # any composite, on either split. Doubly held out (PLAN Phase E context).
    train_paths, test_paths = split_train_test(raw_images, args.train_frac, args.seed)
    print(f"{len(raw_images)} raw images -> {len(train_paths)} train / {len(test_paths)} test (by base image)")

    if args.mode == "single":
        generate_split(
            train_paths, args.output_dir / "train", args.variants_per_category,
            args.negatives_per_image, args.max_dim, args.seed,
        )
        generate_split(
            test_paths, args.output_dir / "test", args.variants_per_category,
            args.negatives_per_image, args.max_dim, args.seed + 1,
        )
    else:  # compose: Phase E's multi_train (tune) and multi_test (report)
        cap = args.max_compose_bases
        generate_compose_split(
            train_paths[:cap], args.output_dir / "multi_train", args.variants_per_composition,
            args.negatives_per_image, args.max_dim, args.seed,
        )
        generate_compose_split(
            test_paths[:cap], args.output_dir / "multi_test", args.variants_per_composition,
            args.negatives_per_image, args.max_dim, args.seed + 1,
        )


if __name__ == "__main__":
    main()
