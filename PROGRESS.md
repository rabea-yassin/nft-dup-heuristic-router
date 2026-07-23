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

## 4. The geometric (ORB) baseline, and why its numbers were retired

Ahmad's ORB pipeline (ORB → LSH voting → RANSAC geometric verification, plus a "mirror hack"
since ORB is rotation-invariant but *not* mirror-invariant) reported **F1 73.6%, precision 82.7%,
recall 66.3%, accuracy 60.7%**. Before building on it we tried to verify it, and the numbers
turned out to measure something other than what they appear to.

### The gallery was missing an entire collection — and *why* is the real finding

The gallery index (`build/image_map.pkl`) indexes **2,000 of the 3,000** originals in `data/raw/`:
1,000 azuki + 1,000 bayc. **All 1,000 CryptoPunk (`cp_`) originals are absent.**

The cause is not a stale file — rebuilding the index changes nothing. **ORB physically cannot
describe a CryptoPunk.** Punks are natively **24×24 px**, and `generate_dataset.py`'s downscale
only ever *shrinks* (`if max(w, h) <= max_dim: return`), so they stay 24×24 while azuki/bayc land
at 256×256. ORB's default `patchSize` and `edgeThreshold` are both **31 px — larger than the
entire image** — so `detectAndCompute` returns **zero descriptors for 100% of punks** (measured:
150/150). The indexer then silently drops them:

```python
_, descriptors = orb.detectAndCompute(img, None)
if descriptors is not None:      # <- every punk fails this test, silently
    image_filenames[img_id] = os.path.basename(filepath)
```

So an entire collection vanished from the gallery without a single warning. Once gone, the
missing collection predetermines the verdict for *every* row:

| Row | What happens | Result |
|-----|--------------|--------|
| positive, copy of a **CryptoPunk** | original not indexed → nothing to match | forced **FN** |
| positive, copy of an **azuki/bayc** | original indexed → ORB finds it | **TP** |
| negative, an unrelated **azuki/bayc** | that exact image *is* in the gallery → finds itself | forced **FP** |
| negative, an unrelated **CryptoPunk** | not indexed, and pixel-art punks resemble nothing in the gallery | **TN** by luck |

The verdict is decided by **which collection an image belongs to**, not by whether it is a duplicate.

### The proof: the confusion matrix reproduces from set membership alone

`python/geometric/verify_baseline.py` predicts the published confusion matrix using nothing but
"is this filename in the gallery?" — pure set arithmetic, **no OpenCV, no image decoding**:

| cell | reported | predicted from membership |
|------|---------:|--------------------------:|
| TP | 30,210 | **30,210** |
| FN | 15,390 | **15,390** |
| FP | 6,306 | **6,306** |
| TN | 3,294 | **3,294** |

Four of four exact; precision/recall/accuracy match to two decimals. A second tell corroborates
it: the threshold grid search is **flat** (F1 73.58 → 73.58 → 73.58 → 73.57 → 73.52 across every
combination). Thresholds should dominate such a system. They are **inert**, because the ORB score
was never deciding anything.

### What this actually tells us — the good news

- The reported figures say **nothing** about ORB's quality; they are an artifact of a stale index.
- But **ORB caught 100% of the positives it could possibly catch** — every findable duplicate,
  zero misses among them. That is real evidence the pipeline *works*; it was simply never given a
  fair test.
- Note this is **not** overfitting, and not a consequence of which dataset was trained on: only two
  hyperparameters were tuned, and they were inert anyway. It is a data-preparation bug.

### Three design lessons carried forward

1. **ORB imposes a minimum resolution on the corpus — the hashes do not.** This is a genuine
   architectural cost of adopting a feature-matching signal, and it is easy to miss because it
   fails *silently*. aHash/pHash/hsvHash resize internally to 8×8/32×32 and work at any input
   size; ORB needs real spatial extent (31 px patch) and simply returns nothing below it. The fix
   is a normalisation step: scale every image to a common working edge (256 px) before ORB, using
   **NEAREST** when magnifying — pixel art's hard edges *are* the corners ORB keys on, and
   interpolating them away measurably costs keypoints (NEAREST 225 kp vs LANCZOS 186; as-is 0).
   With normalisation, punk exact-copies score 137–235 inliers instead of 0.
   *Caveat to carry:* upscaling cannot create information. Punks remain the weakest case — their
   geometric scores (crop ≈22–51, flip ≈9–66) overlap their non-duplicate scores (≈8–62), so ORB
   is far less discriminative on 24×24 pixel art than on 256 px artwork. Worth reporting, and
   notable because CryptoPunks is the very collection the paper used for its Table V evaluation.
2. **Go pairwise.** Comparing `original_image` vs `copy_image` directly removes the gallery from
   the accuracy path entirely — no gallery, no membership artifact, and this whole class of bug
   becomes inexpressible. It also matches the dataset's own schema (**`is_copy` labels a *pair*,
   not an image**), the paper's methodology, and how our four hashes are evaluated, so ORB becomes
   directly comparable to them. The gallery/LSH survives only for the scalability discussion.
3. **ORB must be a geometric specialist.** The vote threshold had been detuned 15 → 1 to chase
   pixelated and colour-swapped images — manipulations ORB fundamentally cannot detect (measured:
   pixelated punks score 0, pixelated azuki/bayc score 10–29, against non-duplicates at ~10) —
   while a single global F1 was optimised across a dataset dominated by non-geometric categories.
   The other signals already cover pixelation/colour/text; ORB owns crop/rotate/scale/reposition.
   So thresholds are tuned on the **geometric subset only**, and metrics are reported **per
   category**, never as one global F1.

---

## 5. The swap, measured: ORB beats sHash on sHash's own job

Section 4 retired the *old* baseline. This section is the new, honest measurement
that justifies replacing sHash with ORB — done **pairwise** (no gallery, per
lesson 2 above) on the manipulations sHash exists for: `flip_rotate_mirror` and
`resize_crop_reposition`, plus the `non_duplicate` negatives the signal must
reject. Test split: 3,600 geometric positives, 2,400 negatives.

**The comparison is deliberately rigged against ORB.** ORB uses the threshold
tuned on the *train* split (`t=16`), the honest protocol. sHash is given its
*best-case* threshold, swept on the *test* set itself — an oracle advantage no
real deployment gets. So sHash's numbers are an upper bound and ORB's are real.

| signal | threshold | precision | recall | F1 |
|---|---|---|---|---|
| sHash (best-case, oracle) | dist ≤ 28 | 66.5% | 89.8% | 76.4% |
| **ORB** (honest, train-tuned) | inliers > 16 | **91.2%** | **90.0%** | **90.6%** |

**+14.2 F1 to ORB, with sHash holding the oracle advantage.**

### The number that looks like a tie, and isn't

sHash's 89.8% recall looks competitive, and its per-category detection is nearly
identical to ORB's (flip 80.9% vs 81.7%; crop 98.7% vs 98.3%). Both are
artifacts of the same thing: **sHash buys recall by flagging almost everything.**
At dist ≤ 28 it flags **1,630 of 2,400 non-duplicates (68%)**. The decisive cut
is to hold precision equal and compare recall:

> At ORB's precision (91.2%), sHash reaches only **27.2% recall** vs ORB's
> **90.0%** — a **3.3× gap**. sHash is dominated across its entire operating
> curve; there is no threshold at which it is the better choice.

