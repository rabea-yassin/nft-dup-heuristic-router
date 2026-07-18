"""Shared plumbing for the routed detector (Phase D).

Phase D assembles the paper's *2-Minimal Distance Detector* -- flag a duplicate
when >=k signals agree within threshold -- out of our signals, and asks whether
routing the vote beats the static rule. Everything here runs off CACHED inputs;
this is arithmetic, not vision. No image is decoded.

The four signals and their polarity (the single place polarity lives -- getting
it backwards is the trap PLAN.md 0 warns about):

    aHash / pHash / hsvHash / sHash  -> DISTANCE, fires when  dist <= threshold
    ORB                              -> inlier COUNT, fires when  count > threshold

Hash distances are Hamming over the per-image hashes in image_hashes.csv (the same
imagehash values our C11 ports are bit-exact against), computed per PAIR at load.
ORB and sHash pair scores are the cached CSVs from python/geometric/.

Operating points (all train-derived, reused from earlier phases so Phase D
introduces no new hash/ORB numbers):
  * aHash<=7, pHash<=19, hsvHash<=3  -- hash_thresholds.json, the 10% FP-budget
    points derived on train (PROGRESS.md 8.3).
  * ORB>16                           -- the geometric train-tuned point (PROGRESS.md 5).
  * sHash<=t                         -- the one gap; tune_static.py derives it on
    train by the same standalone-F1 criterion ORB's slot was tuned by, and writes
    it into detector_config.json.

The report helper never prints a bare F1: it prints, beside every F1, the
flag-everything floor COMPUTED for that exact positive/negative set. The 75.0
figure everyone quotes is only the geometric-subset floor; the full test set is
82.6% positive, so its floor is ~90.5, and each category differs. "Beats 75.0"
can still lose to flag-everything -- so we compute the right floor every time.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geometric"))
from categories import (  # noqa: E402
    CONTROL_POSITIVES,
    GEOMETRIC_POSITIVES,
    NEGATIVES,
    NON_GEOMETRIC_POSITIVES,
    collection_of,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Signal names and polarity. distance=True -> fires on <=t; distance=False -> fires on >t.
HASH_SIGNALS = ("ahash", "phash", "hsvhash")
DISTANCE_SIGNALS = HASH_SIGNALS + ("shash",)
COUNT_SIGNALS = ("orb",)
ALL_SIGNALS = HASH_SIGNALS + ("shash", "orb")

# The two panels Phase D compares. The paper's panel vs the swap.
SHASH_PANEL = ("ahash", "phash", "hsvhash", "shash")
ORB_PANEL = ("ahash", "phash", "hsvhash", "orb")

LABEL = {"ahash": "aHash", "phash": "pHash", "hsvhash": "hsvHash", "shash": "sHash", "orb": "ORB"}
BITS = {"ahash": 64, "phash": 64, "hsvhash": 42, "shash": None, "orb": None}

# Established train operating points reused from earlier phases (PROGRESS 5, 8.3).
# sHash is filled in from detector_config.json (tune_static.py derives it on train).
ORB_THRESHOLD = 16


def fires(signal: str, value: float | None, threshold: float) -> bool:
    """Does `signal` vote 'duplicate' at `threshold`? None (missing score) = abstain."""
    if value is None:
        return False
    if signal in COUNT_SIGNALS:
        return value > threshold
    return value <= threshold  # distance signals


def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


@dataclass
class Pair:
    original: str
    copy: str
    category: str
    is_copy: bool
    collection: str
    scores: dict[str, float | None] = field(default_factory=dict)  # signal -> raw value


def _load_image_hashes(split_dir: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with open(split_dir / "image_hashes.csv", newline="") as f:
        for r in csv.DictReader(f):
            out[r["image"]] = {"ahash": r["ahash"], "phash": r["phash"], "hsvhash": r["hsvhash"]}
    return out


def _load_pair_scores(path: Path, column: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    if not path.exists():
        return out
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            key = (r["original_image"].strip(), r["copy_image"].strip())
            out[key] = float(r[column])
    return out


def load_pairs(split: str, data_dir: Path = REPO_ROOT / "data", verbose: bool = True) -> list[Pair]:
    """Every pair in metadata.csv, joined with all available signal scores.

    A missing score (sHash cache absent, an ORB row dropped) becomes None = the
    signal abstains, never a silent wrong vote. Prints the class balance at load:
    the dict-splat bug (PROGRESS 8.5) turned every row positive and went unnoticed
    precisely because nobody printed positives=... negatives=...
    """
    split_dir = data_dir / split
    img_hashes = _load_image_hashes(split_dir)
    orb = _load_pair_scores(split_dir / "orb_scores.csv", "orb_inliers")
    shash = _load_pair_scores(split_dir / "shash_scores.csv", "shash_dist")

    pairs: list[Pair] = []
    missing_orb = missing_shash = missing_hash = 0
    with open(split_dir / "metadata.csv", newline="") as f:
        for r in csv.DictReader(f):
            o, c = r["original_image"].strip(), r["copy_image"].strip()
            scores: dict[str, float | None] = {}
            ho, hc = img_hashes.get(o), img_hashes.get(c)
            if ho is None or hc is None:
                missing_hash += 1
                for s in HASH_SIGNALS:
                    scores[s] = None
            else:
                for s in HASH_SIGNALS:
                    scores[s] = hamming_hex(ho[s], hc[s])
            scores["orb"] = orb.get((o, c))
            scores["shash"] = shash.get((o, c))
            if scores["orb"] is None:
                missing_orb += 1
            if scores["shash"] is None:
                missing_shash += 1
            pairs.append(
                Pair(o, c, r["manipulation_type"].strip(), r["is_copy"].strip() == "1",
                     collection_of(o), scores)
            )

    if verbose:
        pos = sum(p.is_copy for p in pairs)
        neg = len(pairs) - pos
        print(f"[load {split}] {len(pairs)} pairs   positives={pos} negatives={neg} "
              f"({pos/len(pairs):.1%} positive)")
        notes = []
        if missing_hash:
            notes.append(f"{missing_hash} missing hash")
        if missing_orb:
            notes.append(f"{missing_orb} missing ORB")
        if missing_shash:
            notes.append(f"{missing_shash} missing sHash (abstains)")
        if notes:
            print(f"           abstentions: " + ", ".join(notes))
    return pairs


# --------------------------------------------------------------------------- #
# Voting rules                                                                 #
# --------------------------------------------------------------------------- #
def count_votes(pair: Pair, panel, thresholds: dict[str, float]) -> int:
    return sum(fires(s, pair.scores.get(s), thresholds[s]) for s in panel)


def verdict_static(pair: Pair, panel, thresholds: dict[str, float], k: int) -> bool:
    """The paper's rule: >=k of the panel agree."""
    return count_votes(pair, panel, thresholds) >= k


