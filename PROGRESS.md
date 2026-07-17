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
recognises a low-resolution punk can lean on the other hashes instead. (An open
thread: `flip_rotate_mirror` at 81.7% is oddly weaker than crop's 98.3% despite
explicit mirror handling — partly the punk drag, partly possibly the flip path;
not yet diagnosed.)

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

## 7. Known validity caveat

Our manipulations are generated with PIL, so any forensic traces the router learns (e.g.
histogram "combing" — the missing-bin artifacts integer quantisation leaves behind after a
brightness or saturation edit) are partly **generator-specific**. A sufficiently rich classifier
may learn *how our dataset was made* rather than how real copymints behave.

Mitigation: also evaluate against the authors' `data/reference/test_manipulations/` set (405 real
manipulated images plus their metadata CSV), which we hold and have already validated against.
This set was shared with us informally, so it stays local and is not redistributed.

**Update: this caveat was not hypothetical — §8.3 measures it, and it bites.**

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

### 8.3 Measured, not assumed: which hash does each flag gate?

We had only ever scored ORB and sHash per category. Before Phase D hardcodes any gating,
`hash_reliability.py` scored the other three signals the same way — every signal read at a
fixed **≤10% false-positive operating point**, so the columns are comparable:

| category | aHash | pHash | hsvHash | ORB |
|---|---|---|---|---|
| flip_rotate_mirror | **6.7%** | **2.8%** | 77.8% | 81.7% |
| resize_crop_reposition | 31.2% | 38.7% | 77.8% | 98.3% |
| exact_copy (control) | 100% | 100% | 100% | 100% |
| background_color_change | 62.6% | 82.7% | **16.1%** | 98.9% |
| color_swap_modify_saturate | 75.6% | 78.2% | **28.3%** | 79.8% |
| pixelated | 58.2% | **65.5%** | 79.3% | **28.5%** |
| text_logo_emoji | 95.6% | 96.7% | 93.1% | 100% |
| non_duplicate (FP rate) | 9.9% | 8.1% | 9.2% | 13.0% |

*The ORB column reproduces §5's table exactly, which validates the harness.*

Three findings, and the first one changed the design:

1. **pHash survives pixelation (65.5%) where ORB collapses (28.5%)** — and hsvHash is the best
   signal there (79.3%). Pixelation is *approximately what pHash already does*: coarsen to
   32×32 and keep low-frequency DCT. So the detail flag must gate **ORB alone**. Gating pHash
   alongside it — the intuitive move — would have discarded a *better-performing* signal.
2. **hsvHash is noise under colour edits** (16.1% / 28.3%), confirming the colour grouping.
3. **aHash/pHash are destroyed by flips/rotations** (6.7% / 2.8%): they are not
   rotation-invariant, while hsvHash (77.8%) is. This independently reproduces the paper's own
   Table III claim. It also exposes a **third gating axis** the router does not yet emit —
   geometry should distrust aHash/pHash — which is a Phase D decision, not an assumption.

The general lesson: **each signal's blind spot is a measurement, and the intuitive grouping was
wrong in a way that would have cost accuracy.**

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
Reporting this is the point of having run it.
