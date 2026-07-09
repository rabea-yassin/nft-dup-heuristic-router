# NFT Duplication Detection — Heuristic Router

A bare-metal, zero-allocation C11 pre-mint gatekeeper for NFT image duplication detection.
It sits in front of the multi-hash BK-tree detector described in *"Combating NFT Copymints
in Blockchain Networks: An Image Hashing Approach"* (Kotzer, Reviriego, Conde Diaz,
Rottenstreich) and makes its 4-hash query cheaper **without changing the decision rule
that gives that detector its accuracy.**

> **Status: early.** The C11 router itself (`src/`) hasn't been written yet. What exists
> so far is the dataset (`data/`) and the offline pipeline that generates labeled
> manipulation examples from it (`training/`). Directories for later Roadmap steps
> (`build/`, `logs/`, `third_party/`, `tests/`) are deliberately not created until their
> step is actually reached, to keep the repo free of empty, unexplained folders.

## Background

The reference paper detects NFT copymints by computing four independent perceptual
hashes for every image — **aHash**, **pHash**, **hsvHash**, **sHash** — each stored in
its own **BK-tree** (Burkhard-Keller tree) keyed on Hamming distance. Its best-performing
detector, the *2-Minimal Distance Detector*, queries **all four trees** for every new
image and flags a duplicate only when **at least two** hashes agree within threshold —
that cross-corroboration is what suppresses each hash's individual false positives and
is what makes it the paper's most accurate detector across every dataset it was tested on.

That accuracy has a cost: each hash function is only actually discriminative for a subset
of manipulation types (see the paper's Table III — e.g. `hsvHash` is the only reliable
signal for flips/rotations, but useless for background-color changes). Querying all four
trees on every image unconditionally means paying **4×O(log M)** tree-descent cost and
cache pressure at blockchain scale, even though for most images two or three of those four
queries were never going to change the outcome.

**Our one non-negotiable constraint: we will not trade away the 2-Minimal detector's
benchmarked accuracy for speed.** Anything below is only allowed to change *how fast* we
reach that detector's answer, never *what* the answer is, on any image our fast path isn't
confident about.

## Our Approach

Ethereum's move to Proof-of-Stake gives validators a predictable ~12s block window. We use
part of that budget to make full **pre-mint (conservative) validation** viable, instead of
the paper's optimistic-execution fallback — by making the common case of routing cheap,
while keeping the paper's exact detector as a guaranteed fallback for anything ambiguous.

1. **Feature extraction (O(1)–O(N), cache-friendly).** Every incoming image is reduced to
   a fixed 16-byte feature vector: aspect ratio, Laplacian variance, a quantized
   histogram-shift bucket, and X-axis center of mass.
2. **Offline classifier.** A shallow decision tree (trained once, offline, in Python) maps
   that feature vector to a **confidence-scored prediction** over manipulation types (crop,
   rotation, color/palette shift, text/logo overlay, pixelation, background change, ...).
   Critically, it also has to recognize when it *isn't* confident — compound/adversarial
   manipulations are exactly what a copyminter would combine to defeat a single-hash
   assumption, and the paper's own Table III separation points were only measured on
   isolated, single manipulations.
3. **Codegen, not runtime ML.** The trained tree is walked by a codegen script and compiled
   into a hardcoded, zero-allocation C `if`/`else` cascade — no ML runtime, no heap
   allocation, no dynamic dispatch in the hot path.
4. **Confidence-gated dynamic thresholding ("Strategy B′"), not hard routing.** The cascade
   never removes a hash's vote outright. Instead:
   - **High-confidence, clean prediction** → per-hash Hamming thresholds are tuned for the
     predicted manipulation (per Table III), and hashes the manipulation is known to break
     have their threshold driven to 0 — effectively disabling them for near-duplicate
     purposes while still catching pixel-identical hits. Trees are queried **in confidence
     order**, and the search **short-circuits as soon as two hashes agree**, which is
     exactly the paper's 2-Minimal rule, just reordered and stopped early rather than
     replaced.
   - **Low confidence / ambiguous / suspected compound manipulation** → skip all of the
     above and fall back to querying all four trees at the paper's **default** thresholds.
     This is the safety net: on anything the classifier isn't sure about, we inherit the
     paper's exact, already-benchmarked accuracy rather than gambling on a heuristic.
   - **A threshold of 0 is served by an O(1) exact-match table, not a BK-tree walk** — a
     BK-tree searched at distance 0 is just an expensive way to ask "does this exact hash
     exist," which a hash map answers without a single tree descent.
   - The **write path is unconditional**: every new image's hashes are inserted into all
     four trees regardless of what the router predicted for it. Routing only ever affects
     which trees a *query* consults, never what the index contains — so a future query
     from a smarter classifier (or the fallback path) can never miss data that today's
     prediction chose not to look at.

