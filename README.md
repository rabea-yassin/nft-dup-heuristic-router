# NFT Copymint Detection — a Routed, ORB-Augmented Detector

An extension of *"Combating NFT Copymints in Blockchain Networks: An Image Hashing Approach"*
(Kotzer, Reviriego, Conde Diaz, Rottenstreich). The paper detects copymints by letting four
perceptual hashes vote at fixed thresholds. We change two things:

1. **We replace `sHash` with ORB feature matching** — because sHash cannot be soundly indexed,
   and ORB does its job (crop/geometric robustness) substantially better. *Measured, not asserted.*
2. **We route the signals** — a classifier predicts what was done to a query image, so the
   detector can stop trusting signals that are known-broken for that manipulation, instead of
   letting every hash vote unconditionally.

Both changes target **accuracy**, not speed.

> **Status.** The four paper hashes are reimplemented in C11 and **bit-exact** against the
> reference library (see [C11 hash suite](#the-c11-hash-suite-a-completed-result)). The ORB
> replacement, the router, and the final routed detector (Phase D) are all built and
> evaluated. The headline three-way (PROGRESS.md §9): the **sHash→ORB swap** is the entire
> gain (+5.8 F1 at the paper's rule, all signals compared at an equal ≤10% FP operating
> point), while **dynamic routing adds ≈0** within our distribution — a measured negative
> result, with the mechanism. Full history, findings and
> numbers live in **[PROGRESS.md](PROGRESS.md)** — this file is the *current architecture*.

---

## Background — what the paper does

The paper computes four independent perceptual hashes for every image — **aHash**, **pHash**,
**hsvHash**, **sHash** — each stored in its own **BK-tree** keyed on Hamming distance. Its
best-performing detector, the *2-Minimal Distance Detector*, queries all four trees and flags a
duplicate when **at least two** hashes agree within threshold. That cross-corroboration is what
suppresses each individual hash's false positives.

The weakness we attack is in the *voting*: each hash is only discriminative for a subset of
manipulations, but **every hash votes on every image anyway**. `hsvHash` on a background-colour
change is pure noise, yet it still gets a vote. The paper's own future-work section points at
its spatial/geometric blind spots.

## What we found

Three findings drive the design. All three are measured; details and evidence in
[PROGRESS.md](PROGRESS.md).

**1. sHash cannot be correctly indexed in a BK-tree** *(PROGRESS.md §2)*
A BK-tree requires a true metric. sHash produces a *variable-length list* of per-segment hashes
compared by a directional mean-of-minimums — which is **asymmetric** and **violates the triangle
inequality**. A composite image can act as a "bridge" that makes two unrelated images look
connected, and pruning can silently discard a real match (a false negative — the worst error class
here). On the authors' own reference CSV, the `original→copy` direction reproduces their published
distance on **1,802/1,802** rows while `copy→original` matches only **240** — the asymmetry is not
hypothetical. Raised with the authors; **tentatively confirmed by Arad, not yet formally resolved.**

**2. ORB beats sHash at sHash's own job** *(PROGRESS.md §5)*
sHash exists for crop-resistance, and it is precisely the signal that can't be soundly indexed —
so it is the natural thing to replace. On the geometric subset, ORB scores **F1 90.6%** against
sHash's **best-case** 76.4%, *while sHash was handed an oracle threshold ORB never got*. Held to
equal precision, sHash manages **27.2% recall against ORB's 90.0%**. Notably, ORB turns out to be
a **structure** specialist rather than a narrowly geometric one: it survives everything except the
destruction of high-frequency detail (pixelation).

**3. A feature-matching signal cannot ride in transaction metadata** *(PROGRESS.md §6)*
The paper's premise is that detection is "fully self-contained within the blockchain": hashes ride
in a 300–500 byte transaction. That holds for the four hashes (24–32 bytes). It **breaks** for ORB,
which emits ~455 descriptors ≈ **14.5 KB — 36× an entire transaction**. We measured the whole
accuracy-vs-bytes curve; the best usable point is still ~10× a transaction. This is an
**architectural** boundary, not a tuning problem, and we report it as a finding rather than
omitting it.

## Architecture

```
query image
  │
  ├─ 93 absolute single-image features ─► RandomForest ─► P(manipulation class)
  │                                                         │
  │                                                         └─► soft reliability mass:
  │                                                              P(detail broken)   → distrust ORB
  │                                                              P(colour changed)  → distrust hsvHash
  │
  └─ signals:  aHash │ pHash │ hsvHash │ ORB
                  └────────► ≥2 agree ⇒ duplicate  (the paper's rule, with routed thresholds;
                                                    static fallback when the router is unsure)
```

**The router is for accuracy.** It predicts the manipulation from the query, then drops or
discounts signals that manipulation is known to break. When it isn't confident, it falls back to
the paper's static thresholds — so the routed detector should never be *worse* than the paper's.

**Reliability is derived from soft probabilities, not a hard label.** The router outputs a
probability *vector* over 8 classes; reliability is read as probability mass (e.g.
`P(detail broken) = P(pixelated)`), so a router torn between two classes discounts a signal
*partially* rather than flipping it on a coin-toss.

**Which hash each flag gates is measured, not assumed** (`python/router/hash_reliability.py`).
It is easy to guess wrong here: pixelation destroys ORB, but aHash/pHash *coarsen* the image in
much the same way pixelation does and survive it — so distrusting them on pixelation would switch
off the very signals that still work.

**The architectural asymmetry is the point.** The paper's design silently assumes every signal is
one binary string in a BK-tree. ORB emits a *set* of binary descriptors, so it needs **LSH**, not a
BK-tree. (A KD-tree would imply SIFT's 128-D float descriptors, and KD-trees degrade toward linear
scan at that dimensionality — hence FLANN's randomized KD-*forests*.) **The descriptor type
dictates the index structure**, and that is the real cost of closing the geometric blind spot.

## The C11 hash suite — a completed result

Before the project pivoted to Python, all four paper hashes (plus dHash, a prerequisite) were
reimplemented from scratch in C11 and validated to be **bit-exact** against Johannes Buchner's
[`imagehash`](https://github.com/JohannesBuchner/imagehash) — the library that produced the paper's
own published numbers.

| Hash | Size | Parity | Notes |
|------|------|--------|-------|
| aHash | 64-bit | bit-tolerant | 8×8 grayscale, mean threshold |
| pHash | 64-bit | bit-tolerant | 32×32 → DCT low-freq 8×8, median threshold |
| hsvHash | 42-bit | **bit-exact** | port of `colorhash()`; global HSV histogram |
| dHash | 64-bit | **bit-exact** | 9×8 horizontal gradient; sHash's per-segment hash |
| sHash | list | **bit-exact** | port of `crop_resistant_hash()` |

Validated on 600 distinct images (including all 402 of the authors' own reference images and
synthetic edge cases); for sHash this matched the **full ordered segment list**, not a summary.
`shash_paper_distance` reproduced **all 1,802 rows** of the authors' reference CSV. ASan + UBSan
clean. Two things made this hard, both documented in [PROGRESS.md](PROGRESS.md) §1: Pillow's
**float/double literal trap** (its locals are `float` but its literals are `double`, so the
arithmetic silently runs at double precision), and resolving ambiguities the paper's prose left
open by reading the library's actual C source rather than trusting its description.

**This work is preserved, not extended.** A C11-vs-Python speed comparison isn't a defensible claim
for a formal write-up, and duplicating logic in two languages before the idea is proven is wasted
effort — so the project is **Python-only from here**. The C11 suite stands as a genuine validated
result, and its sHash port is what produced Finding #1. Per-hash design notes live in each
`src/hashes/<name>/README.md`.

## Toolchain

Python only. `imagehash` is the reference implementation the paper itself used, so we use it
directly rather than maintaining a second reimplementation.

- **The `colorhash()` correction.** The paper's §II-C(v) *describes* hsvHash as a block/region
  statistical hash citing Tang et al. 2013 — but `colorhash()` is actually a **global** histogram
  (fractions of black/gray/hue-binned-saturated pixels across the whole image), a different
  algorithm entirely. Since the paper's published numbers were produced by whatever the library
  actually does, we port `colorhash()`'s real algorithm, not the prose description.
- **Pinned versions** (`training/requirements.txt`) — a future default-filter change in Pillow or
  numpy would silently shift what "matching the paper" even means. `scikit-learn` is **router-only
  and never in the hash path**, so it cannot touch any bit-exactness claim.

| Concern | Choice |
|---|---|
| Language | Python 3.12 (`training/.venv`) |
| Hash values | Buchner's `imagehash` (our C11 port is bit-exact to it, so values are identical either way) |
| Geometric signal | OpenCV ORB → BFMatcher(Hamming, crossCheck) → RANSAC homography inlier count |
| Router | scikit-learn `RandomForestClassifier` → `predict_proba` (the paper's model family) |
| Index (discussion only) | ORB ⇒ LSH; BK-tree ⇒ the fixed-width hashes. Not on the evaluation path — we score **pairwise**. |

**Why pairwise, not retrieval.** We compare `original_image` against `copy_image` directly. This
matches the dataset's own schema (**`is_copy` labels a *pair*, not an image**), the paper's
methodology, and how the four hashes are evaluated — and it removes the gallery from the accuracy
path entirely, which is what invalidated an earlier baseline (PROGRESS.md §4). The LSH gallery
survives as a *scalability* discussion, not an accuracy claim.

## Repository structure

```
.
├── PROGRESS.md          # The project's history, findings and numbers (report material)
├── README.md            # This file: the current architecture
├── src/hashes/          # C11 hash suite (complete, bit-exact, preserved — not extended)
│   ├── common/          #   shared Pillow op ports + box-filter downsample
│   ├── ahash/  phash/   #   64-bit
│   ├── hsvhash/         #   42-bit, bit-exact colorhash
│   ├── dhash/           #   64-bit; sHash's per-segment hash_func
│   └── shash/           #   segment list + the paper's mean-of-mins distance
├── python/
│   ├── geometric/       # The ORB signal that replaces sHash
│   │   ├── orb_match.py         #   pairwise ORB→RANSAC inlier score (the signal)
│   │   ├── score_dataset.py     #   cached expensive scoring pass → data/<split>/orb_scores.csv
│   │   ├── tune.py              #   threshold tuning (geometric subset vs global)
│   │   ├── evaluate.py          #   per-category / per-collection metrics
│   │   ├── shash_baseline.py    #   sHash scores, for the swap comparison
│   │   ├── compare_shash.py     #   ORB vs sHash — the evidence for the swap
│   │   ├── descriptor_budget.py #   accuracy-vs-bytes curve (--budgets: live demo knob)
│   │   ├── verify_baseline.py   #   proof the old baseline measured gallery membership
│   │   └── original/            #   teammate's initial pipeline, imported as-is (reference)
│   └── router/          # The router: predicted manipulation → signal reliability
│       ├── features.py          #   93 absolute single-image descriptors
│       ├── extract_features.py  #   cached pass → data/<split>/router_features.csv
│       ├── train_router.py      #   RandomForest → class probabilities + confidence
│       ├── evaluate_router.py   #   confusion matrix, reliability metrics, per-collection
│       └── hash_reliability.py  #   measures which hash each manipulation actually breaks
├── training/            # Dataset generation (see Dataset below)
├── data/                # Gitignored except data/example/
└── third_party/         # stb_image.h (C11 PNG decode)
```

> `src/bench/`, `src/bktree/`, `src/features/` and `src/router/` are empty `.gitkeep` placeholders
> left from the original C11 plan (a codegen'd, zero-allocation router). That plan is abandoned;
> the directories are vestigial and can be removed.

## Dataset

Three sources feed `data/` (all gitignored except `data/example/`):

- **[`shaunmak/nft-classifier`](https://www.kaggle.com/datasets/shaunmak/nft-classifier)** (Kaggle)
  — 3,000 real images across three collections (Azuki, BAYC, CryptoPunks), 1,000 each. This
  balanced set populates `data/raw/`, flattened as `azuki_#<id>.png` / `bayc_#<id>.png` /
  `cp_#<id>.png`. `data/reference/nft_classifier.csv` is the only thing mapping each numeric id to
  its collection, kept as the record of where the naming came from.
- **[`tunguz/cryptopunks`](https://www.kaggle.com/datasets/tunguz/cryptopunks)** (Kaggle) — 10,000
  CryptoPunk sprites; the exact dataset the paper cites for its Table V evaluation. Held in
  `data/extra/`, **deliberately out of `raw/`**: at 10,000 against 1,000 each of the others, it
  would let punk-specific patterns dominate the router's classifier rather than what actually
  discriminates a manipulation. Kept for later scale/stress testing.
- **`data/reference/test_manipulations/`** — obtained **directly from the paper's authors**, not a
  public set: 405 manipulated CryptoPunk images plus a 1,802-row CSV of
  `(original, copy, is_copy, manipulation)` pairs with the authors' own precomputed hashes and
  distances. It doubles as a differential-test fixture and as a **cross-generator check** for the
  router (see below). **Shared informally — it stays local, is not redistributed, and no published
  result may rest on it without checking with the authors first.**

**`data/example/` is the one exception to "all of `data/` is gitignored"** — a tiny, git-tracked
demo (public Kaggle images only) running the full pipeline end-to-end on 2 images, so the layout
and output schema are visible on GitHub without shipping the full corpus.

> **Local storage note.** The bulk image directories (`data/{raw,extra,train/images,test/images}`)
> are large and regenerable, so on the development machine they are relocated off the root disk to
> a separate drive and symlinked back in place — currently `/media/ra/Data/nft-dup-data/`. Every
> path resolves transparently through the symlinks, and nothing tracked in git depends on them.
> **If you run this repo on a machine without that drive, those symlinks dangle** — just re-run
> `training/generate_dataset.py` to rebuild the images locally.

### Labeled data (`training/generate_dataset.py`)

`data/raw/`'s images carry no manipulation labels, so we generate them: for every base image, apply
each of the paper's OpenSea-copymint categories — flip/rotate/mirror, crop/resize/reposition,
text/logo/emoji overlay, background-colour change, pixelation, colour swap/saturate, plus an
exact-copy control — then downscale to `--max-dim` (default 256px, aspect-preserving) and write the
images plus a `metadata.csv` (`original_image,copy_image,manipulation_type,is_copy`) per split.

Splitting happens **by base image before any manipulation is applied**, so an image's variants never
cross the train/test boundary. Non-duplicate (negative) pairs are generated too, matching the
paper's methodology.

Current corpus (default flags): **train** 2,400 base images → 48,000 images / 55,200 rows;
**test** 600 base → 12,000 images / 13,800 rows.

```bash
python3 -m venv training/.venv && training/.venv/bin/pip install -r training/requirements.txt
training/.venv/bin/python training/generate_dataset.py
```

**Validity caveat.** Our manipulations are PIL-generated, so any forensic trace the router learns
(e.g. histogram "combing" — the missing-bin artifacts integer quantisation leaves after a
brightness edit) is partly **generator-specific**. A rich classifier could learn *how our dataset
was made* rather than how real copymints behave. Mitigation: the router is also evaluated against
the authors' `test_manipulations/` set, a different generator.

## Roadmap

| Phase | Status |
|---|---|
| Reimplement the paper's hashes in C11, bit-exact | ✅ done (preserved, not extended) |
| **A** — Document the C11 work, the sHash finding, and the pivot | ✅ done |
| **B** — ORB pipeline replacing sHash: pairwise signal, tuning, per-category eval, sHash comparison, descriptor-budget curve | ✅ done |
| **C** — The router: 93 absolute features → RandomForest → manipulation + soft reliability | ✅ done (PROGRESS §8) |
| **D** — The routed detector: dynamic thresholds over {aHash, pHash, hsvHash, ORB}, ≥2-agree, static fallback | ✅ done (PROGRESS §9) |
| Deferred | sHash's index structure (awaiting the authors' reply → a documented finding) |

Phase D's deliverable is a **three-way comparison** that isolates each contribution:
1. static 4-hash **with sHash** — the paper's baseline,
2. static **with ORB** replacing sHash — isolates the *swap's* gain,
3. **router-driven** dynamic thresholds + ORB — isolates the *router's* gain.

## Reference

A. Kotzer, P. Reviriego, J. Conde Diaz, O. Rottenstreich, *"Combating NFT Copymints in Blockchain
Networks: An Image Hashing Approach."*
