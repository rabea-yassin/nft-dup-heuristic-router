"""Absolute, single-image features for the manipulation-type router.

The router predicts, from the QUERY IMAGE ALONE, which manipulation produced it
and how confident we are -- so the Phase D detector can drop signals known-broken
for that manipulation. That "alone" is the load-bearing constraint: at inference
we hold only the query, never a reference to diff against, so every feature here
is an ABSOLUTE descriptor of ONE image. Nothing relational.

The feature set is chosen for the signal-reliability decision (PROGRESS.md 5),
not for pretty multiclass accuracy. The two questions that actually drive Phase D
are "is detail intact?" (is ORB trustworthy? -- broken only by pixelation) and
"did colour change?" (is hsvHash noise? -- broken by colour edits). The groups
below are organised around exposing exactly those.

Resolution policy (deliberate, not incidental):
  * Structural features (sharpness, edges, the text-overlay grid) run on a gray
    image normalised to a common 256 px working edge -- the SAME normalisation
    ORB uses -- so "is detail intact?" reads the same regardless of whether the
    input is a 24 px punk, a 256 px azuki, or a 336 px reference punk.
  * Colour, histogram, palette and alpha features run on the NATIVE pixels.
    Resampling averages away the integer-quantisation "combing" a brightness or
    saturation edit leaves behind, and blends a pixelated image's collapsed
    palette back apart -- both are exactly the forensic traces we want to keep.

Handles RGBA (our data) and RGB (the authors' reference set) uniformly: an RGB
image converts to RGBA with a fully-opaque alpha, so the alpha/transparency
features simply read zero there. Note the transparent-corner feature is a
PIL-generator artifact of our own flip_rotate_mirror (it fills the corners a
non-axis rotation exposes with transparency); it is expected to collapse to ~0
on the reference set, which is precisely what the cross-dataset eval checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# Reuse ORB's resolution normalisation so the structural features read at the
# same working resolution the geometric signal does (NEAREST up for pixel art's
# hard edges, AREA down). One source of truth for "the working resolution".
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geometric"))
from orb_match import WORKING_EDGE_PX, normalize_for_orb  # noqa: E402

RGB_HIST_BINS = 16  # coarse per-channel histogram: gross colour-distribution shifts
GRID = 4  # NxN grid for the localized-high-edge (text/logo) detector


def _load_rgba(path: Path | str) -> np.ndarray:
    """Decode any image to an HxWx4 uint8 RGBA array (opaque alpha if no alpha)."""
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation, 0 when either channel is constant."""
    a = a.ravel().astype(np.float64)
    b = b.ravel().astype(np.float64)
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _combing(channel: np.ndarray) -> float:
    """Fraction of integer values missing *inside* a channel's occupied range.

    A natural photo fills almost every value between its min and max. A linear
    brightness/contrast/saturation remap on 8-bit integers skips values, leaving
    empty histogram bins interspersed with full ones -- "combing". High = a
    quantised colour edit; ~0 = untouched. Computed on native pixels because a
    resample would blur the gaps shut.
    """
    hist = np.bincount(channel.ravel(), minlength=256)
    occupied = np.nonzero(hist)[0]
    if occupied.size < 2:
        return 0.0
    lo, hi = occupied[0], occupied[-1]
    interior = hist[lo : hi + 1]
    return float(np.count_nonzero(interior == 0) / (hi - lo))


