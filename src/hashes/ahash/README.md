# aHash — average hash

Paper Section II-C(i), Table I. A 64-bit perceptual hash keyed on the
coarse brightness layout of the image.

## Algorithm

1. Decode the image (stb_image, forced to RGB — alpha is dropped, not
   composited, matching PIL's `convert("L")` on RGBA input).
2. Grayscale (ITU-R BT.601 luma) + **8×8 box-filter downsample** in a
   single pass — shared with pHash via [`common/hashes_common`](../common).
3. Compute the mean of the 64 cell values.
4. Bit `i` = 1 iff cell `i` > the overall mean.

## Output

`ahash_t` = `uint64_t`, one bit per 8×8 cell (LSB-first). Compared by
Hamming distance (`ahash_distance`).

## Files

| File | Role |
|------|------|
| `ahash.h` / `ahash.c` | implementation |
| `ahash_sanity_check.c` | build-and-eyeball check vs `data/example/generated/` |

## Parity

Bit-tolerant target (small Hamming tolerance accepted). Sanity distances:
`azuki_#824` exact_copy → 0, color_swap → 2, background_change → 5.

## Notes

Strong for coarse brightness / text-and-logo / pixelation manipulations;
weak against most subtle edits, which is why the paper corroborates it
against three other hashes rather than trusting it alone.
