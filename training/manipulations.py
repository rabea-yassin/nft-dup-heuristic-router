"""
Image manipulation functions implementing OpenSea's copyminting definition
(see paper Section II-B / III-A): each function takes a PIL.Image and a
random.Random instance and returns a new, manipulated PIL.Image.

Kept deliberately simple and dependency-light (Pillow + numpy only) since
this only needs to produce plausible, labeled training examples for the
router's manipulation-type classifier -- not pixel-perfect replication of
any specific copymint tool.
"""

import random

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont


def exact_copy(img: Image.Image, rng: random.Random) -> Image.Image:
    return img.copy()


exact_copy.deterministic = True  # no randomness -- one variant is enough, ever


def flip_rotate_mirror(img: Image.Image, rng: random.Random) -> Image.Image:
    """Plain horizontal/vertical mirroring (each applied independently,
    50/50) plus a continuous random-angle rotation -- not a pick from a
    handful of discrete 90-degree ops, which repeats often across just a
    few random draws and only ever produces axis-aligned results anyway."""
    out = img
    if rng.random() < 0.5:
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    if rng.random() < 0.5:
        out = out.transpose(Image.FLIP_TOP_BOTTOM)

    angle = rng.uniform(1, 359)
    # rotate in RGBA so the corners exposed by a non-90-degree rotation fill
    # with transparency rather than an arbitrary solid color
    rotated = out.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
    return rotated.resize(img.size, Image.LANCZOS).convert(img.mode if img.mode != "P" else "RGBA")


def resize_crop_reposition(img: Image.Image, rng: random.Random) -> Image.Image:
    """Crop (keeping >=50% of the original area, per the paper's bound),
    reposition the crop box randomly, then resize back to the original
    dimensions -- optionally leaving a solid border to simulate
    "modified borders and edges"."""
    w, h = img.size
    # keep-fraction in [0.5, 0.9] area retained => side retained in [~0.71, ~0.95]
    keep_area = rng.uniform(0.5, 0.9)
    side_frac = keep_area**0.5
    crop_w, crop_h = max(1, int(w * side_frac)), max(1, int(h * side_frac))
    left = rng.randint(0, w - crop_w)
    top = rng.randint(0, h - crop_h)
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    resized = cropped.resize((w, h), Image.LANCZOS)

    if rng.random() < 0.3:
        # add a solid border, then resize back down to the original size
        border = max(1, int(min(w, h) * rng.uniform(0.02, 0.08)))
        bordered = Image.new(resized.mode, (w + 2 * border, h + 2 * border), _random_color(rng, resized.mode))
        bordered.paste(resized, (border, border))
        return bordered.resize((w, h), Image.LANCZOS)
    return resized


