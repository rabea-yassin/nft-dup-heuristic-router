"""Pairwise ORB geometric-match signal -- the replacement for sHash.

sHash was the paper's crop-resistance hash, and it is the one signal that cannot
be soundly indexed (its distance is asymmetric and violates the triangle
inequality, so a BK-tree can prune away real matches -- see PROGRESS.md). ORB
covers the same geometric blind spot and is sound. This module is that signal.

Design notes
------------
**Pairwise, not retrieval.** We compare `original_image` against `copy_image`
directly and ask "is this a geometric match?". That matches the dataset's own
schema (`is_copy` labels a *pair*, not an image), the paper's methodology, and
how the four hashes are evaluated -- so ORB drops in beside them as a directly
comparable signal. It also removes the gallery from the accuracy path entirely,
which is what made the previous baseline meaningless (its confusion matrix was
decided by which images happened to be indexed; see verify_baseline.py). The
LSH gallery survives only as a scalability question, in orb_index.py.

**Why the mirror variations are necessary.** ORB is rotation-invariant but not
reflection-invariant: its BRIEF sampling pattern is not mirror-symmetric, so a
mirrored copy's descriptors simply do not match the original's, and matching
fails before RANSAC ever sees a point. A homography *can* express a reflection,
but only if the descriptor matching survives long enough to hand it points. So
we physically flip and re-describe. (This trick is carried over from Ahmad's
original pipeline, where it was likewise load-bearing.)

**Resolution normalisation is mandatory, not cosmetic.** ORB has a minimum
spatial extent: its default patchSize and edgeThreshold are both 31 px, so an
image smaller than that yields *zero* descriptors. Our corpus is heterogeneous
-- generate_dataset.py's downscale only ever shrinks, so azuki/bayc arrive at
256x256 but CryptoPunks stay at their native 24x24 -- and ORB returns nothing at
all for 100% of the punks. That is exactly how the original pipeline lost them:
its indexer skipped any image whose descriptors were None, silently dropping all
1,000 punks from the gallery and capping recall at 2/3 (see verify_baseline.py).
Upscaling to a common working edge fixes it (0 -> ~225 keypoints), and NEAREST
beats LANCZOS for upscaling because pixel art's hard edges *are* the corners ORB
wants; smoothing them away costs keypoints.

This is a real architectural cost of adopting a feature-matching signal: the
four binary hashes resize to 8x8/32x32 internally and work at any input size,
whereas ORB imposes a minimum resolution on the corpus.

**One shared module.** tune.py and evaluate.py both import this, rather than
each carrying their own copy of the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# Unified across both sides of the comparison. The original pipeline used 500
# for the gallery but 1000 for queries, which meant the two sides were never
# described on equal terms.
ORB_FEATURES = 500

# Every image is scaled so its longer edge is this, aspect ratio preserved, so
# both sides of a comparison get equal spatial extent and nothing falls under
# ORB's 31 px patch. A no-op for the 256 px azuki/bayc images; the rescue for
# the 24 px punks.
WORKING_EDGE_PX = 256

# RANSAC reprojection tolerance in pixels, and the minimum correspondences
# cv2.findHomography needs to solve at all.
RANSAC_REPROJECTION_PX = 5.0
MIN_MATCHES_FOR_HOMOGRAPHY = 4

# identity, horizontal mirror, vertical mirror, both (= 180-degree rotation)
MIRROR_FLIPS = (None, 1, 0, -1)


def normalize_for_orb(img: np.ndarray, working_edge: int = WORKING_EDGE_PX) -> np.ndarray:
    """Scale so the longer edge is `working_edge`, preserving aspect ratio.

    NEAREST when magnifying: the small images here are pixel art, whose hard
    edges are the very corners ORB keys on -- interpolating them away measurably
    costs keypoints. AREA when minifying, the usual choice for downscaling.
    """
    height, width = img.shape[:2]
    longest = max(height, width)
    if longest == working_edge or longest == 0:
        return img
    scale = working_edge / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    interpolation = cv2.INTER_NEAREST if scale > 1 else cv2.INTER_AREA
    return cv2.resize(img, size, interpolation=interpolation)


class OrbMatcher:
    """Computes the pairwise geometric-match score between two image files.

    Descriptors for the unflipped image are cached by path, which matters:
    each original appears in ~19 rows of the metadata, so without the cache we
    would re-describe it every time.
    """

    def __init__(
        self,
        images_dir: Path,
        orb_features: int = ORB_FEATURES,
        working_edge: int = WORKING_EDGE_PX,
    ) -> None:
        self.images_dir = Path(images_dir)
        self.working_edge = working_edge
        self._orb = cv2.ORB_create(nfeatures=orb_features)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self._cache: dict[str, tuple] = {}

    def _load_gray(self, filename: str):
        img = cv2.imread(str(self.images_dir / filename), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(self.images_dir / filename)
        return normalize_for_orb(img, self.working_edge)

    def _describe_cached(self, filename: str) -> tuple:
        if filename not in self._cache:
            self._cache[filename] = self._orb.detectAndCompute(self._load_gray(filename), None)
        return self._cache[filename]

    def _inliers(self, kp_a, des_a, kp_b, des_b) -> int:
        """RANSAC inlier count for one already-described pair."""
        if des_a is None or des_b is None:
            return 0
        matches = self._matcher.match(des_a, des_b)
        if len(matches) < MIN_MATCHES_FOR_HOMOGRAPHY:
            return 0
        src = np.float32([kp_a[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        dst = np.float32([kp_b[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_REPROJECTION_PX)
        return 0 if mask is None else int(mask.sum())

    def score(self, original: str, copy: str) -> int:
        """Geometric-match score for a pair: max RANSAC inliers over mirrorings.

        The copy is the side we flip, since it is the manipulated one. Higher
        means more of the two images' structure is explained by a single
        geometric transform.
        """
        kp_o, des_o = self._describe_cached(original)
        if des_o is None:
            return 0

        copy_img = self._load_gray(copy)
        best = 0
        for flip in MIRROR_FLIPS:
            variant = copy_img if flip is None else cv2.flip(copy_img, flip)
            kp_c, des_c = self._orb.detectAndCompute(variant, None)
            best = max(best, self._inliers(kp_c, des_c, kp_o, des_o))
        return best


def is_duplicate(score: int, threshold: int) -> bool:
    """The signal's verdict. Strictly greater, matching the original pipeline."""
    return score > threshold
