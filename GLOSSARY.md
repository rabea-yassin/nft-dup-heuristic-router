# Glossary

Plain-language definitions for every term this project leans on. The **single
home** for definitions — PROGRESS.md and PLAN.md point here rather than
re-explaining, so nothing drifts. If a term is fuzzy anywhere, it's defined here.

The mental model behind almost every number: the system looks at a **pair** of
images and answers one question — *"is the second a copy of the first?"*

---

## Scoring — how we grade an answer

**Recall** — of the real copies, what fraction did we catch? Misses let copymints
through. *90% recall ⇒ 1 in 10 copies escapes.*

**Precision** — of everything we flagged as a copy, what fraction really were?
Low precision = crying wolf. *66% precision ⇒ 1 in 3 accusations hits an innocent
image.*

**The recall/precision tension** — they trade off. **Any** signal can hit 100%
recall by flagging *everything* — at terrible precision. So **a recall number
alone is meaningless**; always read it next to precision. This trap bit us twice.

**F1** — one number balancing precision and recall (their harmonic mean). Lets us
rank signals without first arguing which of the two matters more.

**FP rate** (false-positive rate) — of the *innocent* images, what fraction did we
wrongly flag? The honest sanity check: a signal can fake high recall by flagging
everything, and the FP rate is what exposes it.

**Dangerous miss** — a project-specific worst-case error: the router calling a
truly-pixelated image "detail intact", which would make the detector *trust ORB* —
the one signal pixelation destroys. Tracked separately because it's the error that
actually causes harm; Phase C's rate is 0%.

**TP / FP / FN / TN** — true/false positive/negative. A **false negative** (missing
a real copy) is the worst error class for this system: a copymint slips through.

---

## Honesty checks — the two bars every result is read against

**Flag-everything baseline** (a.k.a. the trivial classifier) — the score you get by
blindly calling *everything* a duplicate. At our 60/40 base rate that's **F1 75.0%**.
Any real signal must clear it; sHash's oracle F1 (76.4%) barely does, which is the
tell that sHash is weak. Quote it next to any F1.

**Oracle threshold** — a cutoff chosen by *peeking at the test set's answers* —
tuned to score best on the very data it's being graded on. That's cheating in
normal use (you never have the answers in advance), so an oracle number is an
**upper bound**: "the best this signal could *possibly* look", not a real-world
number. Legitimate only when used deliberately as a "beat even this" bar.

**oracle-sHash** — sHash evaluated with an oracle (test-peeked) threshold: the
strongest sHash can possibly appear.
- **Right** in §5, where we were proving *ORB beats sHash*: handing sHash its
  best-case threshold rigs the comparison in sHash's favour, so ORB winning anyway
  means it wins for real.
- **Wrong** as a Phase D baseline: there sHash is the *thing to beat*, so a
  test-oracle gives the baseline a test-set advantage our own (train-tuned)
  detector doesn't get — and could manufacture a **false negative** (routing beats
  honest-sHash but loses to oracle-sHash, so we'd wrongly report "routing doesn't
  pay"). The direction of "unfairness" has to point *away* from the claim.

**Tune on train, report on test** — pick every threshold on the *train* split, then
report numbers on the *test* split, which those thresholds never saw. Otherwise
you're grading your own homework. Choosing an operating point on test is the exact
error that invalidated the imported baseline (PROGRESS §4).

**Per-category reporting** — always break numbers down by manipulation type, never
one global F1. A single headline hides where a signal is broken — and any routing
win is *concentrated* exactly where signals break, so a global number can bury it
(or fake it).

---

## The signals — the four "judges" that vote

**Perceptual hash** — turns a whole image into one short binary string; two images
are "close" if their strings differ in few bits (Hamming distance). Robust to small
edits by design.

**aHash** (average hash) — 8×8 grayscale, one bit per pixel vs the mean. Simple;
survives blur/pixelation, blind to colour, destroyed by rotation.

**pHash** (perceptual/DCT hash) — 32×32 → keep the low-frequency DCT corner.
Survives pixelation (it already coarsens the image), destroyed by rotation.

**hsvHash** — a **global** colour histogram (`imagehash.colorhash`): fractions of
black/gray/hue-binned pixels. Survives flips/rotations, blind to colour/background
changes (which are exactly what break it).

**dHash** (difference hash) — 9×8, one bit per left→right brightness step. A paper
hash, but here mainly sHash's per-segment building block.

**sHash** (segment/crop-resistant hash) — the paper's crop-resistance hash. Splits
the image into segments and hashes each, giving a **variable-length list**, not one
string. **Being replaced by ORB.** Two reasons: its distance can't be soundly
indexed (see below), and ORB does its job far better.

**ORB** (Oriented FAST + Rotated BRIEF) — a **feature-matching** approach, not a
hash. Finds ~hundreds of keypoints and matches them geometrically. Replaces sHash
as the crop/geometry signal. Strong wherever spatial structure survives; its one
blind spot is destroyed high-frequency detail (pixelation).

**Structure specialist** — our finding about ORB: it works wherever *spatial
structure* survives (crop, rotate, recolour, logo overlay), and fails in exactly
*one* place — where high-frequency detail is destroyed (pixelation). Not the same
as "only geometric".

---

## ORB machinery

**Keypoint** — a distinctive spot in an image (a corner, an edge junction) that ORB
can re-find after edits.

