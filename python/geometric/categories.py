"""How the dataset's manipulation categories relate to a geometric signal.

Shared by tune.py and evaluate.py so the split is stated once, not restated
(inconsistently) in each.
"""

# What ORB is *for*: manipulations that move, mirror, or reframe the image while
# leaving its structure intact. These are the paper's blind spot and the reason
# sHash existed at all.
GEOMETRIC_POSITIVES = frozenset({"flip_rotate_mirror", "resize_crop_reposition"})

# Trivially detectable; kept out of tuning so it cannot inflate recall, and
# reported separately as a sanity control (a pixel-identical copy that fails to
# score high means the pipeline is broken, not badly tuned).
CONTROL_POSITIVES = frozenset({"exact_copy"})

# What ORB is emphatically *not* for. Pixelation destroys keypoints; colour
# edits are largely invisible in grayscale. The other three hashes cover these.
# Chasing them is what wrecked the original baseline's thresholds.
NON_GEOMETRIC_POSITIVES = frozenset(
    {"pixelated", "color_swap_modify_saturate", "background_color_change", "text_logo_emoji"}
)

NEGATIVES = frozenset({"non_duplicate"})

ALL_POSITIVES = GEOMETRIC_POSITIVES | CONTROL_POSITIVES | NON_GEOMETRIC_POSITIVES


def collection_of(filename: str) -> str:
    """azuki / bayc / cp -- punks are 24x24 pixel art and behave very differently."""
    return filename.split("_")[0]
