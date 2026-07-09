#include "shash.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "pil_ops.h"
#include "stb_image.h"

#define SHASH_CHANNELS 3   /* force RGB decode; alpha dropped, not composited */
#define SHASH_SEG 300      /* segmentation_image_size */
#define SHASH_SEG_PIXELS (SHASH_SEG * SHASH_SEG)
#define SHASH_THRESHOLD 128
#define SHASH_MIN_SEGMENT 500
#define SHASH_GAUSSIAN_RADIUS 2.0f /* ImageFilter.GaussianBlur() default */
#define SHASH_GAUSSIAN_PASSES 3    /* Pillow's _gaussian_blur default */

/* A kept segment, tracked as its bounding box in 300x300 segmentation
 * space (rows = y, cols = x). crop_resistant_hash only ever needs the
 * bounding box, not the full pixel set. */
typedef struct {
    int min_row, max_row, min_col, max_col;
} seg_bbox;

/* 4-connected flood fill from `start` over pixels whose threshold class
 * equals `target`, marking `visited` and filling the bounding box.
 * Returns the pixel count of the component. This is the C equivalent of
 * imagehash's _find_region: the `unassigned`/`already_segmented` set
 * bookkeeping there only prevents re-entering claimed pixels, which
 * `visited` handles directly, and (per the analysis in NEXT_STEPS) yields
 * the same components as plain connected-components on the mask. */
static int flood_fill(const uint8_t *thresh, uint8_t *visited, int *queue,
                      int start, uint8_t target, seg_bbox *box) {
    int head = 0, tail = 0;
    queue[tail++] = start;
    visited[start] = 1;

    int min_row = SHASH_SEG, max_row = -1, min_col = SHASH_SEG, max_col = -1;
    int size = 0;

    while (head < tail) {
        int p = queue[head++];
        int row = p / SHASH_SEG;
        int col = p % SHASH_SEG;
        size++;
        if (row < min_row) min_row = row;
        if (row > max_row) max_row = row;
        if (col < min_col) min_col = col;
        if (col > max_col) max_col = col;

        /* neighbours: (row-1,col),(row+1,col),(row,col-1),(row,col+1) */
        if (row > 0) {
            int n = p - SHASH_SEG;
            if (!visited[n] && thresh[n] == target) { visited[n] = 1; queue[tail++] = n; }
        }
        if (row < SHASH_SEG - 1) {
            int n = p + SHASH_SEG;
            if (!visited[n] && thresh[n] == target) { visited[n] = 1; queue[tail++] = n; }
        }
        if (col > 0) {
            int n = p - 1;
            if (!visited[n] && thresh[n] == target) { visited[n] = 1; queue[tail++] = n; }
        }
        if (col < SHASH_SEG - 1) {
            int n = p + 1;
            if (!visited[n] && thresh[n] == target) { visited[n] = 1; queue[tail++] = n; }
        }
    }

    box->min_row = min_row;
    box->max_row = max_row;
    box->min_col = min_col;
    box->max_col = max_col;
    return size;
}

/* _find_all_segments: hill regions (threshold class 1) then valley regions
 * (class 0). Returns the number of kept segments (> min_segment_size),
 * writing their bounding boxes into `out`. Replicates the valley loop's
 * exact (loose) termination condition on the size of imagehash's
 * `already_segmented` set -- see NEXT_STEPS for why that set's cardinality
 * grows by the component size for components of >= 2 pixels and by 0 for
 * singletons. */