**Descriptor** — a 256-bit string summarising the patch around one keypoint. One
image → hundreds of descriptors (this is why ORB can't live in a BK-tree, and why
its on-chain byte cost is huge — PROGRESS §6).

**Match / BFMatcher** — pairing each of image A's descriptors with its closest in
image B. Many pairings are coincidental junk.

**RANSAC** — the filter that separates real matches from junk: it finds the single
geometric transform (rotate + shift + scale) that the most matches agree on. Real
duplicates have one consistent story; coincidences point every which way.

**Inlier / inlier count** — the matches that agree with RANSAC's winning transform.
The **count is ORB's score**: many keypoints independently corroborating one
transform is near-impossible by chance. Our verdict cutoff is `inliers > 16`.

**Homography** — the mathematical transform RANSAC fits (a plane-to-plane mapping);
it can express rotation, scale, crop and reflection.

**Mirror hack** — ORB is rotation-invariant but *not* reflection-invariant, so a
mirrored copy's descriptors don't match until you physically flip and re-describe
it. We score over all mirrorings and take the best.

---

## Index structures — how you'd search at scale

**BK-tree** (Burkhard-Keller tree) — the paper's index: stores one binary string
per image, searchable by Hamming distance. **Requires a true metric** (symmetry +
triangle inequality) to prune correctly.

**Metric** — a distance that is symmetric (`d(a,b)=d(b,a)`) and obeys the triangle
inequality. sHash's distance is **neither**, which is why it can't be soundly
BK-tree indexed — the project's first main finding (PROGRESS §2).

**LSH** (Locality-Sensitive Hashing) — the index ORB needs, because ORB emits a
*set* of descriptors, not one string. *The descriptor type dictates the index* — a
BK-tree can't hold ORB. (A KD-tree would imply SIFT's float descriptors instead.)

**Pairwise vs retrieval** — *pairwise*: score one (original, copy) pair directly,
which is how we evaluate (matches the dataset's `is_copy`-labels-a-pair schema).
*Retrieval*: search one query against a whole gallery — the deployment shape, and
where a stale gallery invalidated the old baseline (PROGRESS §4).

---

## The router (Phase C)

**Router** — a RandomForest that predicts, *from the query image alone*, which
manipulation produced it — so the detector can adjust how it trusts each signal.
Its features are all **absolute descriptors of one image** (no reference to compare
against at inference — the load-bearing constraint).

**Soft mass** — the router doesn't say "it's pixelated"; it says "70% pixelated".
Reading that *probability mass* (rather than the single top guess) lets an unsure
router discount a signal *partially* instead of flipping it on a coin-toss.

**Reliability decision** — what the detector actually consumes: *"is detail
broken?"* (⇒ distrust ORB) and *"did colour change?"* (⇒ distrust hsvHash),
each derived from the soft mass.

**Gating map** — the measured table of how well each signal detects each
manipulation, at a fixed FP operating point. Built so Phase D doesn't *assume*
which signal each flag should gate. It overturned the intuitive grouping: pHash
*survives* pixelation, so the detail flag gates ORB **alone** (PROGRESS §8.3).

**Silent vs noisy** — a signal that fails a manipulation can either **abstain**
(low detection *and* low FP — "silent") or **misfire** (flags innocents — "noisy").
Measured result: broken signals here are **silent**. Consequence: *dropping* a
silent signal from a vote changes nothing, so routing's value isn't "drop broken
signals" — it's adjusting the voting rule. This reframes the whole router thesis.

---

## The detector (Phase D)

**2-Minimal / "≥2 of 4" / quorum** — the paper's rule: flag a duplicate when at
least 2 of the 4 signals agree. The cross-corroboration is what suppresses each
signal's individual false positives. **We are testing whether "2" is still right
for a panel that swapped sHash for ORB** — it was tuned for the old panel.

**Static vs dynamic detector** — *static*: fixed thresholds, everyone always votes
(the paper). *Dynamic*: the router adjusts thresholds/quorum per image. The dynamic
detector falls back to static when the router is unconfident, so it can never be
*worse* than the paper.

**Weighted vote** — an extension where each signal's vote is weighted by its
measured reliability for the predicted manipulation, rather than one-vote-each.
Kept off the headline because it departs from the paper's rule and overfits easily.

---

## Data & domain

**Copymint** — an NFT that reuses another's image with a manipulation, to pass as
original. What the whole system detects.

**Manipulation categories** (8 dataset labels) — `exact_copy`, `resize_crop_
reposition`, `flip_rotate_mirror`, `pixelated`, `color_swap_modify_saturate`,
`background_color_change`, `text_logo_emoji`, `non_duplicate`. Note
`flip_rotate_mirror` in *our* data always includes a rotation, so our "flip"
numbers describe **rotate+resize**, not pure mirrors (PROGRESS §5).

**Collections** — `azuki` / `bayc` (256×256 artwork) and `cp` (CryptoPunks, native
**24×24** pixel art). The tiny punks are the hard case: ORB can barely describe
them, and the paper used them for its Table V.

**Combing** — the tell-tale empty bins integer quantisation leaves in a histogram
after a brightness/saturation edit; a forensic feature the router reads. Partly
**generator-specific** — one reason the router doesn't fully transfer to the
authors' differently-made set (PROGRESS §7.1, §8.4).