The net effect: average-case cost drops well below 4×O(log M) on confidently-classified
images (often 1–2 tree descents, sometimes just an O(1) lookup), while worst-case behavior
on anything ambiguous is *identical* to the paper's benchmarked 2-Minimal detector — never
worse.

## Toolchain

The runtime stays hand-written C11. The reasoning, and where we deliberately *don't*
reinvent the wheel, matters enough to spell out:

- **Why not just use Johannes Buchner's [`imagehash`](https://github.com/JohannesBuchner/imagehash)
  directly?** It already implements `average_hash`/`phash`/`colorhash`/`crop_resistant_hash`
  — what the paper's aHash/pHash/hsvHash/sHash experiments actually ran on (paper: "these
  hash algorithms have an available Python implementation by Buchner") — and is well-tested.
  One correction worth flagging: the paper's Section II-C(v) *describes* hsvHash as a
  block/region-based statistical hash (citing Tang et al. 2013), but `colorhash()` is
  actually a **global** histogram (fractions of black/gray/hue-binned-saturated pixels
  across the whole image) — a different algorithm than the cited paper's, not just a
  looser implementation of it. Since the paper doesn't reproduce Tang et al.'s algorithm in
  enough detail to reimplement from scratch, and its published numbers were produced by
  whatever Buchner's library actually does, we port `colorhash()`'s real algorithm, not the
  block/region description. It's pure PIL/numpy/scipy, and embedding a Python interpreter
  (or shelling out per image) into a
  validator's hot path reintroduces interpreter startup cost, GIL contention, and
  unpredictable allocation — precisely what a zero-allocation, sub-millisecond router is
  trying to eliminate. It's also not an optimized C core under the hood we could just bind
  to; it's general-purpose array code, not something built for cache efficiency.
- **So `imagehash` isn't discarded — it's repurposed to two places it's actually a good
  fit for, and kept out of the one place it isn't:**
  1. **Differential-test oracle.** A fixed image corpus (covering the paper's manipulation
     categories) is run through `imagehash` once, offline, and the outputs are frozen into
     a checked-in fixture. Our C11 implementations are tested against that fixture — no
     live Python dependency at build or test time, only when fixtures are deliberately
     regenerated.
  2. **Training-data generator.** The offline classifier already needs a Python + scikit-learn
     environment; `training/` calls `imagehash` directly to produce labeled hash/feature
     data rather than maintaining a second, redundant Python reimplementation.
  3. **Never in the runtime binary.** No Python dependency at all in the shipped router.
- **Parity target is per-hash, and it's statistical, not literal, only where the reference
  algorithm itself isn't fully known:**
  - `aHash`/`pHash`: simple, fully-specified pixel operations — bit-exact parity is a
    realistic goal if we match PIL's exact resize filter and luma coefficients, with a
    small Hamming-distance tolerance accepted as passing.
  - `hsvHash`: once the ambiguity above is resolved (porting `colorhash()`'s actual
    algorithm), bit-exact parity is realistic here too — it's a fully deterministic global
    histogram over quantized HSV fractions, no different in principle from aHash/pHash.
    The one genuine imprecision-tolerant step is our own RGB->HSV conversion needing to
    match PIL's specific 0-255-scaled H/S/V convention (not the more common 0-360 degree
    convention for hue) — get that right and the rest is exact arithmetic.
  - `sHash`: if our segmentation approach differs from `crop_resistant_hash`'s internals
    (see below), comparing against the Python fixture stops being meaningful for this hash.
    Ground truth shifts to the paper's own published numbers directly.
- **`sHash`'s segmentation step is an open engineering call, not yet decided:** a
  hand-rolled flood-fill/union-find connected-components pass (simple, no external runtime
  dependency, consistent with the project's zero-allocation ethos) is the current default
  to prototype first. Pulling in OpenCV's connected-components/contour detection is a
  fallback only if early testing shows our segmentation quality is actually costing us
  accuracy — `sHash`'s entire value is segmentation quality (it's the paper's best cropping-
  robustness hash), so this is worth a real spike rather than deciding abstractly.
- **Versions used to generate parity fixtures / training data are pinned** (`imagehash`,
  Pillow, numpy, scipy) in `training/requirements.txt`, since a future default-filter change
  in any of those libraries would silently shift what "matching the paper" even means.

