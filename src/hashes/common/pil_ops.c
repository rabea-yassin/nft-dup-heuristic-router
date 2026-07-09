#include "pil_ops.h"

#include <stdlib.h>
#include <string.h>

/* -std=c11 (CMAKE_C_EXTENSIONS OFF) does not expose M_PI from math.h; the
 * sinc/lanczos kernels below need it. Pillow's own value is the standard
 * double literal, so define it identically when the platform header omits
 * it under strict C. */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ---------------------------------------------------------------------
 * Grayscale conversion (Convert.c)
 * ------------------------------------------------------------------- */

void pil_rgb_to_l(const uint8_t *rgb, int width, int height, uint8_t *out_l) {
    long total = (long)width * (long)height;
    for (long i = 0; i < total; i++) {
        const uint8_t *p = rgb + (size_t)i * 3;
        out_l[i] = pil_rgb_to_l_pixel(p[0], p[1], p[2]);
    }
}

/* ---------------------------------------------------------------------
 * LANCZOS resampling (Resample.c)
 *
 * Port notes, matching Pillow 10.4.0 statement for statement:
 *  - coefficients are computed in double, then quantized to int32 fixed
 *    point with PRECISION_BITS = 32 - 8 - 2 = 22 (Pillow reuses the
 *    double buffer for the int32s; we use a separate buffer to avoid the
 *    aliasing, which changes nothing about the values);
 *  - each output pixel starts from a rounding bias of 1 << 21 and is
 *    finished with an arithmetic shift and an [0,255] clamp (Pillow's
 *    clip8 lookup table, valid on [-640,639], is exactly that);
 *  - the horizontal pass runs first, the vertical pass second, and a
 *    pass whose output size equals its input size is skipped entirely
 *    (Resample.c's need_horizontal/need_vertical), so a same-size
 *    "resize" degenerates to a copy just like Image.resize's shortcut.
 * Pillow's horizontal pass only materializes the row window the vertical
 * pass will read (ybox_first/ybox_last); we materialize all rows, which
 * reads identical inputs per output pixel and only differs in what extra
 * rows get computed and thrown away.
 * ------------------------------------------------------------------- */

#define PIL_PRECISION_BITS (32 - 8 - 2)
#define PIL_LANCZOS_SUPPORT 3.0

static double pil_sinc_filter(double x) {
    if (x == 0.0) {
        return 1.0;
    }
    x = x * M_PI;
    return sin(x) / x;
}

/* Note the asymmetric interval (-3.0 <= x < 3.0), verbatim from Pillow. */
static double pil_lanczos_filter(double x) {
    if (-3.0 <= x && x < 3.0) {
        return pil_sinc_filter(x) * pil_sinc_filter(x / 3);
    }
    return 0.0;
}

static uint8_t pil_clip8(int in) {
    int v = in >> PIL_PRECISION_BITS; /* arithmetic shift, like Pillow */
    if (v < 0) {
        return 0;
    }
    if (v > 255) {
        return 255;
    }
    return (uint8_t)v;
}

/* Resample.c precompute_coeffs + normalize_coeffs_8bpc for the full-image
 * box (in0 = 0, in1 = in_size -- resize() never passes a sub-box in the
 * pipelines we port). Writes int32 fixed-point coefficients and per-output
 * [xmin, count] bounds. Returns ksize, or 0 on allocation failure. */
static int pil_precompute_coeffs(int in_size, int out_size,
                                 int **bounds_out, int32_t **kk_out) {
    double support, scale, filterscale;
    double center, ww, ss;
    int xx, x, ksize, xmin, xmax;
    int *bounds;
    double *prekk;
    double *k;
    int32_t *kk;

    filterscale = scale = (double)in_size / out_size;
    if (filterscale < 1.0) {
        filterscale = 1.0;
    }

    support = PIL_LANCZOS_SUPPORT * filterscale;
    ksize = (int)ceil(support) * 2 + 1;

    prekk = malloc((size_t)out_size * ksize * sizeof(double));
    bounds = malloc((size_t)out_size * 2 * sizeof(int));
    kk = malloc((size_t)out_size * ksize * sizeof(int32_t));
    if (!prekk || !bounds || !kk) {
        free(prekk);
        free(bounds);
        free(kk);
        return 0;
    }

    for (xx = 0; xx < out_size; xx++) {
        center = (xx + 0.5) * scale;
        ww = 0.0;
        ss = 1.0 / filterscale;
        /* (int) truncation of value+0.5, verbatim ("Round the value") */
        xmin = (int)(center - support + 0.5);
        if (xmin < 0) {
            xmin = 0;
        }
        xmax = (int)(center + support + 0.5);
        if (xmax > in_size) {
            xmax = in_size;
        }
        xmax -= xmin;
        k = &prekk[(size_t)xx * ksize];
        for (x = 0; x < xmax; x++) {
            double w = pil_lanczos_filter((x + xmin - center + 0.5) * ss);
            k[x] = w;
            ww += w;
        }
        for (x = 0; x < xmax; x++) {
            if (ww != 0.0) {
                k[x] /= ww;
            }
        }
        for (; x < ksize; x++) {
            k[x] = 0;
        }
        bounds[xx * 2 + 0] = xmin;
        bounds[xx * 2 + 1] = xmax;
    }

    /* normalize_coeffs_8bpc */
    for (x = 0; x < out_size * ksize; x++) {
        if (prekk[x] < 0) {
            kk[x] = (int)(-0.5 + prekk[x] * (1 << PIL_PRECISION_BITS));
        } else {
            kk[x] = (int)(0.5 + prekk[x] * (1 << PIL_PRECISION_BITS));
        }
    }
    free(prekk);

    *bounds_out = bounds;
    *kk_out = kk;
    return ksize;
}