**A sharper way to say it:** at this 60/40 base rate the trivial "flag
everything" classifier scores **F1 75.0%**. sHash's oracle best is 76.4% — it
beats a coin-that-always-says-yes by 1.4 points. It is a signal that barely
functions on the job it was designed for, which is exactly why its precision
(66.5%) sits so near the 60% base rate. This is the evidence for the swap.

### Refinement of §4 lesson 3: ORB is a *structure* specialist, not a *geometric* one

§4 framed ORB as owning "crop/rotate/scale/reposition". The full test-split
per-category detection (at `t=16`) shows that was too narrow:

| category | detected | §4's filing |
|---|---|---|
| exact_copy (control) | 100.0% | — |
| resize_crop_reposition | 98.3% | ORB's job ✓ |
| flip_rotate_mirror | 81.7% | ORB's job ✓ |
| text_logo_emoji | **100.0%** | *not* ORB's job |
| background_color_change | **98.9%** | *not* ORB's job |
| color_swap_modify_saturate | 79.8% | not ORB's job |
| pixelated | **28.5%** | not ORB's job |

Two "not ORB's job" categories score ~100%. Recolouring a background barely moves
the grayscale structure ORB reads; a logo overlay leaves most of the image
matchable. **ORB works wherever spatial structure survives and fails in exactly
one place — where high-frequency detail is destroyed** (pixelation kills the
intensity gradients corner detection needs; colour-swap is a partial case). This
also explains why tuning ORB on the global category mix instead of the geometric
subset costs only **1.4 F1** (t=13 → 88.7 vs t=16 → 90.1): only ~1 category in 6
truly defeats ORB. **Design consequence for the router (§below):** the signal to
predict is *"is detail intact?"*, not *"is the manipulation geometric?"*.

### The honest limitation: CryptoPunks

At a clean 9.9% false-positive rate, ORB detects **68.5%** of geometric punk
copies vs **100%** for azuki/bayc. This is the §4-lesson-1 caveat confirmed on the
full set: punks are natively 24×24, and upscaling to 256px recovers keypoints but
cannot create information that was never captured. It is an argument **for** the
router, not against ORB — no single signal dominates everywhere, so a router that
recognises a low-resolution punk can lean on the other hashes instead.

### Resolved: `flip_rotate_mirror` conflates two different attacks

`flip_rotate_mirror` at 81.7% looked oddly weak next to crop's 98.3%, despite ORB's mirror
handling being explicit. It is now diagnosed, and the cause is our **dataset**, not ORB.

`training/manipulations.py` applies an optional 50/50 h/v mirror **and then always a
continuous rotation** (`rng.uniform(1, 359)`, expand → transparent corner fill → resize back).
The angle is never 0, so **the class contains no pure mirrors at all**: every sample is a
compound *mirror ∘ rotate ∘ resize*. Scoring the components separately (120 test originals,
40 per collection, ORB at `t=16`):

| transform | detected | median inliers | azuki | bayc | **cp** |
|---|---|---|---|---|---|
| **pure mirror** | **100.0%** | **452** | 100% | 100% | **100%** |
| rotate + resize only | 79.2% | 166 | 100% | 100% | **37.5%** |
| our `flip_rotate_mirror` | **81.7%** | 162 | 100% | 100% | 45.0% |

*Reproduce with `python/geometric/diagnose_flip.py --split test`.*

**ORB handles mirrors perfectly** — 453 median inliers, essentially an exact-copy match. The
mirror path was never broken. The whole 81.7% deficit is **CryptoPunks under rotation**
(37.5%): bicubic rotation plus a LANCZOS resize-back destroys exactly the hard pixel-art edges
ORB keys on, and a 24×24 punk has no detail to spare. azuki/bayc are 100% on all three.

Two consequences, and the second is a genuine finding:

1. **Our "flip" numbers across this whole project describe *rotate+resize*, not mirroring.**
   Read `flip_rotate_mirror = 81.7%` as understating ORB on mirrors (100%) and overstating it
   on punk rotations (37.5%). The same caveat applies to every per-category flip figure,
   including §8.3's.
2. **We collapsed a distinction the paper's own data makes.** The authors' reference set keeps
   these as *separate* categories — `rotation` vs `left_to_right` / `top_to_bottom` — while our
   generator fuses them into one label. That is a dataset-design error on our side: the two are
   different attacks with different difficulty (100% vs 37.5% on punks) and, as §8.4 shows,
   different detectability. A mirror leaves no forensic trace at all; a rotation leaves
   transparent corners. Merging them hides both facts.

---

## 6. The on-chain storage elephant: a feature-matching signal can't ride in metadata

The paper's central premise (Sec. I) is that detection is *"fully self-contained
within the blockchain"*: the hashes are stored in the NFT transaction — which it
states is **300–500 bytes** — and *"each hash value needs several bytes"*, so the
overhead is *"low ... practical and easy to implement"* (Sec. VI). That claim is
fair for the four hashes. **It breaks for ORB**, and honesty requires reporting
this rather than omitting it.

Measured payload per image (60 real originals):

| signal | bytes/image | vs a 400-byte transaction |
|---|---|---|
| aHash + pHash + hsvHash | 24 (fixed) | +6% |
| sHash | 32 (median; 1–10 segments × 8B) | +8% |
| **ORB @ nfeatures=500** | **14,560** (455 descriptors × 32B) | **+3,640% — 36× the whole tx** |

On-chain, 14.5 KB is ~455 storage slots ≈ **9M gas for a single mint** — roughly a
third of an Ethereum block, against ~50–70k gas for an ordinary ERC-721 mint.

### `500` was never chosen — and it doesn't even bind

`nfeatures=500` is `cv2.ORB_create`'s default. The original pipeline used 500 in
its notebooks and 1000 in its scripts — the same parameter, two values, one repo —
and our unification to 500 was equally unargued. Worse, **the cap never binds**:
ORB finds ~455 keypoints at 256px, so 500 is a ceiling floating above the real
count. So we measured how few descriptors ORB actually needs — the
accuracy-vs-bytes curve, on the geometric subset, each budget given its own
best (oracle) threshold so the curve's *shape* is what's compared:

| `nfeatures` | actual | bytes | × tx | P | R | F1 | ΔF1 |
|---|---|---|---|---|---|---|---|
| 500 (today) | 455 | 14,560 | 36.4× | 90.8% | 90.8% | 90.8% | — |
| 256 | 242 | 7,760 | 19.4× | 94.2% | 86.1% | 89.9% | −0.9 |
| **128** | 122 | 3,888 | 9.7× | 96.1% | 80.2% | **87.5%** | −3.3 |
| 96 | 92 | 2,928 | 7.3× | 96.4% | 77.4% | 85.9% | −4.9 |
| 64 | 64 | 2,048 | 5.1× | 94.8% | 74.7% | 83.5% | −7.3 |
| 48 | 46 | 1,488 | 3.7× | 97.1% | 67.8% | 79.8% | −11.0 |
| 32 | 32 | 1,024 | 2.6× | 90.8% | 65.5% | 76.1% | −14.7 |
| *24* | *24* | *768* | *1.9×* | *60.1%* | *99.8%* | *75.0* | *−15.8* |
| *16* | *16* | *512* | *1.3×* | *60.5%* | *96.7%* | *74.4* | *−16.4* |