| Concern                | Choice                                                          |
|-------------------------|------------------------------------------------------------------|
| Core language           | C11 — zero-allocation hot path, no RAII/template indirection    |
| Build system            | CMake (out-of-source build in `build/`)                          |
| Offline training        | Python 3 + scikit-learn (shallow `DecisionTreeClassifier`)       |
| Reference/training hashes | Buchner's `imagehash`, used directly (not reimplemented) — differential-test oracle + training-data generator only, never in the runtime binary |
| Codegen                 | `training/codegen.py` walks the trained tree → emits `src/router/generated_cascade.h`, which **is committed** so the C project builds standalone without a Python dependency at build time |
| BK-tree distance metric | Hamming distance over fixed-width hash ints (popcount)           |
| Zero-threshold queries  | O(1) exact-match hash table, bypassing the BK-tree entirely      |

## Repository Structure

```
.
├── src/
│   ├── features/     # 16-byte feature vector extraction (aspect ratio, Laplacian
│   │                 # variance, histogram-shift bucket, center of mass)
│   ├── hashes/       # One directory per hash (C11), each with its own README:
│   │   ├── common/   #   shared: box-filter downsample (aHash/pHash) + bit-exact
│   │   │             #   Pillow op ports (dHash/sHash) — see common/README.md
│   │   ├── ahash/    #   average hash (64-bit)
│   │   ├── phash/    #   perceptual/DCT hash (64-bit)
│   │   ├── hsvhash/  #   global HSV color histogram (42-bit, bit-exact colorhash)
│   │   ├── dhash/    #   difference hash (64-bit); sHash's per-segment hash_func
│   │   └── shash/    #   segmentation hash (list of dHashes, crop-resistant)
│   ├── bktree/       # Generic BK-tree over fixed-width hashes + Hamming distance
│   │                 # (instantiated once per hash type), plus the O(1) exact-match
│   │                 # table used when a query's threshold is 0
│   ├── router/       # Confidence-gated dispatch (Strategy B′) + generated_cascade.h
│   │                 # (committed, codegen output)
│   └── bench/        # Benchmark harness: baseline 4x-probe vs router (avg/worst case)
├── training/                    # Offline Python pipeline (own venv, see requirements.txt)
│   ├── requirements.txt         # Pinned Pillow/numpy (+ imagehash/scikit-learn later)
│   ├── manipulations.py         # OpenSea-copymint manipulation functions (see Dataset)
│   └── generate_dataset.py      # Builds data/train/ + data/test/ from data/raw/
└── data/
    ├── raw/                     # Balanced real NFT corpus (see Dataset) — gitignored
    ├── extra/                   # 10k CryptoPunk sprites, held out of raw/ — gitignored
    ├── reference/                # Borrowed/derived reference material — gitignored
    │   ├── nft_classifier.csv    #   id -> collection mapping (source of raw/'s naming)
    │   └── test_manipulations/   #   paper authors' own labeling/hash reference set
    ├── example/                  # Git-tracked: full pipeline on 2 images (see Dataset)
    │   ├── raw/                  #   1 random Azuki + 1 random BAYC source image
    │   └── generated/            #   generate_dataset.py's output on just those 2
    ├── train/                    # Generated by generate_dataset.py — gitignored
    └── test/                     # Generated by generate_dataset.py — gitignored

Not yet created (appear at their Roadmap step): build/, logs/, third_party/, tests/
```

## Dataset

Three sources feed `data/` (all gitignored except `data/example/`, see below):