static int find_all_segments(const uint8_t *thresh, uint8_t *visited,
                             int *queue, seg_bbox *out) {
    int nseg = 0;
    seg_bbox box;

    /* already_segmented starts as the border ring outside the image:
     * 2*width + 2*height distinct phantom pixels. */
    long already_seg_count = 2L * SHASH_SEG + 2L * SHASH_SEG;

    /* Hill pass: all bright connected components, in row-major start
     * order. No count condition (imagehash uses `while (...).any()`). */
    for (int i = 0; i < SHASH_SEG_PIXELS; i++) {
        if (thresh[i] == 1 && !visited[i]) {
            int size = flood_fill(thresh, visited, queue, i, 1, &box);
            already_seg_count += (size >= 2) ? size : 0;
            if (size > SHASH_MIN_SEGMENT && nseg < SHASH_MAX_SEGMENTS) {
                out[nseg++] = box;
            }
        }
    }

    /* Valley pass: dark components, but bounded by the same
     * `len(already_segmented) < width*height` condition imagehash uses --
     * which can stop before every dark pixel is claimed. A forward cursor
     * is valid because a dark unvisited pixel is only ever left behind if
     * the condition already tripped. */
    int cursor = 0;
    while (already_seg_count < SHASH_SEG_PIXELS) {
        while (cursor < SHASH_SEG_PIXELS && (thresh[cursor] != 0 || visited[cursor])) {
            cursor++;
        }
        if (cursor == SHASH_SEG_PIXELS) {
            break; /* no dark pixels left; imagehash would IndexError here,
                      which only happens for >1200 singleton regions -- not
                      reachable after Gaussian+median smoothing. */
        }
        int size = flood_fill(thresh, visited, queue, cursor, 0, &box);
        already_seg_count += (size >= 2) ? size : 0;
        if (size > SHASH_MIN_SEGMENT && nseg < SHASH_MAX_SEGMENTS) {
            out[nseg++] = box;
        }
    }

    return nseg;
}

int shash_from_file(const char *path, shash_t *out_hash) {
    if (!path || !out_hash) {
        return -1;
    }

    int rc = 0;
    int width, height, source_channels;
    unsigned char *pixels = stbi_load(path, &width, &height, &source_channels, SHASH_CHANNELS);
    if (!pixels) {
        return -2;
    }

    uint8_t *l_full = NULL, *seg = NULL, *median = NULL, *thresh = NULL;
    uint8_t *visited = NULL, *crop = NULL;
    int *queue = NULL;
    seg_bbox *boxes = NULL;

    l_full = malloc((size_t)width * height);
    seg = malloc(SHASH_SEG_PIXELS);
    median = malloc(SHASH_SEG_PIXELS);
    thresh = malloc(SHASH_SEG_PIXELS);
    visited = calloc(SHASH_SEG_PIXELS, 1);
    queue = malloc(SHASH_SEG_PIXELS * sizeof(int));
    boxes = malloc(SHASH_MAX_SEGMENTS * sizeof(seg_bbox));
    crop = malloc((size_t)width * height); /* max possible crop == whole image */
    if (!l_full || !seg || !median || !thresh || !visited || !queue || !boxes || !crop) {
        rc = -3;
        goto cleanup;
    }

    /* Single grayscale conversion of the full-resolution original. Both
     * the segmentation input and every per-segment crop derive from it:
     * segmentation resizes it to 300x300, and cropping-then-converting-L
     * equals converting-L-then-cropping pixel for pixel (crop is a pure
     * rectangular selection, L is per-pixel; out-of-bounds crop pixels are
     * 0 either way), so imagehash's "crop the RGB original, then dhash
     * converts to L" collapses to cropping this L plane. */
    pil_rgb_to_l(pixels, width, height, l_full);
    stbi_image_free(pixels);
    pixels = NULL;

    /* image.convert('L').resize((300,300), LANCZOS) */
    rc = pil_resize_lanczos_l(l_full, width, height, seg, SHASH_SEG, SHASH_SEG);
    if (rc != 0) {
        goto cleanup;
    }

    /* .filter(GaussianBlur()).filter(MedianFilter()) */
    rc = pil_gaussian_blur_l(seg, SHASH_SEG, SHASH_SEG, SHASH_GAUSSIAN_RADIUS, SHASH_GAUSSIAN_PASSES);
    if (rc != 0) {
        goto cleanup;
    }
    rc = pil_median3_l(seg, SHASH_SEG, SHASH_SEG, median);
    if (rc != 0) {
        goto cleanup;
    }

    /* pixels > segment_threshold (float32 cast is a no-op vs uint8 > 128) */
    for (int i = 0; i < SHASH_SEG_PIXELS; i++) {
        thresh[i] = median[i] > SHASH_THRESHOLD ? 1 : 0;
    }

    int nseg = find_all_segments(thresh, visited, queue, boxes);

    /* Fallback: no segments -> one segment spanning {(0,0),(299,299)},
     * whose bounding box is the whole 300x300 area. */
    if (nseg == 0) {
        boxes[0].min_row = 0;
        boxes[0].min_col = 0;
        boxes[0].max_row = SHASH_SEG - 1;
        boxes[0].max_col = SHASH_SEG - 1;
        nseg = 1;
    }

    double scale_w = (double)width / SHASH_SEG;
    double scale_h = (double)height / SHASH_SEG;

    out_hash->count = nseg;
    for (int s = 0; s < nseg; s++) {
        const seg_bbox *b = &boxes[s];
        /* coord[0]=row=y scaled by scale_h; coord[1]=col=x by scale_w;
         * max side +1; then Image._crop's map(int, map(round, box)). */
        int x0 = pil_py_round((double)b->min_col * scale_w);
        int y0 = pil_py_round((double)b->min_row * scale_h);
        int x1 = pil_py_round((double)(b->max_col + 1) * scale_w);
        int y1 = pil_py_round((double)(b->max_row + 1) * scale_h);

        int cw = x1 - x0 > 0 ? x1 - x0 : 0;
        int ch = y1 - y0 > 0 ? y1 - y0 : 0;
        pil_crop_l(l_full, width, height, x0, y0, x1, y1, crop);

        dhash_t h = 0;
        rc = dhash_from_l_pixels(crop, cw, ch, &h);
        if (rc != 0) {
            goto cleanup;
        }
        out_hash->segment_hashes[s] = h;
    }
    rc = 0;

cleanup:
    if (pixels) stbi_image_free(pixels);
    free(l_full);
    free(seg);
    free(median);
    free(thresh);
    free(visited);
    free(queue);
    free(boxes);
    free(crop);
    return rc;
}