int pil_resize_lanczos_l(const uint8_t *in, int in_w, int in_h,
                         uint8_t *out, int out_w, int out_h) {
    const uint8_t *vsrc = in; /* what the vertical pass reads */
    uint8_t *htemp = NULL;
    int *bounds = NULL;
    int32_t *kk = NULL;
    int ksize;

    if (in_w == out_w && in_h == out_h) {
        memcpy(out, in, (size_t)in_w * in_h);
        return 0;
    }

    /* horizontal pass (skipped when widths match, like need_horizontal) */
    if (in_w != out_w) {
        htemp = malloc((size_t)out_w * in_h > 0 ? (size_t)out_w * in_h : 1);
        if (!htemp) {
            return -3;
        }
        ksize = pil_precompute_coeffs(in_w, out_w, &bounds, &kk);
        if (!ksize) {
            free(htemp);
            return -3;
        }
        for (int yy = 0; yy < in_h; yy++) {
            const uint8_t *row = in + (size_t)yy * in_w;
            for (int xx = 0; xx < out_w; xx++) {
                int xmin = bounds[xx * 2 + 0];
                int xmax = bounds[xx * 2 + 1];
                const int32_t *k = &kk[(size_t)xx * ksize];
                int ss0 = 1 << (PIL_PRECISION_BITS - 1);
                for (int x = 0; x < xmax; x++) {
                    ss0 += row[x + xmin] * k[x];
                }
                htemp[(size_t)yy * out_w + xx] = pil_clip8(ss0);
            }
        }
        free(bounds);
        free(kk);
        vsrc = htemp;
    }

    /* vertical pass (skipped when heights match, like need_vertical) */
    if (in_h != out_h) {
        ksize = pil_precompute_coeffs(in_h, out_h, &bounds, &kk);
        if (!ksize) {
            free(htemp);
            return -3;
        }
        for (int yy = 0; yy < out_h; yy++) {
            const int32_t *k = &kk[(size_t)yy * ksize];
            int ymin = bounds[yy * 2 + 0];
            int ymax = bounds[yy * 2 + 1];
            for (int xx = 0; xx < out_w; xx++) {
                int ss0 = 1 << (PIL_PRECISION_BITS - 1);
                for (int y = 0; y < ymax; y++) {
                    ss0 += vsrc[(size_t)(y + ymin) * out_w + xx] * k[y];
                }
                out[(size_t)yy * out_w + xx] = pil_clip8(ss0);
            }
        }
        free(bounds);
        free(kk);
    } else {
        memcpy(out, vsrc, (size_t)out_w * out_h);
    }

    free(htemp);
    return 0;
}

/* ---------------------------------------------------------------------
 * Gaussian blur via repeated box blurs (BoxBlur.c)
 * ------------------------------------------------------------------- */

/* BoxBlur.c _gaussian_blur_radius, literal for literal. The variables are
 * float, but L and l pass through double-precision intermediates (the
 * unsuffixed 12.0/1.0/2.0 literals) and round to float only at each
 * assignment, while sigma2 and a are computed at float precision (their
 * int literals promote to float, not double). Same class of trap that
 * made hsvHash's rgb2hsv port subtly wrong at first -- keep the mixed
 * precision exactly as written. */
static float pil_gaussian_box_radius(float radius, int passes) {
    float sigma2, L, l, a;

    sigma2 = radius * radius / passes;
    L = sqrt(12.0 * sigma2 + 1.0);
    l = floor((L - 1.0) / 2.0);
    a = (2 * l + 1) * (l * (l + 1) - 3 * sigma2);
    a /= 6 * (sigma2 - (l + 1) * (l + 1));

    return l + a;
}

