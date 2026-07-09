#include "dhash.h"

#include <stdlib.h>

#include "pil_ops.h"
#include "stb_image.h"

#define DHASH_CHANNELS 3 /* force RGB decode; alpha is dropped, not
                             composited, matching PIL's convert("L") */
#define DHASH_COLS 9     /* hash_size + 1: 9 pixels -> 8 differences */
#define DHASH_ROWS 8

int dhash_from_l_pixels(const uint8_t *l_pixels, int width, int height,
                        dhash_t *out_hash) {
    if (!l_pixels || !out_hash || width < 0 || height < 0) {
        return -1;
    }

    uint8_t small[DHASH_COLS * DHASH_ROWS];
    int rc = pil_resize_lanczos_l(l_pixels, width, height, small, DHASH_COLS, DHASH_ROWS);
    if (rc != 0) {
        return rc;
    }

    /* pixels[:, 1:] > pixels[:, :-1], flattened row-major, MSB first */
    dhash_t hash = 0;
    for (int row = 0; row < DHASH_ROWS; row++) {
        const uint8_t *r = small + row * DHASH_COLS;
        for (int col = 0; col < DHASH_COLS - 1; col++) {
            hash = (hash << 1) | (dhash_t)(r[col + 1] > r[col]);
        }
    }

    *out_hash = hash;
    return 0;
}

int dhash_from_file(const char *path, dhash_t *out_hash) {
    if (!path || !out_hash) {
        return -1;
    }

    int width, height, source_channels;
    unsigned char *pixels = stbi_load(path, &width, &height, &source_channels, DHASH_CHANNELS);
    if (!pixels) {
        return -2;
    }

    uint8_t *l = malloc((size_t)width * height);
    if (!l) {
        stbi_image_free(pixels);
        return -3;
    }
    pil_rgb_to_l(pixels, width, height, l);
    stbi_image_free(pixels);

    int rc = dhash_from_l_pixels(l, width, height, out_hash);
    free(l);
    return rc;
}

int dhash_distance(dhash_t a, dhash_t b) {
    dhash_t x = a ^ b;
    int distance = 0;
    while (x) {
        x &= (x - 1); /* clear lowest set bit */
        distance++;
    }
    return distance;
}