*Italic rows are degenerate: the best threshold collapsed to 0 ("flag
everything"). Reference points — trivial flag-all classifier = **75.0%** at this
base rate; sHash oracle = **76.4%** at **32 bytes**. The oracle protocol is
validated: the N=500 row (F1 90.8) matches the honest train-tuned number (90.6)
to +0.2, so the curve reads at face value.*

Readings:
- **N=500 is the worst point on the curve** — 73% more bytes than N=128 for nothing, since the cap doesn't bind. As payload falls, precision *rises* and recall erodes: ORB becomes a stricter, sparser matcher, which is a *good* property for one vote among four (lost recall is recoverable from the other hashes; lost precision is not).
- **N=128 is the sweet spot** — 3.7× smaller (14.6 → 3.9 KB) for 3.3 F1, still far above sHash (87.5 vs 76.4).
- **N=32 is the hard floor** — F1 76.1 ≈ sHash's 76.4, but at **32× the bytes**. Below it ORB is worse on *both* axes at once; by N=24 the threshold degenerates to 0 and the signal is dead.

### Conclusion: architectural, not parametric

Tuning cannot rescue the premise. The best usable point is still ~3.9 KB —
**~10× a whole transaction**. We reduce the violation from 36× to 10×; we do not
remove it. **A feature-matching signal cannot ride in transaction metadata at any
descriptor budget.** The escape routes are all structural, and each forfeits
something: aggregate the descriptor set into one fixed-size vector (VLAD / BoVW,
but that sacrifices the RANSAC geometry that makes ORB accurate); store off-chain
with an on-chain commitment (forfeits "self-contained"); or recompute descriptors
on demand from the asset. The last is the revealing one — it exposes *why* the
paper stores hashes on-chain at all: Sec. VI states it is so validators can check
*"in a reasonable time"* without fetching the image. On-chain hashes are a
**latency** optimisation, and ORB is too large to buy in at that price. That is a
genuine finding: the paper's premise has a measured boundary, and we located it.

---

## 7. Known caveats and methodology deviations

### 7.1 Our manipulations are PIL-generated

Any forensic trace the router learns (e.g. histogram "combing" — the missing-bin artifacts
integer quantisation leaves behind after a brightness or saturation edit) is partly
**generator-specific**. A sufficiently rich classifier may learn *how our dataset was made*
rather than how real copymints behave.

Mitigation: also evaluate against the authors' `data/reference/test_manipulations/` set (405 real
manipulated images plus their metadata CSV), which we hold and have already validated against.
This set was shared with us informally, so it stays local and is not redistributed.

**Update: this caveat was not hypothetical — §8.4 measures it, and it bites.**

### 7.2 Our train/test split inverts the paper's, deliberately

`generate_dataset.py --train-frac` defaults to **0.8** (80% train / 20% test), the conventional
ML split. The paper does the **opposite**: Section IV-D trains on 20% of DISC21 and tests on 80%.

The difference is justified by what "training" means in each case. The paper's training step only
tunes **a handful of scalar Hamming thresholds** per hash, which needs very little data — so
reserving 80% for test buys a statistically powerful evaluation of its published numbers. We train
an actual **RandomForest over 93 features across 8 classes**, which benefits from more examples
than a few scalars do. Hence more data in train, not test.

Configurable via `--train-frac` if the paper's exact split is ever wanted for comparability.

---

## 8. The router (Phase C): predicting signal reliability from one image

The router predicts, from the **query image alone**, which manipulation produced it — so the
detector can drop signals known-broken for that manipulation. "Alone" is the load-bearing
constraint: at inference we hold only the query, never a reference to diff against, so all 93
features are **absolute descriptors of one image**. A RandomForest (the paper's own model
family) maps them to the 8 dataset classes.

### 8.1 The router's real output is not the class label

Two things make the raw 8-way accuracy the wrong headline.

**First, two classes are provably indistinguishable.** A `non_duplicate` query is a pristine
real NFT; an `exact_copy` query is byte-identical to a real NFT. From one image, with no
reference, `exact_copy` ≡ `non_duplicate` ≡ a pure axis-aligned mirror. No feature set can
separate them, and **none needs to**: both mean "every signal is trustworthy". We kept all 8
labels and report the confusion rather than hiding it by merging.

**Second, what Phase D actually consumes is a reliability decision**, derived from the *soft*
class-probability vector rather than the hard argmax:

```
P(detail_broken)  = proba[pixelated]                                    -> distrust ORB
P(colour_changed) = proba[color_swap] + proba[background_color_change]  -> distrust hsvHash
```

### 8.2 Results on the held-out test split (11,991 images)

| decision | precision | recall | F1 | dangerous miss |
|---|---|---|---|---|
| **is detail broken?** (⇒ distrust ORB) | **100%** | **100%** | **100%** | **0.0%** |
| **did colour change?** (⇒ distrust hsvHash) | 98.1% | 97.0% | **97.5%** | 3.0% |

The dangerous error is calling a truly-pixelated image "detail intact", because that wrongly
trusts ORB — the one signal pixelation destroys. It never happens: `pixelated` has **zero**
leakage in the confusion matrix (1800/1800).

Raw multiclass accuracy is **89.2%**, and the gap is almost entirely the confusion we
predicted: `exact_copy` recall 20.2%, `non_duplicate` 14.2%, both dissolving into each other
and into `text_logo_emoji`/`crop`. That costs nothing, because **92.6% of pristine images still
receive a reliability-safe verdict** (a predicted class implying "trust every signal"). The
multiclass errors are between classes that share a reliability profile.

**The router is strongest exactly where ORB is weakest.** Detail-recall is **100% on all three
collections including the 24×24 CryptoPunks** — the collection where ORB drops to 68.5% because
upscaling cannot create information. Handcrafted forensics need no minimum spatial extent, so
the router recognises a low-resolution punk and can tell the detector to lean elsewhere. That
is the router thesis (§5) working on its hardest case.

Top features, in importance order, read as a description of the design: `unique_color_ratio`,
`flat_neighbour_frac` (pixelation collapses the palette), the alpha/transparent-corner group
(a non-axis rotation leaves transparent corners), `lap_var`, then `combing_*` (colour edits).

### 8.3 Measured, not assumed: what does gating a signal actually buy?

We had only ever scored ORB and sHash per category. Before Phase D hardcodes any gating,
`hash_reliability.py` scored the other three signals the same way. Two methodology points, both
learned the hard way:

**Thresholds are derived on TRAIN, never on test.** Our first pass picked each signal's
operating point on the very split it then reported — the same error that invalidated the
imported baseline (§4), committed by us this time. Corrected: `--split train` derives, `--split
test` applies. Each signal sits at a fixed **≤10% FP budget on the pristine negatives** rather
than at an F1 optimum, so all four are read at one comparable operating point (ORB's `t=16`
sits at ~13% FP). The honest protocol barely moved the numbers (aHash `≤8`→`≤7`) and **changed
no conclusion** — train and test agree within ~3 points on every cell, so the map is stable
rather than fitted.

**Detection alone cannot tell you whether dropping a signal helps.** So we measure two things.
Detection is over true copies; the false-positive column re-pairs each manipulated copy against
a deterministically-chosen *wrong* original, which is the only way to ask "when the query is a
category-X image, does this signal fire at the wrong thing?" (The dataset's own negatives are
all *pristine* NFTs, so they can only describe untouched queries — a limitation of our
generator that the authors' set does not share, since theirs contains manipulated negatives.)

Thresholds from train (`aHash≤7`, `pHash≤19`, `hsvHash≤3`), reported on test:

