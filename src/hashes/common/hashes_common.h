#ifndef NFT_HASHES_COMMON_H
#define NFT_HASHES_COMMON_H

/* Decodes the image at `path` via stb_image (forced to RGB -- alpha is
 * dropped, not composited, matching PIL's Image.convert("L") on RGBA
 * input) and accumulates a single-pass grayscale box-filter downsample
 * (ITU-R BT.601 luma) directly into an out_dim x out_dim grid of cell
 * means, row-major in `out_cells`.
 *
 * `out_cells` must have room for out_dim*out_dim doubles; callers own that
 * buffer (a fixed-size stack array), so this never allocates beyond the
 * stb_image decode itself.
 *
 * Returns 0 on success, -1 on bad arguments, -2 if the image could not be
 * decoded. */
int hashes_grayscale_box_downsample(const char *path, int out_dim, double *out_cells);

#endif /* NFT_HASHES_COMMON_H */
