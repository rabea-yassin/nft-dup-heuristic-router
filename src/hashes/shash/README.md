# sHash — segmentation hash

Paper Section II-C(vi), Table I. A **bit-exact** port of
`imagehash.crop_resistant_hash()` (default parameters). Built for
**crop resistance**: even after a crop, some large objects survive, so
hashing each object's region separately means a surviving region still
matches — where a single whole-image hash would be wildly off.

Unlike the other four hashes, the result is a variable-length **list** of
[dHashes](../dhash) (Python's `ImageMultiHash`), and comparison is a
best-match pairing, not one Hamming distance.

## Algorithm

1. Grayscale → **300×300 LANCZOS resize** (the segmentation canvas — often
   an *upscale* for our data, so a box filter would be wrong).
2. **GaussianBlur(2) → MedianFilter(3)** — smear away fine detail so
   segmentation finds a few big coherent blobs, not speckles.
3. Threshold at 128 → binary bright/dark mask.
4. **4-connected flood-fill segmentation**: bright "hill" regions, then
   dark "valley" regions; keep those > 500 px.
5. For each region: bounding box → scale back to original resolution →
   crop the original → dHash the crop.

All the pixel operations (resize, blur, median, crop, RGB→L) are the
bit-exact Pillow ports in [`common/pil_ops`](../common).

## Output

`shash_t` = `{ int count; dhash_t segment_hashes[SHASH_MAX_SEGMENTS]; }`.
`SHASH_MAX_SEGMENTS` = 90000/501 = **179** is provably the max (each kept
segment is > 500 of 90000 px). Comparison functions:

| Function | Meaning |
|----------|---------|
| `shash_hash_diff` | `ImageMultiHash.hash_diff` — (matching segments, sum of distances) under a cutoff |
| `shash_matches` | ≥ 1 segment matches within the default 25%-of-bits cutoff |
| `shash_sub` | `ImageMultiHash.__sub__` — the library's own ranking score |
| `shash_paper_distance` | **the paper's own metric** (see below) |

## Parity

**Bit-exact** — validated against live `imagehash.crop_resistant_hash()`
across **600 images** (40 example + all 402 `data/reference/
test_manipulations/` paper images + 100 raw 2000×2000 NFTs + 40 sprites +
18 synthetic edge cases incl. a 99-segment image and the zero-segment
whole-image fallback), matching the **full ordered segment list** with
zero mismatches. `shash_paper_distance` reproduces all **1802 rows** of
the authors' CSV `sHash_dist` exactly, end-to-end. ASan + UBSan clean.

## Notes

- **The paper's `sHash_dist` is NOT `__sub__`.** It's the mean over the
  *original's* segment hashes of the min Hamming distance to any *copy*
  segment hash (directional, no cutoff) — `shash_paper_distance`. This is
  the metric Table II distances and the paper's sHash thresholds (e.g.
  `sHasht = 23`) are expressed in, so it's what the router's sHash index
  threshold will refer to. Verified: 1802/1802 CSV rows match this;
  copy→original only matches 240/1802.
- **Heap-allocation exception.** The other hashes are heap-free past image
  decode; sHash genuinely needs working memory (full-res L plane, 300×300
  planes, a 90k-int BFS queue). It's all freed before returning, and the
  result stays a plain value type. Documented in `shash.h`.
- **Segmentation = plain connected components.** imagehash's
  `unassigned`/`already_segmented` bookkeeping doesn't change segment
  membership; the one place the set's *cardinality* matters is the valley
  loop's loose termination, replicated exactly (see `shash.c`).
- The `(row, col) = (y, x)` axis order in the bounding-box scaling is an
  easy transposition bug — `min_y` uses the row coord and `scale_h`,
  `min_x` uses the col coord and `scale_w`.

Excellent for cropping; has some color sensitivity.