| category | aHash det → FP | pHash det → FP | hsvHash det → FP | ORB det |
|---|---|---|---|---|
| flip_rotate_mirror | **5.0%** → 0.4% | **2.8%** → 0.4% | 77.8% → 7.3% | 81.7% |
| resize_crop_reposition | 26.1% → 1.8% | 38.7% → 1.7% | 77.8% → 7.4% | 98.3% |
| exact_copy (control) | 100% → 9.0% | 100% → 11.3% | 100% → 11.3% | 100% |
| background_color_change | 60.7% → 7.7% | 82.7% → 7.5% | **16.1%** → 3.8% | 98.9% |
| color_swap_modify_saturate | 75.2% → 6.1% | 78.2% → 6.0% | **28.3%** → 6.7% | 79.8% |
| pixelated | 55.3% → 1.9% | **65.5%** → 2.7% | 79.3% → 8.2% | **28.5%** |
| text_logo_emoji | 93.4% → 5.0% | 96.7% → 5.7% | 93.1% → 7.8% | 100% |
| *pristine negatives* | *8.2%* | *8.1%* | *9.2%* | *13.0%* |

*The ORB column reproduces §5's table exactly, which validates the harness against work we did
not write. Read the flip row with §5's correction: it describes rotate+resize, not mirroring.*

**Finding 1 — pHash survives pixelation (65.5%) where ORB collapses (28.5%)**, and hsvHash is
the best signal there (79.3%). Pixelation is *approximately what pHash already does*: coarsen to
32×32 and keep low-frequency DCT. So the detail flag gates **ORB alone**. Gating pHash alongside
it — the intuitive move — would have discarded a better-performing signal. This holds train-side
(pHash 63.3% vs ORB 29.0%), which matters because it is the claim Phase D leans on hardest.

**Finding 2 — hsvHash goes quiet under colour edits** (16.1% / 28.3%), confirming that grouping.

**Finding 3 — aHash/pHash are destroyed by flips/rotations** (5.0% / 2.8%): they are not
rotation-invariant, while hsvHash (77.8%) is. This independently reproduces the paper's own
Table III claim.

**Finding 4 — and this one reframes what routing buys: every broken signal is SILENT, not
NOISY.** Look at the FP column. Where a signal's detection collapses, its false-positive rate
collapses *with* it — aHash on flips detects 5.0% and misfires 0.4%; hsvHash on background
changes detects 16.1% and misfires 3.8%, *below* its own 9.2% pristine baseline. A broken signal
here does not vote at random; it abstains.

That has a direct consequence the original design missed. The paper's rule flags a duplicate
when **≥2 signals agree**. Dropping a signal that is already silent cannot change that verdict —
it was contributing no votes to begin with. **So "drop the signals known-broken for this
manipulation" — the phrasing of our own pivot (§3) — is measurably close to a no-op.** The
value of routing must come from the *other* lever in that sentence: relaxing the quorum or the
thresholds among the signals that remain reliable. On flips, for instance, only hsvHash (77.8%)
and ORB (81.7%) work, so the ≥2 rule needs *both* to fire — roughly 0.64 joint probability, and
that, not the presence of two dead hashes, is what caps recall.

It also killed a hypothesis of ours: we expected pixelation to make aHash collide with
everything (a coarse blob matching any other blob). It does the opposite — FP 1.9%. Pixelation
moves a hash away from *everything*, including the wrong originals, which is exactly why it
stays usable at 55.3%.

The general lesson, twice over: **each signal's blind spot is a measurement, and the intuitive
design was wrong both times** — once about which signal pixelation breaks, once about whether
dropping a broken signal helps at all.

### 8.4 The §7 caveat, confirmed: the router partly learned our generator

Evaluated against the authors' own `test_manipulations` set (202 distinct manipulated punks, a
different generator), the router **degrades badly**: it predicts `pixelated` for nearly
everything, and the colour decision collapses to 0% recall at τ=0.5.

The cause is diagnosed, not guessed. Their punks are **pre-upscaled to 336–454 px RGB**; ours
are **native 24×24 RGBA**. Two of the router's top tells therefore vanish:

| feature | our rotated punk | their rotated punk |
|---|---|---|
| `corner_minus_centre_transparent` | 0.71 | **0.00** (RGB — no alpha at all) |
| `combing_r` | 0.69 | **0.00** (their pipeline smooths the histogram) |

Stripped of its rotation and colour tells, and fed the pixelation-like flatness that smooth
upscaling produces (`flat_neighbour_frac` 0.95), the model falls back on `pixelated`.

This is the §7 caveat made concrete: **a forensic feature set trained on one generator learns
that generator's conventions, including its resolution pipeline.** Two honest qualifications:
the failure is *fail-safe* for the detail decision (over-firing "distrust ORB" costs recall,
never correctness — the dangerous miss stays 0%), but it is a *real* failure for the colour
decision. And the test is deliberately the extreme of domain shift: a different generator, a
different resolution convention, RGB instead of RGBA, and punk-only.

It does not invalidate §8.2 — within a generator the reliability decisions are excellent — but
it bounds the claim: **the router's numbers are a within-distribution result, and cross-generator
robustness (resolution-invariant features, multi-generator training) is unfinished work.**

The sharpest single instance: **flip recall is 0.0% on the reference set.** Per §5, our flip
class is *always* rotated, so the router learned to spot flips by their transparent corners —
and the authors' rotations are RGB, with no alpha to read. Their set also contains the pure
mirrors ours lacks (`left_to_right` / `top_to_bottom`), which leave **no forensic trace at all**.
Our 99.2% flip recall (§8.2) is therefore a property of our generator, not a capability. This is
the concrete reason the geometry flag was rejected (§8.5).

### 8.5 What Phase C assumed, cut, or got wrong

Recorded so Phase D does not rediscover them.

- **Rejected: a third "geometry" flag** (`P(flip) + P(crop)` ⇒ distrust aHash/pHash), despite it
  looking like the biggest lever (aHash/pHash at 5.0%/2.8% on flips). Two measured reasons.
  *It is a near-no-op*: those signals are silent there, not noisy (§8.3 finding 4), so dropping
  them cannot change a ≥2-agree verdict. *And its trigger does not generalise*: the router's
  99.2% flip recall reads a PIL artifact and scores 0.0% cross-generator (§8.4), so the flag
  would fire reliably only where it is least needed — and would inflate Phase D's headline with
  a generator artifact. **Decided: no. Do not relitigate; the gain lives in quorum/threshold
  policy instead.**
- **Our pixelation is severe** — `rng.uniform(0.04, 0.12)`, i.e. downscale to 4–12% of size and
  back. §8.2's 100% detail decision is partly a property of that severity; a subtler pixelation
  would be materially harder. The number reads "solved" but means "solved at our severity".
- **Feature resolution policy** (load-bearing, and the reason §8.4 fails the way it does):
  structural features run on a gray image normalised to a 256 px working edge — the same
  normalisation ORB uses — so "is detail intact?" reads alike at 24 px or 336 px. Colour,
  histogram, palette and alpha features run on **native** pixels, because resampling averages
  away the quantisation combing and re-blends a collapsed palette. That split is why the model
  survives resolution changes structurally but not chromatically.
- **No feature ablation was run.** All 93 features were kept on the strength of RandomForest
  importances alone; we do not know which are load-bearing, redundant, or actively harmful
  cross-generator. Given §8.4, an ablation that drops the generator-specific tells
  (transparent-corner, combing) and re-measures is the obvious first experiment.
