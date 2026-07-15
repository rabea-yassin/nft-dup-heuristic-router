# Project progress log

A running record of what we built, what we found, and why the project changed direction.
Written to be the raw material for the final project report — including the dead ends, since
one of them turned into our main finding.

For the current architecture see [README.md](README.md); this file is the *history*.

---

## 1. Roadmap step 2: the four paper hashes, in C11, bit-exact

The paper (Kotzer et al., *"Combating NFT Copymints in Blockchain Networks: An Image Hashing
Approach"*) detects copymints with four perceptual hashes — aHash, pHash, hsvHash, sHash —
each in its own BK-tree, flagging a duplicate when **at least two** agree within threshold
(the *2-Minimal Distance Detector*). We reimplemented all four in C11, plus dHash as a
prerequisite.

**All five are bit-exact against Buchner's `imagehash`** — not merely "close", but identical
output on every image tested.

| Hash | Size | Parity | Notes |
|------|------|--------|-------|
| aHash | 64-bit | bit-tolerant | 8×8 grayscale, mean threshold |
| pHash | 64-bit | bit-tolerant | 32×32 → DCT low-freq 8×8, median threshold |
| hsvHash | 42-bit | **bit-exact** | port of `colorhash()`; global HSV histogram |
| dHash | 64-bit | **bit-exact** | 9×8, horizontal gradient; sHash's per-segment hash |
| sHash | list | **bit-exact** | port of `crop_resistant_hash()` |

**Validation:** 600 distinct images matched exactly (40 example fixtures, all 402 of the
authors' own `test_manipulations` images, 100 random 2000×2000 raw NFTs, 40 CryptoPunk
sprites, 18 synthetic edge cases including a 99-segment image and the zero-segment fallback).
For sHash this matched the **full ordered segment list**, not just a summary. Additionally,
`shash_paper_distance` reproduced **all 1,802 rows** of the authors' reference CSV exactly.
ASan + UBSan clean.

### Two things that made bit-exactness hard

**(a) Pillow's float/double literal trap.** PIL's `rgb2hsv_row` (`src/libImaging/Convert.c`)
declares its locals as `float`, but its literals (`2.0`, `6.0`, `255.0`) have no `f` suffix and
are therefore `double`. So most of the arithmetic actually runs at *double* precision and only
rounds down to float at each assignment. A natural all-`float` port is wrong on ~0.4% of pixels
at rounding boundaries. Reproducing the assignment-by-assignment rounding is what made it exact.
The same trap recurred in Pillow's Gaussian-radius math (`_gaussian_blur_radius`).

**Lesson:** for bit-exact parity, a library's *variable types* are not its *arithmetic
precision*. We only found this by downloading Pillow's actual C source rather than reasoning
from the Python API.

**(b) Resolution ambiguities the paper's prose didn't settle.** The paper's Section II-C(v)
*describes* hsvHash as a block/region-based hash citing Tang et al. 2013 — but `colorhash()`,
the function that actually produced the paper's numbers, is a **global** histogram with no
spatial blocks at all. We ported what the library does, not what the prose says, because the
published numbers came from the library.

Similarly, we could not tell from the paper whether sHash used `crop_resistant_hash`'s default
parameters. We resolved it empirically: recomputing the authors' own CSV hash strings with the
defaults reproduced them exactly, so the defaults are confirmed.

---

## 2. The main finding: sHash cannot be correctly indexed in a BK-tree

This started as an implementation detail and became the most interesting result of the project.

### The problem

A BK-tree only works if its distance is a true **metric**. The paper states this itself
(Section V): a distance is "valid for a BK-tree use only if it complies with" the metric
conditions — notably **symmetry** and the **triangle inequality**. The tree prunes whole
branches using those guarantees; without them, pruning can silently discard a true match.

aHash/pHash/hsvHash/dHash are fixed-width binary strings compared by Hamming distance — a
proper metric. **sHash is not.** It produces a *variable-length list* of per-segment dHashes,
and its distance is directional: *for each segment of the source, take the minimum Hamming
distance into the target, then average*. That breaks both required properties.

### Symmetry fails

Image P1 has 3 segments, P2 has 4. P1's three each match one of P2's well (≈2 apart), but P2's
fourth segment matches nothing in P1 (≈34).

- P1 → P2 = (2 + 2 + 2) / 3 = **2**
- P2 → P1 = (2 + 2 + 2 + 34) / 4 = **10**

Same pair, two different distances. An extra/unmatched segment only costs you when it is on the
*source* side, because only the source's segments are forced to find a match.

*This is not hypothetical:* on the authors' own CSV, the original→copy direction matches their
reported `sHash_dist` on **1,802 / 1,802** rows, while copy→original matches only **240 / 1,802**.

### The triangle inequality fails

Let A = {🐱}, C = {🐶}, and B = {🐱, 🐶} — a composite containing both. With cats ≈2 apart,
dogs ≈2 apart, and cat–dog ≈30:

- d(A, B) = **2**  (A's cat finds B's cat)
- d(B, C) = (30 + 2) / 2 = **16**  (B's dog matches; B's cat has nothing to match)
- d(A, C) = **30**  (A's cat vs C's dog)

The inequality requires d(A,C) ≤ d(A,B) + d(B,C), i.e. 30 ≤ 18 — **false**. A composite image
acts as a "bridge" making two unrelated images look connected, which a real metric cannot do.

**Why this is dangerous, concretely:** query q = {🐱} with a genuine duplicate X = {🐱} stored
under a composite root B. `d(q,X) = 2` — a dead-on match. But B's extra dog segment inflates the
stored edge to `d(B,X) = 16`, while `d(q,B) = 2`. Searching within threshold t = 5, the tree only
descends into children whose edge lies in `[2−5, 2+5] = [−3, 7]`. X's edge is 16 → **pruned**.
The tree reports "no duplicate found" and misses a real copymint — a **false negative**, the
worst error class for this system.

### What the authors said

We raised this with the authors. Arad's reply:

> "I need to look at the code deeper to provide you an answer (as I did not touch this code for
> more than a year). Though, I do not think I noticed sHash does not hold these two properties.
> If so, this might explain how the tree is not 100% correct and why the results are not perfect.
> I'll look at it this week and let you know if this is indeed the case, but I do think this is
> might what happened."

