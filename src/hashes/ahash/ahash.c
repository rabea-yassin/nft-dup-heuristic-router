#include "ahash.h"

#include "hashes_common.h"

#define AHASH_DIM 8

int ahash_from_file(const char *path, ahash_t *out_hash) {
    if (!out_hash) {
        return -1;
    }

    double cell_mean[AHASH_DIM * AHASH_DIM];
    int rc = hashes_grayscale_box_downsample(path, AHASH_DIM, cell_mean);
    if (rc != 0) {
        return rc;
    }

    double mean = 0.0;
    for (int i = 0; i < AHASH_DIM * AHASH_DIM; i++) {
        mean += cell_mean[i];
    }
    mean /= (double)(AHASH_DIM * AHASH_DIM);

    ahash_t hash = 0;
    for (int i = 0; i < AHASH_DIM * AHASH_DIM; i++) {
        if (cell_mean[i] > mean) {
            hash |= ((ahash_t)1 << i);
        }
    }

    *out_hash = hash;
    return 0;
}

int ahash_distance(ahash_t a, ahash_t b) {
    ahash_t x = a ^ b;
    int distance = 0;
    while (x) {
        x &= (x - 1); /* clear lowest set bit */
        distance++;
    }
    return distance;
}