def text_logo_emoji(img: Image.Image, rng: random.Random) -> Image.Image:
    """Overlay a short text/logo-like mark covering < 20% of the image
    area (per the paper's bound), at a random position and color."""
    out = img.convert("RGBA")
    w, h = out.size
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    mark = rng.choice(["COPY", "NFT", "©", "SAMPLE", "★"])
    font_size = max(8, int(min(w, h) * rng.uniform(0.08, 0.16)))
    try:
        font = ImageFont.load_default(size=font_size)
    except TypeError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), mark, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # keep the overlay's footprint under ~20% of the image area
    while (text_w * text_h) > 0.20 * w * h and font_size > 6:
        font_size = int(font_size * 0.85)
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            break
        bbox = draw.textbbox((0, 0), mark, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    x = rng.randint(0, max(1, w - text_w))
    y = rng.randint(0, max(1, h - text_h))
    color = _random_color(rng, "RGBA")
    draw.text((x, y), mark, font=font, fill=color)

    return Image.alpha_composite(out, overlay).convert(img.mode if img.mode != "P" else "RGBA")


def background_color_change(img: Image.Image, rng: random.Random) -> Image.Image:
    """Approximate a background-color swap: treat pixels close to the
    dominant *opaque border* color as background and recolor them,
    preserving alpha. Corner pixels are unreliable on images with
    antialiased/rounded corners (they're often near-transparent), so the
    reference color is taken from the full opaque border ring instead."""
    rgba = img.convert("RGBA")
    arr = np.array(rgba).astype(np.int16)
    h, w = arr.shape[:2]

    border_mask = np.zeros((h, w), dtype=bool)
    margin = max(1, min(h, w) // 64)
    border_mask[:margin, :] = border_mask[-margin:, :] = True
    border_mask[:, :margin] = border_mask[:, -margin:] = True
    opaque = arr[:, :, 3] > 200
    ref_mask = border_mask & opaque

    if not ref_mask.any():
        # fully transparent border (e.g. a circular sprite) -- nothing
        # reliably reads as "background", so leave the image unchanged.
        return rgba.convert(img.mode if img.mode != "P" else "RGBA")

    bg_ref = np.median(arr[ref_mask][:, :3], axis=0)

    dist = np.linalg.norm(arr[:, :, :3] - bg_ref, axis=-1)
    threshold = rng.uniform(20, 45)
    mask = (dist < threshold) & opaque

    new_color = np.array(_random_color(rng, "RGB"), dtype=np.int16)
    out = arr.copy()
    out[mask, :3] = new_color
    return Image.fromarray(out.astype(np.uint8), mode="RGBA").convert(img.mode if img.mode != "P" else "RGBA")


def pixelate(img: Image.Image, rng: random.Random) -> Image.Image:
    w, h = img.size
    factor = rng.uniform(0.04, 0.12)
    small = img.resize((max(1, int(w * factor)), max(1, int(h * factor))), Image.NEAREST)
    return small.resize((w, h), Image.NEAREST)


def color_swap_modify_saturate(img: Image.Image, rng: random.Random) -> Image.Image:
    mode = rng.choice(["saturate", "desaturate", "brightness", "channel_swap", "invert_partial"])
    if mode == "saturate":
        return ImageEnhance.Color(img.convert("RGB")).enhance(rng.uniform(1.6, 2.5)).convert(img.mode)
    if mode == "desaturate":
        return ImageEnhance.Color(img.convert("RGB")).enhance(rng.uniform(0.0, 0.4)).convert(img.mode)
    if mode == "brightness":
        factor = rng.choice([rng.uniform(1.4, 1.9), rng.uniform(0.3, 0.6)])
        return ImageEnhance.Brightness(img.convert("RGB")).enhance(factor).convert(img.mode)
    if mode == "channel_swap":
        arr = np.array(img.convert("RGB"))
        perm = rng.choice([(1, 0, 2), (2, 1, 0), (0, 2, 1), (2, 0, 1)])
        return Image.fromarray(arr[:, :, perm]).convert(img.mode)
    # invert_partial: invert RGB, keep alpha if present
    rgb = np.array(img.convert("RGB"))
    inverted = 255 - rgb
    return Image.fromarray(inverted).convert(img.mode)


def _random_color(rng: random.Random, mode: str):
    r, g, b = rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)
    if mode == "RGBA":
        return (r, g, b, 255)
    return (r, g, b)


# category name -> manipulation function. Order matters only for display/debug.
MANIPULATIONS = {
    "exact_copy": exact_copy,
    "flip_rotate_mirror": flip_rotate_mirror,
    "resize_crop_reposition": resize_crop_reposition,
    "text_logo_emoji": text_logo_emoji,
    "background_color_change": background_color_change,
    "pixelated": pixelate,
    "color_swap_modify_saturate": color_swap_modify_saturate,
}

# The six non-trivial manipulations (exact_copy composes with nothing -- it is the
# identity, so exact_copy o X == X). Phase E composes TWO of these in sequence.
COMPOSABLE = tuple(k for k in MANIPULATIONS if k != "exact_copy")


def composition_pairs() -> list[tuple[str, str]]:
    """All ordered pairs (first, second) of two DISTINCT non-exact manipulations:
    the full cross-product in BOTH orders (15 unordered x 2 = 30). Composing is
    second(first(base)). Both orders are kept on purpose -- order changes what a
    forensic tell survives (recolour's histogram "combing" is wiped by a later
    pixelation but not by an earlier one), so the order effect is itself a Phase E
    measurement, and the full cross-product avoids cherry-picking the combo that
    happens to win."""
    import itertools

    pairs: list[tuple[str, str]] = []
    for a, b in itertools.combinations(COMPOSABLE, 2):
        pairs.append((a, b))
        pairs.append((b, a))
    return pairs
