#ifndef NFT_HASHES_DHASH_H
#define NFT_HASHES_DHASH_H

#include <stdint.h>

/* dHash (difference hash): mentioned in the paper's Section II-C list of
 * common hashes and evaluated in its Table II, but its real role in this
 * codebase is as crop_resistant_hash's default per-segment hash_func --
 * sHash computes one dhash per image segment. Port of imagehash.dhash():
 * grayscale, resize to 9x8 (LANCZOS), then one bit per horizontally
 * adjacent pixel pair, set when the right pixel is strictly brighter
 * than the left (8 rows x 8 comparisons = 64 bits).
 *
 * Bit order matches str(imagehash.dhash(...)): the first flattened
 * comparison (row 0, leftmost pair) is the MOST significant bit, so
 * printing with %016llx reproduces imagehash's hex string exactly.
 * (aHash/pHash pack LSB-first -- irrelevant for Hamming distances, which
 * are all the router compares, but dhash's order is pinned down so sHash
 * validation can diff hex strings against the Python oracle directly.) */
typedef uint64_t dhash_t;

/* Decodes the image at `path`, computes its dHash, and writes it to
 * `out_hash`. Returns 0 on success, -1 on bad arguments, -2 if the image
 * could not be decoded, -3 if a working allocation failed. */
int dhash_from_file(const char *path, dhash_t *out_hash);

/* dHash of an already-decoded grayscale (PIL "L") buffer, used by sHash
 * on cropped segments. `width`/`height` may be 0 for a degenerate crop
 * (PIL produces an all-zero resize for those, hence hash 0 -- real
 * occurrence, not a theoretical case). Returns 0 on success, -1 on bad
 * arguments, -3 if a working allocation failed. */
int dhash_from_l_pixels(const uint8_t *l_pixels, int width, int height,
                        dhash_t *out_hash);

/* Hamming distance between two dHash values. */
int dhash_distance(dhash_t a, dhash_t b);

#endif /* NFT_HASHES_DHASH_H */
