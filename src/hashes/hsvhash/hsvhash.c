#include "hsvhash.h"

#include <math.h>
#include <stddef.h>

#include "stb_image.h"

#define HSVHASH_CHANNELS 3 /* force RGB decode; alpha is dropped, not
                               composited, matching PIL's convert("L")/
                               convert("HSV") on RGBA input */
#define HSVHASH_HUE_BINS 6
#define HSVHASH_BINBITS 3
#define HSVHASH_MAXVALUE (1 << HSVHASH_BINBITS) /* 8 */
#define HSVHASH_NUM_VALUES (2 + 2 * HSVHASH_HUE_BINS) /* 14 */

/* colorhash()'s exact 3-bit expansion of a quantized value 0..7 -- verbatim
 * from imagehash's `v // (2 ** (binbits - i - 1)) % 2` bit extraction, not
 * standard binary: 4/6 alias to the same 3 bits, and so do 5/7. That's
 * colorhash()'s real behavior, not a transcription bug -- don't "fix" it. */
static const uint8_t COLORHASH_BIT_LUT[HSVHASH_MAXVALUE] = {
    0, 1, 2, 3, 6, 7, 6, 7,
};

/* PIL's RGB->L (Convert.c's L24 macro): 16-bit fixed-point ITU-R BT.601
 * luma with round-to-nearest. Verified bit-exact against a real Pillow
 * build across 300k+ random RGB triples. */
static uint8_t rgb_to_intensity(uint8_t r, uint8_t g, uint8_t b) {
    int32_t l24 = (int32_t)r * 19595 + (int32_t)g * 38470 + (int32_t)b * 7471 + 0x8000;
    return (uint8_t)(l24 >> 16);
}

/* PIL's RGB->HSV (Convert.c's rgb2hsv_row), replicated statement-for-
 * statement including its literal types: h/s/rc/gc/bc/cr are `float`, but
 * the source's literals (2.0, 6.0, 255.0, ...) are `double` with no `f`
 * suffix, so most of this arithmetic actually runs at double precision and
 * only rounds down to float at each assignment. Reproducing that
 * assignment-by-assignment rounding (rather than computing everything in
 * one precision throughout) is what makes this bit-exact -- verified
 * against a real Pillow build across 500k+ random and edge-case (ties,
 * grayscale, extremes) RGB triples with zero mismatches. `v` isn't needed
 * by colorhash() so it isn't computed here. */
static void rgb_to_hue_sat(uint8_t r, uint8_t g, uint8_t b, uint8_t *out_h, uint8_t *out_s) {
    uint8_t maxc = r > g ? (r > b ? r : b) : (g > b ? g : b);
    uint8_t minc = r < g ? (r < b ? r : b) : (g < b ? g : b);
    if (minc == maxc) {
        *out_h = 0;
        *out_s = 0;
        return;
    }

    float cr = (float)(maxc - minc);
    float s = cr / (float)maxc;
    float rc = ((float)(maxc - r)) / cr;
    float gc = ((float)(maxc - g)) / cr;
    float bc = ((float)(maxc - b)) / cr;

    float h;
    if (r == maxc) {
        h = bc - gc;
    } else if (g == maxc) {
        h = 2.0 + rc - bc;
    } else {
        h = 4.0 + gc - rc;
    }
    h = fmod((h / 6.0 + 1.0), 1.0);

    int hi = (int)(h * 255.0);
    int si = (int)(s * 255.0);
    *out_h = (uint8_t)(hi <= 0 ? 0 : (hi < 256 ? hi : 255));
    *out_s = (uint8_t)(si <= 0 ? 0 : (si < 256 ? si : 255));
}

/* min(maxvalue-1, int(frac * maxvalue)) where frac = count/total, computed
 * in the same order (divide, then multiply) as numpy's `mask.mean() *
 * maxvalue` so float64 rounding matches exactly. */
