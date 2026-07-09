#include "phash.h"

#include <math.h>
#include <stdlib.h>

#include "hashes_common.h"

#define PHASH_SRC_DIM 32
#define PHASH_LOW_DIM 8
#define PHASH_LOW_CELLS (PHASH_LOW_DIM * PHASH_LOW_DIM)

/* Not guaranteed by <math.h> under strict C11 (M_PI needs a glibc feature
 * test macro that -std=c11 doesn't set), so spell it out. */
#define PHASH_PI 3.14159265358979323846

static int compare_double(const void *a, const void *b) {
    double da = *(const double *)a;
    double db = *(const double *)b;
    return (da > db) - (da < db);
}

/* 2D DCT-II of `src`, matching scipy.fftpack.dct(x, type=2, norm=None)
 * applied along axis 0 then axis 1 (what imagehash's phash() uses) -- but
 * only the low_dim x low_dim low-frequency corner is ever read downstream,
 * so only those output frequencies are computed instead of the full
 * SRC_DIM x SRC_DIM transform.
 *
 * cos(k, n) depends only on the frequency index and the input position,
 * never on the other input axis, so each pass hoists it to the loop level
 * where it's invariant instead of recomputing it in the innermost loop. */
static void dct2d_low_freq(const double src[PHASH_SRC_DIM][PHASH_SRC_DIM],
                            double out[PHASH_LOW_DIM][PHASH_LOW_DIM]) {
    double partial[PHASH_LOW_DIM][PHASH_SRC_DIM] = {{0}};

    for (int k = 0; k < PHASH_LOW_DIM; k++) {
        for (int y = 0; y < PHASH_SRC_DIM; y++) {
            double c = cos(PHASH_PI * k * (2 * y + 1) / (2.0 * PHASH_SRC_DIM));
            for (int x = 0; x < PHASH_SRC_DIM; x++) {
                partial[k][x] += src[y][x] * c;
            }
        }
    }

    for (int k = 0; k < PHASH_LOW_DIM; k++) {
        for (int m = 0; m < PHASH_LOW_DIM; m++) {
            out[k][m] = 0.0;
        }
    }

    for (int m = 0; m < PHASH_LOW_DIM; m++) {
        for (int x = 0; x < PHASH_SRC_DIM; x++) {
            double c = cos(PHASH_PI * m * (2 * x + 1) / (2.0 * PHASH_SRC_DIM));
            for (int k = 0; k < PHASH_LOW_DIM; k++) {
                out[k][m] += partial[k][x] * c;
            }
        }
    }

    /* scipy's unnormalized type-2 DCT applies a factor of 2 per axis. */
    for (int k = 0; k < PHASH_LOW_DIM; k++) {
        for (int m = 0; m < PHASH_LOW_DIM; m++) {
            out[k][m] *= 4.0;
        }
    }
}

int phash_from_file(const char *path, phash_t *out_hash) {
    if (!out_hash) {
        return -1;
    }

    double cells[PHASH_SRC_DIM * PHASH_SRC_DIM];
    int rc = hashes_grayscale_box_downsample(path, PHASH_SRC_DIM, cells);
    if (rc != 0) {
        return rc;
    }

    double src[PHASH_SRC_DIM][PHASH_SRC_DIM];
    for (int y = 0; y < PHASH_SRC_DIM; y++) {
        for (int x = 0; x < PHASH_SRC_DIM; x++) {
            src[y][x] = cells[y * PHASH_SRC_DIM + x];
        }
    }

    double freq[PHASH_LOW_DIM][PHASH_LOW_DIM];
    dct2d_low_freq(src, freq);

    double flat[PHASH_LOW_CELLS];
    int idx = 0;
    for (int k = 0; k < PHASH_LOW_DIM; k++) {
        for (int m = 0; m < PHASH_LOW_DIM; m++) {
            flat[idx++] = freq[k][m];
        }
    }

    double sorted[PHASH_LOW_CELLS];
    for (int i = 0; i < PHASH_LOW_CELLS; i++) {
        sorted[i] = flat[i];
    }
    qsort(sorted, PHASH_LOW_CELLS, sizeof(double), compare_double);
    /* PHASH_LOW_CELLS (64) is even; match numpy.median's average-of-two-
     * middle-elements convention. */
    double median = (sorted[PHASH_LOW_CELLS / 2 - 1] + sorted[PHASH_LOW_CELLS / 2]) / 2.0;

    phash_t hash = 0;
    for (int i = 0; i < PHASH_LOW_CELLS; i++) {
        if (flat[i] > median) {
            hash |= ((phash_t)1 << i);
        }
    }

    *out_hash = hash;
    return 0;
}

int phash_distance(phash_t a, phash_t b) {
    phash_t x = a ^ b;
    int distance = 0;
    while (x) {
        x &= (x - 1);
        distance++;
    }
    return distance;
}
