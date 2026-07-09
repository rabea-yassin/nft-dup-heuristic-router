# pHash — perceptual (DCT) hash

Paper Section II-C(ii), Table I. A 64-bit hash keyed on the low-frequency
(coarse) structure of the image, discarding high-frequency detail and
noise that pixel-level hashes like aHash react to.

## Algorithm

1. Decode → RGB, grayscale + **32×32 box-filter downsample** (shared with
   aHash via [`common/hashes_common`](../common)).
2. 2D DCT-II (matching `scipy.fftpack.dct(type=2, norm=None)`'s convention
   — axis 0 then axis 1). Only the top-left **8×8 low-frequency block** is
   ever computed, not the full 32×32 transform.
3. Bit `i` = 1 iff coefficient `i` > the median of those 64 coefficients,
   **including the DC term** — matching `imagehash.phash()`'s convention
   (not the textbook DC-excluded variant).

## Output

`phash_t` = `uint64_t` (LSB-first over the 8×8 low-freq block). Compared by
Hamming distance (`phash_distance`).

## Files

| File | Role |
|------|------|
| `phash.h` / `phash.c` | implementation |
| `phash_sanity_check.c` | build-and-eyeball check vs `data/example/generated/` |

## Parity

Bit-tolerant target. Sanity distances: `azuki_#824` exact_copy → 0,
color_swap → 6, background_change → 6; `bayc_#1242` pixelated → 20.

## Notes

More robust than aHash to compression and minor edits (it keys on coarse
structure); still weak against strong geometric change.