/* ---------------------------------------------------------------------
 * Comparison (ImageMultiHash matching logic)
 * ------------------------------------------------------------------- */

static int hamming64(dhash_t a, dhash_t b) {
    dhash_t x = a ^ b;
    int d = 0;
    while (x) {
        x &= (x - 1);
        d++;
    }
    return d;
}

void shash_hash_diff(const shash_t *a, const shash_t *b, double hamming_cutoff,
                     int *out_matches, int *out_sum_distance) {
    int matches = 0, sum = 0;
    for (int i = 0; i < a->count; i++) {
        int lowest = 65; /* > any 64-bit Hamming distance */
        for (int j = 0; j < b->count; j++) {
            int d = hamming64(a->segment_hashes[i], b->segment_hashes[j]);
            if (d < lowest) {
                lowest = d;
            }
        }
        if ((double)lowest > hamming_cutoff) {
            continue;
        }
        matches++;
        sum += lowest;
    }
    *out_matches = matches;
    *out_sum_distance = sum;
}

int shash_matches(const shash_t *a, const shash_t *b) {
    int matches, sum;
    shash_hash_diff(a, b, SHASH_HAMMING_CUTOFF, &matches, &sum);
    return matches >= 1;
}

double shash_sub(const shash_t *a, const shash_t *b) {
    int matches, sum;
    shash_hash_diff(a, b, SHASH_HAMMING_CUTOFF, &matches, &sum);

    int max_difference = a->count;
    if (matches == 0) {
        return (double)max_difference;
    }
    double max_distance = (double)matches * 64.0; /* len(segment_hashes[0]) */
    double tie_breaker = 0.0 - ((double)sum / max_distance);
    double match_score = (double)matches + tie_breaker;
    return (double)max_difference - match_score;
}

double shash_paper_distance(const shash_t *a, const shash_t *b) {
    long total = 0;
    for (int i = 0; i < a->count; i++) {
        int lowest = 65;
        for (int j = 0; j < b->count; j++) {
            int d = hamming64(a->segment_hashes[i], b->segment_hashes[j]);
            if (d < lowest) {
                lowest = d;
            }
        }
        total += lowest;
    }
    return (double)total / (double)a->count;
}