- **A threshold sweep that returns its grid extreme is a red flag, not a result.** The first
  gating map reported every hash detecting 100% of every category *and* 100% of the negatives.
  The tempting explanation was a base-rate artifact (the split is 82.6% positive, which really
  does reward flagging everything) — and it was wrong. The actual cause was a dict-splat bug:

  ```python
  {"category": ..., "is_copy": r["is_copy"] == "1", **r}   # WRONG
  ```

  `is_copy` is itself a CSV column, so `**r` splatted *last* overwrote the parsed bool with the
  raw `"0"`/`"1"` string — and **both strings are truthy**. Every row became a positive, there
  were zero negatives, precision was therefore always 1.0, and the sweep ran to the end of its
  grid. **Lesson: when a sweep picks the extreme end of its grid, check the label parse before
  theorising about the metric, and assert the class balance first** — printing
  `positives=11400 negatives=2400` would have caught it immediately. Same family as the §4
  lesson: an instrument that cannot fail is not measuring anything.
Reporting this is the point of having run it.

---

## 9. Phase D: the routed detector — does routing actually pay?

Phase C proved the router is an **accurate instrument** (detail 100% P/R/F1, colour 97.5%,
zero dangerous misses). That is not the same claim as the router being **useful**. Phase D
tests the second, and it is the project's actual thesis. All of it is arithmetic over the
cached signal scores — no image passes were re-run — assembled in `python/detector/`
(mirroring `python/geometric/`: one shared module `detector_common.py`, cached inputs,
per-category evaluation).

The deliverable is a **three-way comparison** that isolates each change we made to the
paper's detector, so no single "ours is better" number can hide which change earned what:

  1. **A** — static 4-hash **with sHash**: the paper's own 2-Minimal rule.
  2. **B** — static **with ORB** swapped for sHash: isolates the *swap's* gain (§5).
  3. **C** — **router-driven** dynamic voting **with ORB**: isolates the *router's* gain.

### 9.1 The headline: the swap pays, the router does not

At the paper's rule (≥2 of 4) on the test split (11,400 pos / 2,400 neg). **All four signals
in each panel vote at the same operating point** — the ≤10% FP-on-pristine-negatives budget
(§8.3): aHash≤7, pHash≤19, hsvHash≤3, sHash≤21, ORB>23. That last one matters and is a
correction: §5's ORB point t=16 was tuned on the geometric *subset* for signal quality and
sits at ~14% FP on the full negatives, so using it here would hand ORB a looser vote than the
other three and inflate the swap. Re-pointing ORB to its own iso-FP budget (t=23) makes A-vs-B
a clean comparison; t=16 remains the signal-quality number in `python/geometric/`, not the
panel-vote number (§9.8).

| detector | precision | recall | **F1** | isolates |
|---|---|---|---:|---|
| **A** static + sHash (paper) | 97.6% | 70.3% | **81.7%** | — |
| **B** static + ORB (the swap) | 97.8% | 79.2% | **87.5%** | swap = **+5.8 F1** |
| **C** routed + ORB | 97.8% | 79.2% | **87.5%** | router = **≈ 0** |

The **sHash→ORB swap is the entire gain** (+5.8 F1 at k=2), and it is located exactly where
§5 said it would be — in the geometric categories, via ORB's higher detection:

| category (detected) | A: sHash | B: ORB |
|---|---:|---:|
| flip_rotate_mirror *(rotate+resize, no pure mirrors — §5)* | 14.1% | **58.4%** |
| resize_crop_reposition | 69.9% | **80.9%** |
| pixelated | 71.1% | 66.4% |
| background_color_change | 79.5% | 84.2% |
| color_swap / text / exact | ≈ equal | ≈ equal |

The one place the swap *costs* a little is pixelation (71.1→66.4), where sHash still
contributed a vote and ORB is dead (§5) — but ORB's geometric gain dwarfs it. **The router
adds nothing measurable on top of the swap** (87.4 pure / 87.5 with fallback vs 87.5). That
is the predicted no-op, and §9.3 gives the mechanism.

*(Per-category F1 pairs each category's positives against the shared 2,400 negatives, so
low-support categories read a lower F1 at the same FP — exact_copy shows F1 ~84% despite 100%
detection. The **detection** column is the base-rate-free per-category lens; read it, not the
per-category F1, when comparing categories.)*

**The honest caveat, kept in front of every F1.** At our 82.6%-positive base rate the
flag-everything classifier scores **F1 90.5%**, so *both* baselines at k=2 sit **below** the
trivial floor. This is not a real defeat — it is the base-rate artifact §9.2 dissects — but
it is why every table here prints the computed floor beside the F1, and why the paper's k=2
must be read against §9.2 rather than in isolation.

**The paper *as deployed*.** Row A above re-tunes the paper's hash thresholds to *our* split,
an advantage a shipped detector never gets — it runs at **fixed** thresholds. Held to the paper's
**own published** 2-Minimal thresholds (aHash≤7, pHash≤15, hsvHash≤3, sHash≤17;
`NFT_Duplications.pdf`, Table V), and swapping *only* sHash→ORB (the paper's other thresholds
untouched), on the same test set:

| detector, *as deployed* | P | R | **F1** |
|---|---:|---:|---:|
| Paper 2-Minimal @ its **published** thresholds (frozen) | 98.1% | 64.1% | **77.5%** |
| **Swap — replace only sHash with ORB** | 98.1% | 77.2% | **86.4%** |
| Paper 2-Minimal @ *our* re-tuned thresholds (= A) | 97.6% | 70.3% | 81.7% |

Re-tuning *helps* the paper here (+4.2 F1); the swap beats it either way — **+8.9 F1** over the
frozen paper, +5.8 over the re-tuned one (`python/detector/evaluate_deployed.py`). *(For locating
our numbers relative to the paper's: its own tables report the 2-Minimal detector at (88.4%,
94.13%) duplicate/non-duplicate detection on DISC21 and (82.32%, 97.23%) on CryptoPunks —
recall/specificity, on a benchmark and BK-tree pipeline we do not reproduce, so not directly
comparable to our pairwise P/R/F1. Two things there still corroborate us: sHash is among their
weakest hashes, and their own Random Forest does not beat the 2-Minimal rule — Phase D's result.)*

### 9.2 The voting rule k is a base-rate parameter, not a free one

We had inherited "≥2 of 4" as a constant. It is the paper's tuned hyperparameter, tuned for
the paper's panel and base rate; we fired sHash, hired ORB, and never re-tested it. Sweeping
k on train (selection) and reporting on test:

| k | sHash panel F1 | ORB panel F1 | flag-all floor |
|---|---:|---:|---:|
| 1 | 92.4% | **94.3%** | 90.5% |
| 2 (paper) | 81.7% | 87.5% | 90.5% |
| 3 | 71.8% | 72.8% | 90.5% |
| 4 | 46.7% | 47.0% | 90.5% |

Best-k is **1 for both panels** — it does *not* move when the panel changes. But that is not
a statement about the panels; it is the base rate. At 82.6% positive, F1 rewards recall, so
the most permissive rule wins and only k=1 clears the floor. Sweeping the *assumed
prevalence* separates the two effects:

| assumed prevalence | static k=1 | static k=2 | geometry-oracle (ceiling) |
|---|---:|---:|---:|
| 2% | 15.4% | **26.7%** | 29.2% |
| 10% | 49.1% | **62.0%** | 66.7% |
| 25% | 73.0% | **77.4%** | 82.5% |
| 50% | **87.1%**† | 84.4% | 89.6% |
| 82.6% (ours) | **94.3%** | 87.5% | 92.8% |