/* BoxBlur.c ImagingLineBoxBlur8: one 1D pass over one line. The window
 * [x-radius, x+radius] gets full weight ww, the two pixels just outside
 * it get fractional weight fw (that's how a non-integer box radius is
 * realized), and out-of-line indices clamp to the first/last pixel.
 * Unsigned wraparound in MOVE_ACC is intentional and matches Pillow. */
static void pil_box_blur_line(uint8_t *line_out, const uint8_t *line_in,
                              int lastx, int radius, int edge_a, int edge_b,
                              uint32_t ww, uint32_t fw) {
    int x;
    uint32_t acc;
    uint32_t bulk;

#define PIL_MOVE_ACC(acc, subtract, add) \
    acc += line_in[add] - line_in[subtract];

#define PIL_ADD_FAR(bulk, acc, left, right) \
    bulk = (acc * ww) + (line_in[left] + line_in[right]) * fw;

#define PIL_SAVE(x, bulk) \
    line_out[x] = (uint8_t)((bulk + (1 << 23)) >> 24)

    /* Accumulator for the window centered one pixel left of the line:
     * the first pixel repeated radius+1 times, then pixels [0, edge_a-1),
     * then the last pixel repeated for whatever the window still needs
     * (only when radius reaches past the line's end). */
    acc = line_in[0] * (radius + 1);
    for (x = 0; x < edge_a - 1; x++) {
        acc += line_in[x];
    }
    acc += line_in[lastx] * (radius - edge_a + 1);

    if (edge_a <= edge_b) {
        for (x = 0; x < edge_a; x++) {
            PIL_MOVE_ACC(acc, 0, x + radius);
            PIL_ADD_FAR(bulk, acc, 0, x + radius + 1);
            PIL_SAVE(x, bulk);
        }
        for (x = edge_a; x < edge_b; x++) {
            PIL_MOVE_ACC(acc, x - radius - 1, x + radius);
            PIL_ADD_FAR(bulk, acc, x - radius - 1, x + radius + 1);
            PIL_SAVE(x, bulk);
        }
        for (x = edge_b; x <= lastx; x++) {
            PIL_MOVE_ACC(acc, x - radius - 1, lastx);
            PIL_ADD_FAR(bulk, acc, x - radius - 1, lastx);
            PIL_SAVE(x, bulk);
        }
    } else {
        for (x = 0; x < edge_b; x++) {
            PIL_MOVE_ACC(acc, 0, x + radius);
            PIL_ADD_FAR(bulk, acc, 0, x + radius + 1);
            PIL_SAVE(x, bulk);
        }
        for (x = edge_b; x < edge_a; x++) {
            PIL_MOVE_ACC(acc, 0, lastx);
            PIL_ADD_FAR(bulk, acc, 0, lastx);
            PIL_SAVE(x, bulk);
        }
        for (x = edge_a; x <= lastx; x++) {
            PIL_MOVE_ACC(acc, x - radius - 1, lastx);
            PIL_ADD_FAR(bulk, acc, x - radius - 1, lastx);
            PIL_SAVE(x, bulk);
        }
    }

#undef PIL_MOVE_ACC
#undef PIL_ADD_FAR
#undef PIL_SAVE
}