def verdict_quorum(pair: Pair, healthy: tuple[str, ...], thresholds: dict[str, float],
                   quorum: dict[int, int]) -> bool:
    """Router-driven: count votes only among the signals the router still trusts,
    and require `quorum[len(healthy)]` of them. Falls to >=1-of-2 when only two
    signals can see the image -- the router's actual payoff (PLAN 2)."""
    votes = sum(fires(s, pair.scores.get(s), thresholds[s]) for s in healthy)
    need = quorum.get(len(healthy), max(1, len(healthy)))
    return votes >= need


# --------------------------------------------------------------------------- #
# Metrics                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class Metrics:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def evaluate(pairs: list[Pair], predict) -> Metrics:
    tp = fp = fn = tn = 0
    for p in pairs:
        yes = predict(p)
        if p.is_copy:
            tp += yes
            fn += not yes
        else:
            fp += yes
            tn += not yes
    return Metrics(tp, fp, fn, tn)


def flag_everything_f1(n_pos: int, n_neg: int) -> float:
    """The trivial 'always say duplicate' F1 for THIS set. recall=1, precision=
    n_pos/(n_pos+n_neg). It is what nearly fooled the project twice (PLAN 0); it
    varies with base rate, so it is computed per table, never hardcoded."""
    if n_pos == 0:
        return 0.0
    precision = n_pos / (n_pos + n_neg)
    return 2 * precision / (precision + 1)


CATEGORY_GROUPS = [
    ("ORB's job (geometric)", sorted(GEOMETRIC_POSITIVES)),
    ("control", sorted(CONTROL_POSITIVES)),
    ("not ORB's job", sorted(NON_GEOMETRIC_POSITIVES)),
]

FLIP_NOTE = "flip_rotate_mirror"  # label rows: rotate+resize, no pure mirrors (PROGRESS 5)


def per_category_detection(pairs: list[Pair], predict, negatives: list[Pair] | None = None) -> None:
    """Detection per positive category, with the negatives' FP rate. Each row's
    F1 (positives-of-that-category vs the shared negative pool) is printed beside
    its computed flag-everything floor."""
    neg = negatives if negatives is not None else [p for p in pairs if not p.is_copy]
    n_neg = len(neg)
    fp = sum(predict(p) for p in neg)
    print(f"  {'category':<26}{'n':>6}{'detected':>10}{'F1':>8}{'flag-all':>10}")
    for title, cats in CATEGORY_GROUPS:
        print(f"  -- {title} --")
        for cat in cats:
            sub = [p for p in pairs if p.is_copy and p.category == cat]
            if not sub:
                continue
            tp = sum(predict(p) for p in sub)
            m = Metrics(tp, fp, len(sub) - tp, n_neg - fp)
            floor = flag_everything_f1(len(sub), n_neg)
            note = "  (rot+resize, no pure mirrors)" if cat == FLIP_NOTE else ""
            print(f"  {cat:<26}{len(sub):>6}{tp/len(sub):>9.1%}{m.f1:>8.1%}{floor:>10.1%}{note}")
    print(f"  {'non_duplicate (FP)':<26}{n_neg:>6}{fp/n_neg if n_neg else 0:>9.1%}")


def load_config(data_dir: Path = REPO_ROOT / "data") -> dict[str, float]:
    """Per-signal thresholds: the 3 hashes from hash_thresholds.json (8.3), ORB
    fixed at its established point (5), sHash from detector_config.json (D)."""
    thr = json.loads((data_dir / "train" / "hash_thresholds.json").read_text())
    out = {s: float(thr[s]["threshold"]) for s in HASH_SIGNALS}
    out["orb"] = float(ORB_THRESHOLD)
    cfg_path = data_dir / "train" / "detector_config.json"
    if cfg_path.exists():
        out["shash"] = float(json.loads(cfg_path.read_text())["shash_threshold"])
    return out
