# common — shared hash helpers

Two independent helper modules shared across the hashes. They are kept
separate on purpose: one is a fast approximate downsample, the other is a
suite of bit-exact Pillow reimplementations, and conflating them would
put a downscale-only box filter where real interpolation is required.

## `hashes_common.h` / `.c` — box-filter grayscale downsample

`hashes_grayscale_box_downsample(path, out_dim, out_cells)`: decodes an
image (stb, forced to RGB) and accumulates a single-pass grayscale
(ITU-R BT.601 luma) **box-filter downsample** to an `out_dim × out_dim`
grid of cell means.

Used by [`ahash`](../ahash) (8×8) and [`phash`](../phash) (32×32). It is a
downscale-only averaging filter — correct for those two, which only ever
shrink the image, and cheap (no per-pixel interpolation).

## `pil_ops.h` / `.c` — bit-exact Pillow 10.4.0 op ports

The operations `imagehash`'s dHash / crop_resistant_hash pipelines run
through, reimplemented to reproduce Pillow byte-for-byte:

| Function | Pillow source |
|----------|---------------|
| `pil_rgb_to_l` | `Convert.c` (BT.601 fixed-point, round-to-nearest) |
| `pil_resize_lanczos_l` | `Resample.c` (LANCZOS, `precompute_coeffs` + 22-bit fixed-point 8bpc resampler) |
| `pil_gaussian_blur_l` | `BoxBlur.c` (`ImagingGaussianBlur` = repeated box blurs) |
| `pil_median3_l` | `Filter.c` `ImagingExpand` + `RankFilter.c` (3×3 median, edge-replicated) |
| `pil_crop_l` | `Crop.c` (zero-filled integer crop) |
| `pil_py_round` | Python `round()` (half-to-even) for `Image._crop`'s box rounding |

Used by [`dhash`](../dhash) and [`shash`](../shash). These resizes are
frequently **upscales** (dHash 9×8, sHash 300×300 vs our 256×256 data), so
the box-filter helper above cannot be reused here — real interpolation is
required. Read from Pillow's actual C source rather than assumed.

**Trap:** `pil_gaussian_box_radius` (Pillow's `_gaussian_blur_radius`)
mixes `float` locals with unsuffixed `double` literals, so it must be
replicated at that exact mixed precision — the same float/double lesson
[`hsvhash`](../hsvhash) documents.
