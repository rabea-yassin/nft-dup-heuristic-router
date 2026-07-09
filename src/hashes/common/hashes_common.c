#include "hashes_common.h"

#include <stddef.h>

#include "stb_image.h"

#define DOWNSAMPLE_CHANNELS 3 /* force RGB decode; alpha is dropped, not
                                  composited, matching PIL's convert("L") */
#define DOWNSAMPLE_MAX_DIM 64 /* generous ceiling for stack accumulators;
                                  every hash's target grid is far smaller */

int hashes_grayscale_box_downsample(const char *path, int out_dim, double *out_cells) {
    if (!path || !out_cells || out_dim <= 0 || out_dim > DOWNSAMPLE_MAX_DIM) {
        return -1;
    }

    int width, height, source_channels;
    unsigned char *pixels = stbi_load(path, &width, &height, &source_channels, DOWNSAMPLE_CHANNELS);
    if (!pixels) {
        return -2;
    }

    double sum[DOWNSAMPLE_MAX_DIM][DOWNSAMPLE_MAX_DIM] = {{0}};
    int count[DOWNSAMPLE_MAX_DIM][DOWNSAMPLE_MAX_DIM] = {{0}};

    for (int y = 0; y < height; y++) {
        int cell_y = (y * out_dim) / height;
        const unsigned char *row = pixels + (size_t)y * width * DOWNSAMPLE_CHANNELS;
        for (int x = 0; x < width; x++) {
            int cell_x = (x * out_dim) / width;
            const unsigned char *p = row + (size_t)x * DOWNSAMPLE_CHANNELS;
            double luma = 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2];
            sum[cell_y][cell_x] += luma;
            count[cell_y][cell_x]++;
        }
    }

    stbi_image_free(pixels);

    for (int cy = 0; cy < out_dim; cy++) {
        for (int cx = 0; cx < out_dim; cx++) {
            out_cells[cy * out_dim + cx] = sum[cy][cx] / count[cy][cx];
        }
    }

    return 0;
}