At every realistic (low) copymint prevalence the paper's **k=2 is correct** and k=1 collapses;
only our inflated generator base rate inverts it. *(†k=1 overtakes k=2 between the 25% and 50%
marks.)*
**Real copymints are rare, so the low-prevalence rows are the deployment-relevant ones, and
they vindicate the paper's k=2.** This is why we do **not** report "best-k=1" as a
recommendation — it is an artifact of §7.2's deliberately ML-conventional split, quantified
rather than hidden.

### 9.3 Why the router is a no-op here — the mechanism, not just the number

§8.3 (finding 4) measured that a broken signal is **silent, not noisy**: where a signal's
detection collapses, its false-positive rate collapses with it. Dropping a silent signal
cannot change a ≥2-agree verdict, because it was casting no votes. So the router's value
could only ever come from the *other* lever — **relaxing the quorum** among the signals that
remain, "≥2 of 4" ⇒ "≥2 of the trusted, ≥1 of 2 when only two survive".

The measurement (C2 healthy-quorum, pure, per category vs static k=2):

- Overall **F1 87.4% vs 87.5%** (Δ −0.09%). Per category it is +0.0% everywhere **except
  background_color_change, where it *regresses* −0.9%** — distrusting hsvHash there drops a
  vote that was sometimes correct.