def features_from_rgba(rgba: np.ndarray) -> dict[str, float]:
    """The full ordered feature dict for one already-decoded RGBA image.

    Insertion order is stable across images (fixed bin counts), so the caller
    can take the header from any single row's keys.
    """
    h, w = rgba.shape[:2]
    rgb = rgba[:, :, :3].astype(np.uint8)
    alpha = rgba[:, :, 3]
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    gray_native = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray256 = normalize_for_orb(gray_native, WORKING_EDGE_PX)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)  # H:0-179, S/V:0-255
    sat, val = hsv[:, :, 1].astype(np.float64), hsv[:, :, 2].astype(np.float64)

    f: dict[str, float] = {}

    # --- Geometry / dimensions -------------------------------------------------
    # Native size is informative on its own: punks are tiny pixel art, and the
    # RF can condition scale-sensitive features on it.
    f["orig_width"] = float(w)
    f["orig_height"] = float(h)
    f["aspect_ratio"] = float(w / h) if h else 0.0
    f["log_area"] = float(np.log1p(w * h))

    # --- Transparency / alpha (rotation tell; ~0 for opaque or RGB input) ------
    transparent = alpha < 16
    f["alpha_mean"] = float(alpha.mean()) / 255.0
    f["alpha_std"] = float(alpha.std()) / 255.0
    f["transparent_frac"] = float(transparent.mean())
    # Corner-vs-centre transparency: a non-axis rotation leaves transparent
    # triangles in the corners while the subject stays opaque in the middle.
    ch, cw = max(1, h // 4), max(1, w // 4)
    corner = np.zeros((h, w), dtype=bool)
    corner[:ch, :cw] = corner[:ch, -cw:] = corner[-ch:, :cw] = corner[-ch:, -cw:] = True
    centre = np.zeros((h, w), dtype=bool)
    centre[h // 2 - ch : h // 2 + ch, w // 2 - cw : w // 2 + cw] = True
    corner_t = float(transparent[corner].mean()) if corner.any() else 0.0
    centre_t = float(transparent[centre].mean()) if centre.any() else 0.0
    f["corner_transparent_frac"] = corner_t
    f["corner_minus_centre_transparent"] = corner_t - centre_t

    # --- Detail / sharpness (normalised gray => "is detail intact?") -----------
    lap = cv2.Laplacian(gray256, cv2.CV_64F)
    f["lap_var"] = float(lap.var())
    gx = cv2.Sobel(gray256, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray256, cv2.CV_64F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    f["grad_mean"] = float(grad.mean())
    f["grad_std"] = float(grad.std())
    edges = cv2.Canny(gray256, 100, 200)
    f["edge_density"] = float((edges > 0).mean())

    # --- Blockiness / palette (native => pixelation collapses the palette) -----
    # Pixelation (NEAREST down-then-up) leaves flat blocks and few unique colours.
    diff_h = gray_native[:, 1:] != gray_native[:, :-1]
    diff_v = gray_native[1:, :] != gray_native[:-1, :]
    flat = 1.0 - (diff_h.sum() + diff_v.sum()) / (diff_h.size + diff_v.size)
    f["flat_neighbour_frac"] = float(flat)
    packed = (r.astype(np.uint32) << 16) | (g.astype(np.uint32) << 8) | b.astype(np.uint32)
    values, counts = np.unique(packed.ravel(), return_counts=True)
    n_pixels = packed.size
    f["unique_color_count"] = float(values.size)
    f["unique_color_ratio"] = float(values.size / n_pixels)
    order = np.sort(counts)[::-1]
    f["dominant_color_frac"] = float(order[0] / n_pixels)
    f["top5_color_frac"] = float(order[:5].sum() / n_pixels)

    # --- Colour statistics (native => a colour edit is a colour edit) ----------
    f["sat_mean"] = float(sat.mean()) / 255.0
    f["sat_std"] = float(sat.std()) / 255.0
    f["high_sat_frac"] = float((sat > 200).mean())
    f["low_sat_frac"] = float((sat < 40).mean())
    f["val_mean"] = float(val.mean()) / 255.0
    f["val_std"] = float(val.std()) / 255.0
    f["bright_frac"] = float((val > 220).mean())
    f["dark_frac"] = float((val < 35).mean())
    for name, chan in (("r", r), ("g", g), ("b", b)):
        f[f"{name}_mean"] = float(chan.mean()) / 255.0
        f[f"{name}_std"] = float(chan.std()) / 255.0
    # Channel correlations: channel-swap reassigns them, invert flips their sign.
    f["rg_corr"] = _corr(r, g)
    f["rb_corr"] = _corr(r, b)
    f["gb_corr"] = _corr(g, b)
    # Hasler-Susstrunk colourfulness: down for desaturate, up for saturate.
    rgd = r.astype(np.float64) - g
    ybd = 0.5 * (r.astype(np.float64) + g) - b
    f["colorfulness"] = float(
        np.sqrt(rgd.std() ** 2 + ybd.std() ** 2)
        + 0.3 * np.sqrt(rgd.mean() ** 2 + ybd.mean() ** 2)
    ) / 255.0

    # --- Combing (native, per channel) -----------------------------------------
    f["combing_r"] = _combing(r)
    f["combing_g"] = _combing(g)
    f["combing_b"] = _combing(b)

    # --- Coarse per-channel RGB histograms (native, normalised to fractions) ---
    for name, chan in (("r", r), ("g", g), ("b", b)):
        hist = np.bincount(
            (chan.ravel().astype(np.int32) * RGB_HIST_BINS) // 256, minlength=RGB_HIST_BINS
        ).astype(np.float64)
        hist /= n_pixels
        for i in range(RGB_HIST_BINS):
            f[f"hist_{name}_{i:02d}"] = float(hist[i])

    # --- Spatial layout: centre of mass + mirror symmetry ----------------------
    gi = gray_native.astype(np.float64)
    total = gi.sum()
    if total > 0:
        ys, xs = np.mgrid[0:h, 0:w]
        f["com_x"] = float((gi * xs).sum() / total / max(1, w - 1))
        f["com_y"] = float((gi * ys).sum() / total / max(1, h - 1))
    else:
        f["com_x"] = f["com_y"] = 0.5
    g256 = gray256.astype(np.float64) / 255.0
    f["lr_asymmetry"] = float(np.abs(g256 - g256[:, ::-1]).mean())
    f["tb_asymmetry"] = float(np.abs(g256 - g256[::-1, :]).mean())

    # --- Localized high-edge patch (text/logo overlay) -------------------------
    # A logo overlay concentrates edges in one small region; a normal image
    # spreads them. Max-cell minus mean-cell edge density flags that spike.
    gh, gw = edges.shape[0] // GRID, edges.shape[1] // GRID
    cell_density = []
    for gy_ in range(GRID):
        for gx_ in range(GRID):
            cell = edges[gy_ * gh : (gy_ + 1) * gh, gx_ * gw : (gx_ + 1) * gw]
            if cell.size:
                cell_density.append((cell > 0).mean())
    cell_density = np.array(cell_density) if cell_density else np.array([0.0])
    f["edge_cell_max"] = float(cell_density.max())
    f["edge_cell_spread"] = float(cell_density.max() - cell_density.mean())

    return f


def features_from_path(path: Path | str) -> dict[str, float]:
    """Decode `path` and return its feature dict."""
    return features_from_rgba(_load_rgba(path))


if __name__ == "__main__":
    # Tiny self-check: print the feature vector for a couple of files.
    for arg in sys.argv[1:]:
        feats = features_from_path(arg)
        print(f"\n{arg}  ({len(feats)} features)")
        for k, v in feats.items():
            print(f"  {k:32} {v:.5f}")
