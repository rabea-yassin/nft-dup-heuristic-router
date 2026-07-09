#ifndef NFT_HASHES_PIL_OPS_H
#define NFT_HASHES_PIL_OPS_H

#include <math.h>
#include <stdint.h>

/* Bit-exact C ports of the Pillow (10.4.0) image operations that
 * imagehash's dhash()/crop_resistant_hash() pipelines run through:
 * grayscale conversion, LANCZOS resampling, GaussianBlur's box-blur
 * approximation, the 3x3 median filter, and integer crop. Each function
 * documents the Pillow source file it replicates. They exist because
 * sHash's segmentation output feeds through several of these back to
 * back, so any single almost-right filter breaks hash parity end to end.
 *
 * All images here are single-channel 8-bit grayscale ("L" mode), row-major,
 * width*height bytes, matching Pillow's image8 representation. */

/* PIL Convert.c rgb2l (the L24 macro): 16-bit fixed-point ITU-R BT.601
 * luma with round-to-nearest. Same conversion hsvhash.c uses, verified
 * bit-exact against a real Pillow build. Alpha, if the source had any, is
 * dropped before this (stb decode forced to RGB), matching PIL's
 * convert("L") on RGBA/P input. */
static inline uint8_t pil_rgb_to_l_pixel(uint8_t r, uint8_t g, uint8_t b) {
    int32_t l24 = (int32_t)r * 19595 + (int32_t)g * 38470 + (int32_t)b * 7471 + 0x8000;
    return (uint8_t)(l24 >> 16);
}

/* Convert a packed RGB buffer (3 bytes/pixel) to an L buffer. */
void pil_rgb_to_l(const uint8_t *rgb, int width, int height, uint8_t *out_l);

/* Python's round() (round-half-to-even), as used by Image.py's _crop on
 * float crop boxes: `map(int, map(round, box))`. nearbyint() rounds
 * half-to-even under the default FE_TONEAREST mode, which this project
 * never changes. */
static inline int pil_py_round(double v) {
    return (int)nearbyint(v);
}

/* Pillow Resample.c, LANCZOS filter, 8-bit single-band path: two-pass
 * (horizontal, then vertical) separable resampling with double-precision
 * coefficients quantized to 22-bit fixed point. Handles up- and
 * downscaling; a same-size pass is skipped exactly like Pillow's
 * need_horizontal/need_vertical logic (so equal in/out size is a copy).
 * in_w/in_h may be 0 (a degenerate PIL crop): the output is then all
 * zeros, which is what Pillow computes for it too.
 * Returns 0 on success, -3 if a working allocation failed. */
int pil_resize_lanczos_l(const uint8_t *in, int in_w, int in_h,
                         uint8_t *out, int out_w, int out_h);

/* Pillow BoxBlur.c ImagingGaussianBlur: `passes` box blurs per axis at a
 * fractional box radius derived from the gaussian radius (all x passes,
 * then all y passes, matching ImagingBoxBlur's order). In place.
 * ImageFilter.GaussianBlur() defaults are radius=2, passes=3.
 * Returns 0 on success, -3 if a working allocation failed. */
int pil_gaussian_blur_l(uint8_t *img, int width, int height,
                        float radius, int passes);

/* Pillow ImageFilter.MedianFilter(size=3): ImagingExpand (edge-replicate
 * pad by 1) followed by RankFilter.c's rank filter with rank 4 -- i.e. a
 * 3x3 median with replicated borders. `out` must not alias `in`.
 * Returns 0 on success, -3 if a working allocation failed. */
int pil_median3_l(const uint8_t *in, int width, int height, uint8_t *out);

/* Pillow Crop.c ImagingCrop: copy [x0,x1)x[y0,y1) into `out`
 * ((x1-x0)*(y1-y0) bytes, dimensions clamped to >= 0); regions outside
 * the source are zero-filled, exactly like Pillow. Callers get the same
 * geometry semantics crop_resistant_hash sees. */
void pil_crop_l(const uint8_t *in, int width, int height,
                int x0, int y0, int x1, int y1, uint8_t *out);

#endif /* NFT_HASHES_PIL_OPS_H */
