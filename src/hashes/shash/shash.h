#ifndef NFT_HASHES_SHASH_H
#define NFT_HASHES_SHASH_H

#include <stdint.h>

#include "dhash.h"

/* sHash (segmentation hash): paper Section II-C item (vi), Table I. Port
 * of imagehash.crop_resistant_hash() with its default parameters --
 * verified to be exactly what the paper's own numbers used by recomputing
 * the authors' reference CSV (data/reference/test_manipulations/
 * final_test_metadata.csv) hash strings from their images with defaults
 * and matching them string for string.
 *
 * Pipeline: grayscale -> 300x300 LANCZOS resize -> GaussianBlur(2) ->
 * MedianFilter(3) -> threshold at 128 -> flood-fill segmentation (bright
 * "hill" regions first, then dark "valley" regions), keep segments larger
 * than 500 pixels -> scale each segment's bounding box back to the
 * original resolution, crop it, and dhash the crop.
 *
 * Unlike the other three hashes, the result is a variable-length LIST of
 * 64-bit dhashes (Python's ImageMultiHash), and comparison is a
 * best-match pairing, not one Hamming distance. */

/* A segment must exceed 500 of the 300x300 = 90000 segmentation pixels
 * to be kept, so at most 90000/501 = 179 segments can ever be stored and
 * a fixed-capacity result needs no heap. (The fallback whole-image
 * segment only appears when count would otherwise be 0.) */
#define SHASH_MAX_SEGMENTS ((300 * 300) / (500 + 1))

/* imagehash's default match cutoff: a segment pair only counts as
 * matching when its Hamming distance is <= 25% of the hash bits
 * (64 * 0.25 = 16). */
#define SHASH_HAMMING_CUTOFF 16.0

typedef struct {
    int count; /* >= 1 for any successfully hashed image */
    dhash_t segment_hashes[SHASH_MAX_SEGMENTS];
} shash_t;

/* Decodes the image at `path`, computes its sHash, and writes it to
 * `out_hash`. Returns 0 on success, -1 on bad arguments, -2 if the image
 * could not be decoded, -3 if a working allocation failed.
 *
 * Note on allocation: the other hashes are heap-free past image decode,
 * but sHash inherently is not -- it needs a full-resolution grayscale
 * plane, fixed 300x300 segmentation planes plus a flood-fill queue
 * (~0.5 MB, too large to put on callers' stacks), and per-segment crop
 * buffers sized by the input image. All of it is working memory freed
 * before returning; the returned shash_t itself is a plain value. */
int shash_from_file(const char *path, shash_t *out_hash);

/* ImageMultiHash.hash_diff: for each segment hash in `a`, find the
 * closest segment hash in `b`; pairs above `hamming_cutoff` are
 * discarded. Writes the number of matching segments and the sum of their
 * distances. Directional: a's segments are matched against b's. */
void shash_hash_diff(const shash_t *a, const shash_t *b, double hamming_cutoff,
                     int *out_matches, int *out_sum_distance);

/* ImageMultiHash.matches with region_cutoff=1 and the default cutoff:
 * 1 if at least one segment pair matches, else 0. */
int shash_matches(const shash_t *a, const shash_t *b);

/* ImageMultiHash.__sub__: segment-count-scaled difference score in
 * [0, a->count]; 0 means every segment matched exactly. */
double shash_sub(const shash_t *a, const shash_t *b);

/* The paper's own sHash distance (the "sHash_dist" column of the authors'
 * reference CSV, reverse-engineered and verified on all 1802 rows): the
 * mean over a's segment hashes of the minimum Hamming distance to b's
 * segment hashes, with no cutoff. This is the metric the paper's Table II
 * distances and sHash thresholds (e.g. sHasht = 23) are expressed in, so
 * it's what the router's BK-tree thresholds will refer to. Directional:
 * `a` plays the original's role. */
double shash_paper_distance(const shash_t *a, const shash_t *b);

#endif /* NFT_HASHES_SHASH_H */