- τ (the flag threshold on the router's soft mass) was swept on train and barely moves the
  result (F1 86.68→86.71% across τ∈[0.3,0.7]); C2 is so close to static that its operating
  point is immaterial. We report τ=0.7 but nothing rests on it.

The reason is structural: the quorum only *falls to 1-of-2* when the router marks **two**
signals untrustworthy at once (detail-broken **and** colour-changed). Our generator applies
**one** manipulation per image, so that two-flag state effectively never occurs — the healthy
panel is almost always 4 or 3, where "≥2 of trusted" equals the static rule. **The lever the
router exists to pull is real, but our dataset almost never puts the detector in the state
where it engages.** D's static fallback (union the routed verdict with static k=2) makes the
routed detector provably never-worse; the per-category table confirms it removes the −0.9%
background regression, at the cost of the router never *helping* either.

### 9.4 Costing the geometry flag we rejected (not relitigating it)

§8.5 rejected a third "geometry" flag (`P(flip)+P(crop)` ⇒ distrust aHash/pHash) for two
reasons: (1) it is a near-no-op because those signals are silent on flips, and (2) its
trigger is a PIL artifact that scores 0% cross-generator. Phase D can now **price** that
decision instead of merely asserting it.

Reason (2) holds and is decisive (§9.5). But reason (1) turns out to be **wrong**, and the
reason it is wrong is the whole point of Phase D. Simulating the geometry flag on the ORB
panel:

| | flip recall | crop recall | FP |
|---|---:|---:|---:|
| static k=2 / C2 | 58.4% | 80.9% | 8.5% |
| + geometry flag (router trigger) | **97.7%** | **98.1%** | 8.5% |
| + geometry flag (oracle trigger) | 98.1% | 98.5% | 8.5% |

It lifts flip recall **58→98%** at **zero** FP cost. The mechanism is exactly §9.3's:
distrusting aHash **and** pHash drops the healthy panel from 4 to 2 ({hsvHash, ORB}), so the
quorum falls to **1-of-2** — and on flips hsvHash and ORB are the two survivors, so requiring
*either* rather than *both* is the whole 58→98 gap (measured). Dropping the silent hashes is
not what pays; **the quorum fall the drop triggers is.** So the geometry flag is *not* a no-op
within-distribution — it is the single biggest routing lever we found.

And it is precisely the one we cannot bank. Its trigger reaches ~98% here and **0.0%** on the
authors' set (§9.5). **The lever routing needs most is the one whose trigger does not
generalise** — that, not "routing does nothing", is Phase D's real finding about the router.
(The FP staying flat at 8.5% is the payoff of §9.1's iso-FP correction: at ORB's old looser
t=16 the geometry relax also nudged FP up, muddying exactly this "zero-cost" claim.)

### 9.5 Cross-generator: the authors' set (bounded by §8.4)

The same three-way on `data/reference/test_manipulations/` (202 pos / 1,600 *manipulated*
negatives — a realistic 11.2% base rate, and the negatives our own generator cannot make;
§7.1). Hash distances are the authors' own precomputed values; ORB was scored here and cached
(`data/reference/orb_scores.csv`, local only).

| detector | precision | recall | **F1** |
|---|---:|---:|---:|
| **A** static + sHash | 38.6% | 69.3% | **49.6%** |
| **B** static + ORB | 43.5% | 94.1% | **59.5%** |
| **C** routed + ORB (C2+fallback) | 43.5% | 94.1% | **59.5%** |

Flag-all floor here is only 20.2%, so unlike the test set **all three clear it comfortably** —
the low-prevalence regime where the detector is genuinely worth running. The swap pays even
harder here (**+9.9 F1**, recall 69→94 *and* precision 39→44); the router again adds nothing
under fallback.

**But +9.9 is measured against a handicapped paper.** Baseline A uses *our* re-tuned thresholds,
looser than the paper's own and precision-wrecking at this low prevalence. Held to the paper's
**published** thresholds — the honest "as deployed" baseline (§9.1) — and swapping only sHash→ORB:

| detector, *as deployed* | P | R | **F1** |
|---|---:|---:|---:|
| Paper 2-Minimal @ its **published** thresholds (frozen) | 55.2% | 65.8% | **60.0%** |
| **Swap — replace only sHash with ORB** | 48.0% | 93.1% | **63.3%** |
| Paper 2-Minimal @ *our* re-tuned thresholds (= A) | 38.6% | 69.3% | 49.6% |

So the *deployed* cross-generator margin is **+3.3 F1** (60.0 → 63.3), not +9.9 — and it is
threshold-dependent: at a looser ORB (t=16) the swap slips to 54.8 and *loses* to the frozen
paper. The swap still wins on the authors' own data, but honestly and by a modest, ORB-sensitive
margin. Two details tie the whole section together:

- Its pure C2 variant *appeared*, at ORB's old loose t=16, to gain +6.4 F1 for the wrong
  reason — the router misfires and distrusts ORB on **86%** of these punks (§8.4: it
  over-predicts `pixelated`), and at 11% prevalence being accidentally stricter raised F1.
  At the **iso-FP** ORB (t=23) that accidental gain **collapses to +0.3** (59.7 vs 59.5),
  because B is now already at the stricter operating point the misfire was blundering into.
  So the iso-FP correction did not just clean the headline — it dissolved a spurious
  cross-generator "win," confirming it was an artifact of ORB's loose vote, not routing.
- The geometry flag's trigger recall is **0.0%** (n=86), confirming §9.4's bound directly.

**The router's contribution here is limited by §8.4; this is a within-distribution result
stated honestly, not a cross-generator claim.**

### 9.6 Weighted vote (C3) — an extension, not the headline

Weighting each signal by its measured per-manipulation reliability (§8.3) and thresholding the
weighted sum (threshold tuned on train) scores **F1 94.2%** — matching static k=1 (94.3%),
because at this base rate it collapses toward the same permissive behaviour. It *does* pick up
the geometric lift implicitly (flip detected 98.1%, crop 98.6%) by upweighting ORB/hsvHash on
predicted-flips — i.e. it reaches §9.4's lever through the soft weights — but it inherits the
identical §8.4 fragility, and its non-duplicate FP rises to 20.7%. It departs from the paper's
rule and is the easiest to overfit, so per the plan it is reported as an extension and **does
not carry the headline**. *(And its decision threshold tuned to the grid floor (0.1) — by
§8.5's own rule a sweep landing on its grid extreme is the least trustworthy result here, a
second reason C3 is not the headline.)*

### 9.7 What Phase D measured, and the honest limits

- **The swap is the contribution; the router is not.** ORB-for-sHash buys +5.8 F1 at the
  paper's rule (+9.9 cross-generator); dynamic voting buys ≈0 on top. We built an accurate
  router and measured that routing does not pay **within our distribution** — a real result,
  reported as one rather than tuned until it looked otherwise.
- **The mechanism is understood, not just the number.** Routing is a no-op because (a) broken
  signals are silent so dropping them is free of consequence (§8.3.4), and (b) the quorum
  relax only engages when two signals are distrusted at once, which our **one-manipulation-per-
  image** generator almost never produces (§9.3). The lever is real; our data rarely triggers
  it.
- **The one lever that would pay cannot be banked.** The geometry flag lifts flip recall
  58→98% at zero FP (§9.4), but its trigger is a generator artifact scoring 0% cross-generator
  (§9.5/§8.4). This is the sharpest statement of the router's boundary.
- **k is a base-rate artifact.** Our best-k=1 is an artifact of a deliberately inflated
  positive rate (§7.2); at realistic copymint prevalence the paper's k=2 is correct (§9.2).
- **What it would take to change the verdict.** A generator that composes manipulations
  (colour-change *and* pixelate on one image) would populate the two-flag state where the
  quorum relax bites, and resolution-invariant / multi-generator router features (§8.4, §8.5's
  un-run ablation) would make the geometry trigger bankable. Both are the same unfinished work
  Phase C already named; Phase D is the measurement that says exactly why they are the
  bottleneck.

### 9.8 Two operating-point errors caught before the numbers were trusted

Publishing the near-misses is the house rule (§4, §8.5), and Phase D produced two — both in
*how a signal's threshold was chosen*, both caught by sanity-checking against the flag-all
floor rather than by the metric moving.

1. **sHash silently degenerated to flag-everything.** The first cut derived sHash's baseline
   threshold by maximising standalone F1 on the full train set. At our 82.6%-positive base
   rate that objective *is* maximised by the trivial classifier, so it returned **dist≤62**,
   which fires on **100% of pristine negatives** — a signal that always votes "duplicate" and
   contributes nothing to a ≥2 rule. It was caught the §8.5 way: the pick sat at the top of its
   grid and its precision equalled the base rate at 100% recall (the flag-everything
   signature). Fixed by placing sHash at the §8.3 ≤10% FP-budget point (**dist≤21**, standalone
   F1 77.8%), the same operating point as the three hashes.

2. **The swap comparison was not iso-FP.** ORB was left at §5's t=16, which is a
   *geometric-subset signal-quality* threshold sitting at ~14% FP on the full negatives —
   looser than the ≤10% the other three signals use. That handed B's fourth vote a small
   unearned edge and inflated the swap (measured: +6.5 F1 at t=16 vs **+5.8 at the fair t=23**;
   the cross-generator swap actually *grew*, +2.4→+9.9, and a spurious C2-pure "win" dissolved,
   §9.5). Fixed by deriving ORB's own ≤10% FP-budget point on train (**t=23**) for the panel
   vote; t=16 stays the signal-quality number in `python/geometric/`.

The lesson is one we keep relearning (§4, §8.3, §8.5): **at a skewed base rate, "maximise F1"
and "the operating point everything else uses" are different instructions, and the first one
quietly reproduces the trivial classifier.** Neither error changed a conclusion — the swap
still dominates, the router is still a no-op — but both would have put a wrong number in the
headline.

---

## 10. Phase E: the multi-manipulation test — the router's no-op is *fundamental*, not incidental

Phase D found routing pays ≈0 on single-manipulation data, with a mechanism (§9.3): the
router's quorum-relax lever only engages when **two** signals are distrusted at once, and one
manipulation per image never creates that state. That is not "the router is useless" — it is
"the situation where the router helps never occurs in single-manipulation data." Phase E
**builds** that situation — images with two manipulations composed — and tests whether a
**storage-free** detector can then earn a deployable win. The answer is no, and *why* it is no
turns out to be a deeper result than Phase D's.

### 10.1 The deployable thesis, and the panel that keeps crop-resistance

The prize is **deployability**, and it turns on a cost asymmetry (§6): ORB's cost is on-chain
**storage** (14.5 KB/NFT — fatal), while the router's cost is validator-side **compute** and it
stores nothing. So the deployable detector is confined to the paper's four *cheap* hashes
`{aHash, pHash, hsvHash, sHash}` (24–32 bytes), and the thesis is:

> a router-**managed** detector over those four hashes beats the paper's **static** detector on
> multi-manipulation — **without** sacrificing crop-resistance, adding nothing on-chain.

Keeping sHash (rather than dropping it) is deliberate: sHash is the crop-resistance signal, and
a detector that drops it goes blind on every geometric composition. The bet is that the router
can instead *manage* sHash — trust it on geometric compositions, distrust it where it breaks —
the same way it manages ORB and hsvHash. Whether that is possible is an empirical fork (§10.2).

**The generator** (`generate_dataset.py --mode compose`) applies two manipulations in sequence,
`second(first(base))`, over the **full cross-product** of the six non-exact manipulations in
**both orders** (15 unordered pairs × 2 = **30 composite categories**) — the full cross-product,
not a hand-picked subset, so the result cannot be accused of cherry-picking the combination that
happens to win, and so the *order* effect is itself measurable. It writes two splits from the
same base-image partition as the single-manip data: `data/multi_train/` (tune the policy) and
`data/multi_test/` (report), 600 bases each → **18,000 positives (600 per composition) + 2,400
pristine negatives, 88.2% positive**. The router (trained on the *train* bases' single
manipulations) has never seen the *test* bases, nor any composite on either split.

### 10.2 The fork-decider: pixelation breaks sHash, silently (Chunk 1)

Phase C never measured sHash's reliability (it was mid-swap-out). Before designing the managed
detector we added sHash to the §8.3 gating map (`hash_reliability.py`, standalone sHash at its
iso-FP point dist≤21). Read **standalone**, not as part of a panel:

| category | aHash | pHash | hsvHash | **sHash** | ORB |
|---|---:|---:|---:|---:|---:|
| resize_crop_reposition | 26% | 39% | 78% | **74.3%** | 98% |
| **pixelated** | 55% | 65% | 79% | **38.1%** | 28% |
| flip_rotate_mirror | 5% | 3% | 78% | **11.2%** | 82% |
| pristine-neg FP | 8.2% | 8.1% | 9.2% | **7.7%** | 13% |
| pixelated-neg FP | 1.9% | 2.7% | 8.2% | **8.7%** | — |

Pixelation roughly **halves** sHash detection (crop 74.3% → pixelated 38.1%), and on pixelation
sHash is **silent, not noisy** (FP 8.7% ≈ its pristine 7.7%). So the **detail flag** — which
predicts pixelation at 100% (§8.2) — *can* gate sHash exactly as it gates ORB, at no precision
cost, while sHash stays healthy on geometric compositions and keeps its crop-resistance. The
fork resolves in favour of the full-panel "keep-and-gate" design. (A prior note mis-cited §9.1's
"sHash 71.1% on pixelated" as evidence sHash *survives* pixelation; that 71.1% is the whole
4-hash **panel's** ≥2 verdict, a different quantity — corrected here.)

### 10.3 The gate, and the finding it forced: the colour tell is *erased*, not masked

The detail flag gates sHash; the win then needs the **colour** flag to *also* fire on a
pixelate+colour composite, so the panel falls to `{aHash, pHash}` and the quorum relaxes to ≥1.
Running the shipped **single-label** router on the composites (`probe_multi.py`, no retrain,
reading soft mass per §8.1), the colour flag fires **0% whenever pixelation is present** —
because pixelation's signature **monopolises** the 8-class soft mass (P(pixelated) ≈ 0.95,
leaving ≈0 for the colour classes). That is ambiguous between two very different causes: a
**tooling** limit (winner-take-all normalisation) or a **physical** one (the colour tell is
gone). The single-label router cannot tell them apart.

So we removed the competition: two **independent binary heads** (`train_multilabel.py`), "detail
= pixelated?" and "colour = colour-changed?", trained on the same single-manip features (each
fires on its own signature, no softmax coupling). On single-manip test they are clean (detail
100% on pixelated / 0% else; colour ~99% on colour categories / low else). On the composites:

| composite | detail head | **colour head** |
|---|---:|---:|
| colour × non-pixelation (e.g. flip×colour, text×colour) | 0% | **76–100%** |
| **pixelated × colour** (both orders) | 100% | **0%** |

The independent colour head **still** fires 0% on pixelate×colour — in **both** orders — while
firing 76–100% on colour composed with anything else. So it is **not** a tooling limit: the
colour forensic tell (histogram *combing*, §7.1) is **physically erased** by pixelation.
Pixelation collapses the palette, so a *later* recolour cannot imprint the combing pattern the
head reads, and an *earlier* recolour's combing is smoothed away — either way the signal is
absent. (This *refutes* our own pre-registered hypothesis that order would matter for the colour
tell — "colour-last keeps combing." It does not. Order matters for the **detail** flag instead:
pixelate-then-geometric degrades pixelation detection to 11–18% versus ~100% for
geometric-then-pixelate, because cropping/rotating a pixelated image disturbs the flatness
statistics the flag reads.)

### 10.4 The measurement: routing is a no-op, the swap still pays

`evaluate_multi.py` runs the four-way on multi_test, all at the §9 iso-FP operating points
(aHash≤7, pHash≤19, hsvHash≤3, sHash≤21, ORB>23), the router-managed policy tuned on
multi_train:

| detector | P | R | **F1** | ΔF1 vs A | isolates |
|---|---:|---:|---:|---:|---|
| **A** static + sHash (paper) | 96.7% | 36.4% | **52.9%** | — | the number to beat |
| **B** router-managed, same panel | 96.7% | 36.4% | **52.9%** | **+0.01** | the router = **≈0** |
| B pure (no fallback) | 96.5% | 34.0% | 50.3% | −2.59 | routing *alone* regresses |
| **B′** static + ORB (swap, no router) | 97.3% | 45.1% | **61.6%** | **+8.73** | the swap |
| Cstat static sHash+ORB (no router) | 97.2% | 52.0% | 67.7% | +14.79 | +sHash in panel |
| **C** router + ORB (ceiling) | 97.2% | 52.0% | **67.7%** | +14.80 | router w/ ORB = **≈0** |

The **deployable router B does not beat the paper A (+0.01 F1)**, and **C routed = C static to
+0.01 F1** (the fallback union adds a negligible handful of relaxes and removes none) — routing is
a no-op even with ORB in the panel. Without the static fallback,
routing *regresses* (−2.6): distrusting sHash on detected pixelation drops a sometimes-useful
vote, and the quorum-relax the drop is supposed to enable never fires (§10.3), so the fallback is
load-bearing purely to keep the router from *hurting*. The **sHash→ORB swap still pays**
(B′ vs A, **+8.7 F1**), located exactly where §5 said — the structure-surviving compositions.

Three corroborations:
- **The harness reproduces §9.1.** The same script on single-manip (`--single`) gives A **81.7**,
  swap B′ **87.5** (**+5.77** ≈ §9.1's +5.8), routing **+0.00** — the Phase D three-way, from
  independent code.
- **Multi-manipulation is much harder.** The paper's recall roughly **halves**, 70.3% → 36.4%
  (single → multi); the swap and ceiling halve similarly (79→45%, 84→52%). Composing two
  manipulations stacks two degradations, and even the ceiling recovers only to 52%.
- **The base-rate caveat (§9.1/§9.2) applies unchanged.** At 88.2% positive the flag-everything
  floor is **93.8%**, so every real detector sits below it on raw F1; the honest lens is recall
  at equal precision (all policies ~96–97% P). The weighted-vote extension (D) "wins" +23 F1 but
  its decision threshold lands on the **grid boundary** — the §9.6/§8.5 permissiveness artifact,
  flagged and dismissed, not a routing gain.

### 10.5 The finding: the two flaggable manipulations are mutually exclusive in detectability

The router's quorum-relax needs the **two-distrust state** — distrust hsvHash *and* sHash at
once. The only detail-breaker in the manipulation set is pixelation, and pixelation is *precisely*
the manipulation that erases the colour tell (§10.3). So the two conditions the relax requires
**cannot co-occur**: wherever the router could justifiably distrust sHash (pixelation present),
it can no longer perceive the colour change, and wherever it can perceive the colour change, there
is no pixelation to justify distrusting sHash. **The two-distrust state is unreachable in
principle**, and the quorum never relaxes.

This **generalises §9.3**. Phase D explained the no-op as a *dataset* artifact — one manipulation
per image, so two signals are never broken together — and named "compose the manipulations" as
the experiment that would change the verdict. Phase E ran exactly that experiment, and the verdict
**did not change**: the two-distrust state is not merely absent from our data, it is **forensically
self-cancelling**. That is the strongest statement of the router's boundary the project has: the
lever dynamic routing exists to pull is one the physics of these manipulations does not let it
reach. **The measured contribution remains the sHash→ORB swap** (+8.7 F1 on multi, +5.8 on
single); dynamic routing pays ≈0 on both, now for a reason that is fundamental rather than
incidental.

### 10.6 Honest limits

- **Within our generator, and at our pixelation severity.** The erasure claim rests on PIL
  pixelation at `rng.uniform(0.04, 0.12)` (§8.5) — severe. A subtler pixelation might leave some
  colour tell, which would make the two-distrust state occasionally reachable; we did not test
  milder pixelation. The colour tell's generator-specificity (§7.1/§8.4) also bounds this to a
  within-distribution result.
- **The multi-label heads are trained on single manipulations** (the deployable story: train on
  single manips, deploy on anything). They generalise to composites for the *detail* axis and,
  where the tell survives, the *colour* axis — the failure on pixelate×colour is the tell being
  absent, not the head being weak (it fires 76–100% on other colour composites).
- **Policy tuning on multi_train has mild base-image overlap** with the router's training bases
  (the first 600 train bases); it affects only the *tuning* of τ and the weighted decision, both
  of which proved immaterial (τ moves B by 0.00; the weighted decision hit its grid boundary).
  The reported numbers are on multi_test's unseen bases.
- **The deeper "mutually exclusive" claim is specific to *these* flags** (detail via pixelation,
  colour via combing). A different reliability signal — one whose trigger survived pixelation —
  could in principle reach the two-distrust state; none of the ones this project can predict do.