- **[`tunguz/cryptopunks`](https://www.kaggle.com/datasets/tunguz/cryptopunks)** (Kaggle) —
  10,000 CryptoPunk sprites; this is the exact dataset the paper itself cites (footnote 2)
  for its Table V CryptoPunics evaluation. **Held in `data/extra/` as `cryptopunks#<id>.png`,
  deliberately kept out of `raw/`:** at 10,000 images against 1,000 each of the other three
  collections, including it in the training corpus would let CryptoPunks-specific visual
  patterns dominate the router's classifier and tilt it toward whatever discriminates a
  CryptoPunk sprite rather than what discriminates a manipulation type. It's kept around in
  `extra/` for later large-scale/stress testing (e.g. BK-tree scaling, Table VII-style
  timing) where raw volume matters more than class balance.
- **[`shaunmak/nft-classifier`](https://www.kaggle.com/datasets/shaunmak/nft-classifier)**
  (Kaggle) — 3,000 real images spanning three collections (Azuki, BAYC, CryptoPunks), 1,000
  each — this balanced set is what actually populates `data/raw/`. The images are
  numerically named with no collection info in the filename; `data/reference/nft_classifier.csv`
  is the only thing that maps each numeric id to its collection, kept as the record of where
  the naming came from. Flattened into `data/raw/` as `azuki_#<id>.png`, `bayc_#<id>.png`,
  `cp_#<id>.png` (`#<id>` = the numeric filename, looked up against the CSV; index `#0` has
  no corresponding image in the download and was skipped). These are real, unmanipulated
  images — the base corpus `training/generate_dataset.py` manipulates to build labeled
  training data.
- **`data/reference/test_manipulations/`** — obtained directly from the paper's authors, *not*
  a public Kaggle set. Treated as a **reference, not a training set**: 405 manipulated
  CryptoPunk images plus `final_test_metadata.csv` (1,803 rows) giving `(original, copy,
  is_copy, manipulation type)` pairs with the authors' own precomputed hash values and
  Hamming distances per pair. Shows exactly how the authors structured labeling and hash
  computation for their own evaluation, and is authoritative enough to double as a
  differential-test fixture for our C11 hash implementations later — but since it was
  borrowed informally rather than published, it stays local and shouldn't be redistributed
  or used as the basis of any results we publish without checking with the authors first.

**`data/example/` is the one exception to "all of `data/` is gitignored."** It's a tiny,
git-tracked demo (public Kaggle-sourced images only — nothing derived from
`test_manipulations/`) showing the full pipeline end to end on just 2 images (one random
Azuki, one random BAYC): `example/raw/` holds the 2 source images, and
`example/generated/` holds the result of running `training/generate_dataset.py`'s logic on
exactly those 2 — the downscaled originals, every manipulated variant, and a `metadata.csv`
in the same schema as the real `data/train/`/`data/test/` output. It's what the pipeline
produces, visible on GitHub, without shipping the full 75,000-pair corpus.

### Labeled training data (`training/generate_dataset.py`)

`data/raw/`'s 3,000 images have no manipulation labels on their own, but the router's
decision tree needs them to train. `training/generate_dataset.py` builds them: for every
base image it applies each of the paper's OpenSea-copymint categories (flip/rotate/mirror,
crop/resize/reposition, text/logo/emoji overlay, background-color change, pixelation,
color swap/saturate, plus an exact-copy control) via `training/manipulations.py`, downscales
everything to a `--max-dim` (default 256px, aspect-ratio-preserving — aspect ratio is one of
the router's own features, so it must survive this step) to keep the generated set small and
fast to iterate on, and writes both the images and a `metadata.csv`
(`original_image,copy_image,manipulation_type,is_copy`) per split into `data/train/` and
`data/test/`. Splitting happens **by base image before any manipulation is applied**, so a
given image's variants never cross the train/test boundary. One non-duplicate (negative)
pair per base image is generated too, matching the paper's own evaluation methodology.

Run it (from the repo root, using the pinned venv):
```
python3 -m venv training/.venv && training/.venv/bin/pip install -r training/requirements.txt
training/.venv/bin/python training/generate_dataset.py
```

## Roadmap

1. ~~Assemble labeled dataset under `data/`~~ — done via `training/generate_dataset.py`
2. Implement `aHash`/`pHash`/`hsvHash`/`sHash` in C11 (`src/hashes/`)
3. Implement the generic BK-tree + O(1) exact-match table (`src/bktree/`)
4. Implement the 16-byte feature extractor (`src/features/`)
5. Build the `imagehash`-backed parity fixture generator and differential tests
   (`training/generate_parity_fixtures.py`, `tests/parity/` — created at this step, not before)
6. Train the shallow, confidence-aware decision tree + build the codegen script (`training/`)
7. Generate `src/router/generated_cascade.h` and wire up Strategy B′ dispatch (fallback
   path, confidence gating, early-exit ordering) in `src/router/`
8. Prototype `sHash` segmentation (hand-rolled flood-fill vs. OpenCV) and decide based on
   measured accuracy, not assumption
9. Build the baseline-vs-router benchmark (`src/bench/`) and validate both the accuracy
   parity (never worse than the paper's 2-Minimal detector) and the average-case speedup

## Reference

A. Kotzer, P. Reviriego, J. Conde Diaz, O. Rottenstreich, *"Combating NFT Copymints in
Blockchain Networks: An Image Hashing Approach."*
