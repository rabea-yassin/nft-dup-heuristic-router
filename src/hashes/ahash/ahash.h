#ifndef NFT_HASHES_AHASH_H
#define NFT_HASHES_AHASH_H

#include <stdint.h>

/* aHash (average hash): paper Section II-C item (i), Table I.
 * 64-bit hash, one bit per cell of an 8x8 grayscale downsample, set when
 * that cell's mean luma exceeds the image's overall mean luma. */
typedef uint64_t ahash_t;

/* Decodes the image at `path`, computes its aHash, and writes it to
 * `out_hash`. Returns 0 on success, -1 on bad arguments, -2 if the image
 * could not be decoded. Allocates only to decode the image; the hashing
 * itself (downsample, mean, threshold) is heap-free. */
int ahash_from_file(const char *path, ahash_t *out_hash);

/* Hamming distance between two aHash values. */
int ahash_distance(ahash_t a, ahash_t b);

#endif /* NFT_HASHES_AHASH_H */