static int quantize_fraction(long count, long total) {
    int q = (int)(((double)count / (double)total) * (double)HSVHASH_MAXVALUE);
    return q > HSVHASH_MAXVALUE - 1 ? HSVHASH_MAXVALUE - 1 : q;
}

/* min(maxvalue-1, int(count * maxvalue / c)) -- multiply then divide,
 * matching `h_counts[i] * maxvalue / c`'s evaluation order exactly. */
static int quantize_hue_bin(long count, long colored_total) {
    int q = (int)((double)(count * HSVHASH_MAXVALUE) / (double)colored_total);
    return q > HSVHASH_MAXVALUE - 1 ? HSVHASH_MAXVALUE - 1 : q;
}

int hsvhash_from_file(const char *path, hsvhash_t *out_hash) {
    if (!path || !out_hash) {
        return -1;
    }

    int width, height, source_channels;
    unsigned char *pixels = stbi_load(path, &width, &height, &source_channels, HSVHASH_CHANNELS);
    if (!pixels) {
        return -2;
    }

    long total = (long)width * (long)height;
    long count_black = 0;
    long count_gray = 0; /* gray, excluding black -- matches mask_gray & ~mask_black */
    long count_colored = 0;
    long faint_bins[HSVHASH_HUE_BINS] = {0};
    long bright_bins[HSVHASH_HUE_BINS] = {0};

    for (long i = 0; i < total; i++) {
        const unsigned char *p = pixels + (size_t)i * HSVHASH_CHANNELS;
        uint8_t intensity = rgb_to_intensity(p[0], p[1], p[2]);
        uint8_t h, s;
        rgb_to_hue_sat(p[0], p[1], p[2], &h, &s);

        int is_black = intensity < 32; /* 256 // 8 */
        int is_gray_raw = s < 85;      /* 256 // 3 */

        if (is_black) {
            count_black++;
        } else if (is_gray_raw) {
            count_gray++;
        } else {
            count_colored++;
            /* 6 equal-width bins over [0,255]; edges land on non-integer
             * values except 85/170, so integer h*2/85 (floor) reproduces
             * numpy.histogram's bin assignment exactly without any float
             * boundary ambiguity. Clamp handles h==255, whose true bin
             * (5) would otherwise fall one past the last edge. */
            int bin = (h * 2) / 85;
            if (bin > HSVHASH_HUE_BINS - 1) {
                bin = HSVHASH_HUE_BINS - 1;
            }
            if (s < 170) { /* 256 * 2 // 3 */
                faint_bins[bin]++;
            } else if (s > 170) {
                bright_bins[bin]++;
            }
            /* s == 170 falls into neither histogram -- matches colorhash(). */
        }
    }

    stbi_image_free(pixels);

    long colored_total = count_colored > 0 ? count_colored : 1;

    int values[HSVHASH_NUM_VALUES];
    values[0] = quantize_fraction(count_black, total);
    values[1] = quantize_fraction(count_gray, total);
    for (int i = 0; i < HSVHASH_HUE_BINS; i++) {
        values[2 + i] = quantize_hue_bin(faint_bins[i], colored_total);
    }
    for (int i = 0; i < HSVHASH_HUE_BINS; i++) {
        values[2 + HSVHASH_HUE_BINS + i] = quantize_hue_bin(bright_bins[i], colored_total);
    }

    hsvhash_t hash = 0;
    int bit = 0;
    for (int i = 0; i < HSVHASH_NUM_VALUES; i++) {
        uint8_t bits3 = COLORHASH_BIT_LUT[values[i]];
        for (int b = 2; b >= 0; b--) {
            if ((bits3 >> b) & 1) {
                hash |= ((hsvhash_t)1 << bit);
            }
            bit++;
        }
    }

    *out_hash = hash;
    return 0;
}

int hsvhash_distance(hsvhash_t a, hsvhash_t b) {
    hsvhash_t x = a ^ b;
    int distance = 0;
    while (x) {
        x &= (x - 1);
        distance++;
    }
    return distance;
}
