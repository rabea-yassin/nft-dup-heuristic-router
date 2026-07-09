#ifndef NFT_HASHES_PHASH_H
#define NFT_HASHES_PHASH_H

#include <stdint.h>

/* pHash (DCT hash): paper Section II-C item (ii), Table I. 64-bit hash: the
 * image is downsampled to 32x32 grayscale, a 2D DCT-II is taken, and one
 * bit is set per coefficient in the top-left 8x8 low-frequency block that
 * exceeds that block's median (DC term included, matching imagehash's
 * phash()). Low frequencies are robust to the high-frequency noise pixel
 * hashes like aHash are sensitive to. */
typedef uint64_t phash_t;

/* Decodes the image at `path`, computes its pHash, and writes it to
 * `out_hash`. Returns 0 on success, -1 on bad arguments, -2 if the image
 * could not be decoded. Allocates only to decode the image; the DCT,
 * median, and threshold steps run on fixed-size stack buffers. */
int phash_from_file(const char *path, phash_t *out_hash);

/* Hamming distance between two pHash values. */
int phash_distance(phash_t a, phash_t b);

#endif /* NFT_HASHES_PHASH_H */