int pil_gaussian_blur_l(uint8_t *img, int width, int height,
                        float radius, int passes) {
    float box_radius = pil_gaussian_box_radius(radius, passes);

    /* ImagingHorizontalBoxBlur's weight setup, including the mixed
     * unsigned/float arithmetic: ww divides an exact 1<<24 by the
     * fractional window width in float, fw distributes the remainder to
     * the two fractional edge pixels in integer math. */
    int box_int = (int)box_radius;
    uint32_t ww = (uint32_t)((uint32_t)(1 << 24) / (box_radius * 2 + 1));
    uint32_t fw = ((uint32_t)(1 << 24) - (uint32_t)(box_int * 2 + 1) * ww) / 2;

    int longest = width > height ? width : height;
    uint8_t *t0 = malloc((size_t)longest);
    uint8_t *t1 = malloc((size_t)longest);
    if (!t0 || !t1) {
        free(t0);
        free(t1);
        return -3;
    }

    /* Pillow runs `passes` whole-image x blurs, then transposes and runs
     * `passes` whole-image y blurs. Each 1D pass touches every line
     * independently, so running all passes of one line back to back
     * produces byte-identical output without materializing the
     * intermediate images or the transpose. */
    if (box_radius != 0) { /* xradius/yradius are equal here; Pillow
                              skips an axis whose radius is 0 */
        int edge_a = (box_int + 1) < width ? (box_int + 1) : width;
        int edge_b = (width - box_int - 1) > 0 ? (width - box_int - 1) : 0;
        for (int y = 0; y < height; y++) {
            uint8_t *row = img + (size_t)y * width;
            memcpy(t0, row, (size_t)width);
            for (int pass = 0; pass < passes; pass++) {
                pil_box_blur_line(t1, t0, width - 1, box_int, edge_a, edge_b, ww, fw);
                uint8_t *swap = t0;
                t0 = t1;
                t1 = swap;
            }
            memcpy(row, t0, (size_t)width);
        }

        edge_a = (box_int + 1) < height ? (box_int + 1) : height;
        edge_b = (height - box_int - 1) > 0 ? (height - box_int - 1) : 0;
        for (int x = 0; x < width; x++) {
            for (int y = 0; y < height; y++) {
                t0[y] = img[(size_t)y * width + x];
            }
            for (int pass = 0; pass < passes; pass++) {
                pil_box_blur_line(t1, t0, height - 1, box_int, edge_a, edge_b, ww, fw);
                uint8_t *swap = t0;
                t0 = t1;
                t1 = swap;
            }
            for (int y = 0; y < height; y++) {
                img[(size_t)y * width + x] = t0[y];
            }
        }
    }

    free(t0);
    free(t1);
    return 0;
}

/* ---------------------------------------------------------------------
 * 3x3 median filter (ImageFilter.MedianFilter -> ImagingExpand +
 * ImagingRankFilter with size=3, rank=4)
 * ------------------------------------------------------------------- */

int pil_median3_l(const uint8_t *in, int width, int height, uint8_t *out) {
    int ew = width + 2;
    int eh = height + 2;
    uint8_t *expanded = malloc((size_t)ew * eh);
    if (!expanded) {
        return -3;
    }

    /* ImagingExpand: xmargin = ymargin = 3 // 2 = 1, edge-replicated */
    for (int y = 0; y < eh; y++) {
        int sy = y - 1;
        if (sy < 0) {
            sy = 0;
        }
        if (sy > height - 1) {
            sy = height - 1;
        }
        const uint8_t *srow = in + (size_t)sy * width;
        uint8_t *drow = expanded + (size_t)y * ew;
        drow[0] = srow[0];
        memcpy(drow + 1, srow, (size_t)width);
        drow[ew - 1] = srow[width - 1];
    }

    /* Rank filter, rank = 3*3//2 = 4. Pillow uses Wirth's selection; any
     * exact k-th order statistic is value-identical, so a 9-element
     * insertion sort is used here. */
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
            uint8_t win[9];
            int n = 0;
            for (int i = 0; i < 3; i++) {
                const uint8_t *erow = expanded + (size_t)(y + i) * ew + x;
                win[n++] = erow[0];
                win[n++] = erow[1];
                win[n++] = erow[2];
            }
            for (int i = 1; i < 9; i++) {
                uint8_t v = win[i];
                int j = i - 1;
                while (j >= 0 && win[j] > v) {
                    win[j + 1] = win[j];
                    j--;
                }
                win[j + 1] = v;
            }
            out[(size_t)y * width + x] = win[4];
        }
    }

    free(expanded);
    return 0;
}

/* ---------------------------------------------------------------------
 * Crop (Crop.c ImagingCrop): zero-fill + paste of the overlapping region
 * ------------------------------------------------------------------- */

void pil_crop_l(const uint8_t *in, int width, int height,
                int x0, int y0, int x1, int y1, uint8_t *out) {
    int out_w = x1 - x0 > 0 ? x1 - x0 : 0;
    int out_h = y1 - y0 > 0 ? y1 - y0 : 0;
    if (out_w == 0 || out_h == 0) {
        return;
    }

    if (x0 < 0 || y0 < 0 || x1 > width || y1 > height) {
        memset(out, 0, (size_t)out_w * out_h);
    }

    int sx0 = x0 > 0 ? x0 : 0;
    int sy0 = y0 > 0 ? y0 : 0;
    int sx1 = x1 < width ? x1 : width;
    int sy1 = y1 < height ? y1 : height;
    if (sx1 <= sx0 || sy1 <= sy0) {
        return; /* no overlap with the source at all */
    }

    for (int y = sy0; y < sy1; y++) {
        memcpy(out + (size_t)(y - y0) * out_w + (sx0 - x0),
               in + (size_t)y * width + sx0,
               (size_t)(sx1 - sx0));
    }
}
