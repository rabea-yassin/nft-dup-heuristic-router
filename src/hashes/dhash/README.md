# dHash — difference hash

A **bit-exact** port of `imagehash.dhash()`. A 64-bit hash keyed on
horizontal gradients (edges) rather than absolute brightness, which makes
it robust to global brightness shifts.

dHash is one of the paper's Table II hashes, but its real role in this
codebase is as [`shash`](../shash)'s default per-segment `hash_func` —
sHash computes one dHash per image segment — so it lands ahead of sHash.

## Algorithm

1. Decode → RGB, grayscale (via [`common/pil_ops`](../common) `pil_rgb_to_l`).
2. **LANCZOS resize to 9×8** (`pil_ops` `pil_resize_lanczos_l`).
3. Bit = 1 iff each pixel's right neighbor is strictly brighter than it
   (8 comparisons per row × 8 rows = 64 bits).

## Output

`dhash_t` = `uint64_t`, packed **MSB-first** — the first flattened
comparison is the most significant bit, so `%016llx` reproduces
imagehash's hex string verbatim. (aHash/pHash pack LSB-first; the order is
irrelevant for Hamming distance but pinned here so sHash segment lists can
be diffed against the Python oracle as hex strings.) Compared by Hamming
distance (`dhash_distance`).

`dhash_from_l_pixels()` hashes an already-decoded, cropped grayscale
buffer without re-decoding — this is what sHash calls per segment.

## Files

| File | Role |
|------|------|
| `dhash.h` / `dhash.c` | implementation |
| `dhash_sanity_check.c` | build-and-eyeball check vs `data/example/generated/` |

## Parity

**Bit-exact** — validated against live `imagehash.dhash()` as part of the
600-image sHash validation (see [`shash`](../shash)), zero mismatches.
Sanity distances: `azuki_#824` exact_copy → 0, color_swap → 9,
background_change → 5.