So this is **tentatively confirmed by an author and not yet formally resolved**: the paper's
sHash BK-tree is likely unsound, which would help explain its imperfect reported results.

---

## 3. Why the project changed direction

Three things landed at once.

1. **The speed thesis lost its foundation.** The original plan was a confidence-gated router
   making the paper's 4-hash query *cheaper* without changing its answer. But if sHash can't be
   soundly BK-tree indexed, "accelerate the four BK-trees" is built on sand.
2. **C11-vs-Python isn't a fair comparison.** Our lab instructor pointed out that benchmarking a
   C11 implementation against Python is not a defensible claim for a formal write-up. It's
   acceptable for a course project, but it isn't the contribution we thought it was — and
   duplicating every component in two languages *before the idea is proven* is wasted effort.
3. **A better target existed.** The paper's own future-work section points at its spatial and
   geometric blind spots. Feature-matching approaches (SIFT/ORB) are rotation- and
   scale-invariant and attack exactly that gap.

### The resulting pivot

- **Python-only from here.** The C11 hashes stay in the repo (`src/hashes/`, all bit-exact,
  documented per-hash) but are not extended. They remain a genuine result — a validated,
  from-scratch reimplementation of the paper's hash suite — just not the project's thesis.
- **The router survives, re-aimed at accuracy instead of speed.** The paper's static detector
  lets *every* hash vote at fixed thresholds, including hashes that are known-broken for a given
  manipulation (hsvHash on a background-colour change is noise). A router that predicts the
  manipulation and then ignores unreliable signals — or tightens thresholds where a signal *is*
  reliable — should beat the static rule on **accuracy**. Dropping the speed goal also drops the
  16-byte / zero-allocation / codegen constraints that were making the feature set too weak.
- **sHash is replaced by ORB.** This is the neat part: sHash's entire job was crop-resistance,
  and it is precisely the signal that cannot be soundly indexed. ORB covers the same geometric
  blind spot *and* has a sound index (LSH). Our sHash work is not wasted — **it is the evidence
  that justifies the swap.** sHash is retained as an evaluation baseline so the swap is measured,
  not asserted.

### An architectural consequence worth reporting

The paper's design silently assumes every signal is a binary string living in a BK-tree. A
feature-matching signal breaks that assumption: ORB emits a *set* of binary keypoint descriptors,
not one string, so it needs **LSH**, not a BK-tree. (A KD-tree would imply SIFT's 128-D float
descriptors — and KD-trees degrade toward linear scan at that dimensionality, which is why FLANN
uses randomized KD-*forests*.) **The descriptor type dictates the index structure**, and that is
the real architectural cost of closing the geometric blind spot.

---

## 4. Notes on the geometric (ORB) work

An initial ORB pipeline (ORB → LSH voting → RANSAC geometric verification, plus a "mirror hack"
since ORB is rotation-invariant but *not* mirror-invariant) reported F1 73.6% / accuracy 60.7%.

**Those numbers are retired, for two structural reasons rather than model quality:**

1. **The negatives were unwinnable.** Every `non_duplicate` row's `copy_image` is an *original*
   image, and the gallery being queried contained that same original. Querying an image against a
   gallery that contains it yields a perfect self-match → flagged duplicate → but labelled
   `is_copy=0` → **guaranteed false positive**. The root cause is a schema misreading: **`is_copy`
   labels a *pair*, not an image.** The dataset is pairwise (matching the paper's methodology),
   but the pipeline was retrieval-shaped and reused pairwise labels as per-image labels.
2. **ORB was asked to do a job it can't.** The vote threshold was detuned 15 → 1 to chase
   pixelated and colour-swapped images — manipulations ORB fundamentally cannot detect (pixelation
   destroys keypoints; colour-swap is invisible in grayscale). Tuning optimised a single global F1
   across a dataset dominated by non-geometric categories, forcing a bad compromise.

**Lesson, and the design principle going forward:** ORB must be a **geometric specialist**
supplementing the other hashes, not an all-manipulations detector. The other signals already
cover pixelation/colour/text; ORB owns crop/rotate/scale/reposition. Evaluation is therefore
**pairwise** (comparable to the hashes and to the paper), and thresholds are tuned on the
**geometric subset only**, with per-category metrics reported rather than one global F1.

---

## 5. Known validity caveat

Our manipulations are generated with PIL, so any forensic traces the router learns (e.g.
histogram "combing" — the missing-bin artifacts integer quantisation leaves behind after a
brightness or saturation edit) are partly **generator-specific**. A sufficiently rich classifier
may learn *how our dataset was made* rather than how real copymints behave.

Mitigation: also evaluate against the authors' `data/reference/test_manipulations/` set (405 real
manipulated images plus their metadata CSV), which we hold and have already validated against.
This set was shared with us informally, so it stays local and is not redistributed.
