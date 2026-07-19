"""Phase E detector: does a router-MANAGED, storage-free detector beat the paper on
MULTI-manipulation images -- without sacrificing crop-resistance?

Four rows, all on the composed splits (data/multi_{train,test}); the voting policy
is tuned on multi_train and reported on multi_test (tune-on-train, §4/§8.3):

  A  static {aHash,pHash,hsvHash,sHash} k=2  -- THE PAPER'S OWN METHOD; the number to beat.
  B  router-MANAGED, SAME panel {a,p,hsv,sHash} -- so B - A is PURELY the router. The
       deployable enhancement (4 small hashes, 24-32 B; no ORB). Multi-label heads
       (train_multilabel.py) gate it: P_detail>tau -> distrust sHash; P_colour>tau ->
       distrust hsvHash; then a quorum over the survivors, with a static-k=2 fallback so
       it is never worse than A.
  B' static {a,p,hsv,ORB} k=2, NO router -- the ORB swap alone on multi (Phase D row B
       analogue), NON-DEPLOYABLE (ORB is 14.5 KB/NFT, §6). Isolates swap vs router.
  C  router-MANAGED {a,p,hsv,sHash,ORB} -- the ceiling, NON-DEPLOYABLE. detail distrusts
       ORB AND sHash; colour distrusts hsvHash.

Every F1 is printed beside its COMPUTED flag-everything floor for that exact set (never
the reused 75.0/90.5). positives=/negatives= printed at load (§8.5).

The Chunk-3/4a result this quantifies: the router's quorum-relax needs the 2-flag
(detail AND colour) state, and that state is UNREACHABLE -- pixelation (the only
detail-breaker) physically erases the colour tell (probe_multi.py + train_multilabel.py),
in both composition orders. So B is expected ~= A on multi; the swap (B') still pays.

Usage:
    training/.venv/bin/python python/detector/evaluate_multi.py            # multi
    training/.venv/bin/python python/detector/evaluate_multi.py --single   # contrast on data/test
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from detector_common import (
    Metrics,
    evaluate,
    fires,
    flag_everything_f1,
    load_config,
    load_pairs,
    verdict_static,
    REPO_ROOT,
)
from multilabel_signal import multilabel_proba
from evaluate_routed import quorum_needed

B_PANEL = ("ahash", "phash", "hsvhash", "shash")        # deployable: 4 small hashes
BPRIME_PANEL = ("ahash", "phash", "hsvhash", "orb")     # static + ORB (no router)
C_PANEL = ("ahash", "phash", "hsvhash", "shash", "orb")  # ceiling: router + ORB

COLOUR_MANIPS = {"color_swap_modify_saturate", "background_color_change"}


# --------------------------------------------------------------------------- #
# Router-managed voting                                                        #
# --------------------------------------------------------------------------- #
def healthy_panel(panel, p_detail, p_colour, tau):
    """Signals the multi-label router still trusts. detail (pixelation) breaks ORB
    AND sHash (Chunk 1); colour breaks hsvHash (§8.3). aHash/pHash are never gated --
    they survive both (the §8.3 pHash lesson)."""
    h = list(panel)
    if p_detail > tau:
        for s in ("orb", "shash"):
            if s in h:
                h.remove(s)
    if p_colour > tau and "hsvhash" in h:
        h.remove("hsvhash")
    return tuple(h)


def routed_predict(pair, proba_row, thresholds, panel, tau):
    p_detail, p_colour = proba_row
    h = healthy_panel(panel, p_detail, p_colour, tau)
    votes = sum(fires(s, pair.scores.get(s), thresholds[s]) for s in h)
    return votes >= quorum_needed(len(h))


def weighted_predict(pair, proba_row, thresholds, panel, decision):
    """Extension policy: weight each signal's vote by its predicted reliability instead
    of one-vote-each (the Phase-D C3 idea). detail downweights ORB & sHash; colour
    downweights hsvHash; aHash/pHash always full weight. Fires if the weighted sum of
    the firing signals clears `decision` (tuned on multi_train)."""
    p_detail, p_colour = proba_row
    w = {"ahash": 1.0, "phash": 1.0, "hsvhash": 1.0 - p_colour,
         "shash": 1.0 - p_detail, "orb": 1.0 - p_detail}
    score = sum(w[s] for s in panel if fires(s, pair.scores.get(s), thresholds[s]))
    return score >= decision


def tune_weighted(train_pairs, train_proba, thresholds, panel):
    best_d, best_f1 = 1.0, -1.0
    grid = np.round(np.linspace(0.5, 3.0, 26), 2)
    for d in grid:
        m = evaluate(train_pairs,
                     lambda p, d=d: weighted_predict(p, train_proba[p.copy], thresholds, panel, float(d)))
        if m.f1 > best_f1:
            best_d, best_f1 = float(d), m.f1
    boundary = best_d in (float(grid[0]), float(grid[-1]))
    print(f"  weighted-vote decision tuned on multi_train: {best_d} (train F1 {best_f1:.2%})"
          + ("  ** grid boundary -- distrust (§8.5) **" if boundary else ""))
    return best_d


def make_policies(proba, thresholds, tau):
    """The four comparison policies as pair->bool predicates."""
    A = lambda p: verdict_static(p, B_PANEL, thresholds, 2)                 # paper
    Bpure = lambda p: routed_predict(p, proba[p.copy], thresholds, B_PANEL, tau)
    B = lambda p: A(p) or Bpure(p)                                         # + static fallback
    Bprime = lambda p: verdict_static(p, BPRIME_PANEL, thresholds, 2)      # swap, no router
    Cstat = lambda p: verdict_static(p, C_PANEL, thresholds, 2)            # sHash+ORB, no router
    Cpure = lambda p: routed_predict(p, proba[p.copy], thresholds, C_PANEL, tau)
    C = lambda p: Cstat(p) or Cpure(p)
    return {"A static+sHash (paper)": A, "B routed (deployable)": B,
            "B pure (no fallback)": Bpure, "B' static+ORB (swap, no router)": Bprime,
            "Cstat static sHash+ORB (no router)": Cstat, "C routed+ORB (ceiling)": C}


# --------------------------------------------------------------------------- #
# tau sweep on multi_train                                                     #
# --------------------------------------------------------------------------- #
def tune_tau(train_pairs, train_proba, thresholds):
    grid = [0.3, 0.4, 0.5, 0.6, 0.7]
    print("  tau sweep on multi_train (B routed, full-set F1):")
    best_tau, best_f1 = 0.5, -1.0
    for tau in grid:
        pol = make_policies(train_proba, thresholds, tau)["B routed (deployable)"]
        m = evaluate(train_pairs, pol)
        if m.f1 > best_f1:
            best_tau, best_f1 = tau, m.f1
        print(f"    tau={tau}: F1 {m.f1:.2%}")
    print(f"  -> tau={best_tau} (train F1 {best_f1:.2%}); note the 2-flag relax is unreachable")
    print(f"     (pixelation erases the colour tell), so tau barely moves B -- itself the finding.")
    return best_tau


# --------------------------------------------------------------------------- #
# reporting                                                                    #
# --------------------------------------------------------------------------- #
def overall_table(pairs, policies):
    n_pos = sum(p.is_copy for p in pairs)
    floor = flag_everything_f1(n_pos, len(pairs) - n_pos)
    base = evaluate(pairs, policies["A static+sHash (paper)"])
    print(f"\n--- overall on {len(pairs)} pairs (flag-everything floor {floor:.1%}) ---")
    print(f"  {'policy':<34}{'P':>8}{'R':>8}{'F1':>8}{'dF1 vs A':>10}")
    for name, pol in policies.items():
        m = evaluate(pairs, pol)
        print(f"  {name:<34}{m.precision:>7.1%}{m.recall:>8.1%}{m.f1:>8.1%}{m.f1 - base.f1:>+10.2%}")


def profile_of(comp):
    parts = comp.split("__")
    has_det = "pixelated" in parts
    has_col = any(p in COLOUR_MANIPS for p in parts)
    if has_det and has_col:
        return "2-flag: pixelate x colour  (relax SHOULD fire -- but tell is erased)"
    if has_det:
        return "1-flag detail: pixelate x other"
    if has_col:
        return "1-flag colour: colour x other"
    return "0-flag: geometric / text"


def per_composition(pairs, policies):
    """Detection per composition, grouped by router-flag profile, for A / B / B' / C.
    Negatives are shared; each row also prints its computed flag-everything floor."""
    neg = [p for p in pairs if not p.is_copy]
    n_neg = len(neg)
    cols = ["A static+sHash (paper)", "B routed (deployable)",
            "B' static+ORB (swap, no router)", "C routed+ORB (ceiling)"]
    short = {"A static+sHash (paper)": "A", "B routed (deployable)": "B",
             "B' static+ORB (swap, no router)": "B'", "C routed+ORB (ceiling)": "C"}
    fp = {c: sum(policies[c](p) for p in neg) for c in cols}

    comps = sorted({p.category for p in pairs if p.is_copy})
    groups = {}
    for comp in comps:
        groups.setdefault(profile_of(comp), []).append(comp)

    print(f"\n--- detection per composition (n_neg={n_neg}; shared FP row at end) ---")
    print(f"  {'composition':<50}{'n':>5}" + "".join(f"{short[c]:>7}" for c in cols) + f"{'floor':>8}")
    for prof in sorted(groups):
        print(f"  -- {prof} --")
        for comp in groups[prof]:
            sub = [p for p in pairs if p.is_copy and p.category == comp]
            floor = flag_everything_f1(len(sub), n_neg)
            line = f"  {comp:<50}{len(sub):>5}"
            for c in cols:
                line += f"{sum(policies[c](p) for p in sub) / len(sub):>7.0%}"
            line += f"{floor:>8.1%}"
            print(line)
    print(f"  {'non_duplicate (FP)':<50}{n_neg:>5}" +
          "".join(f"{fp[c] / n_neg:>7.0%}" for c in cols))


# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", action="store_true",
                        help="report on data/test single-manip (the B~=A contrast) instead of multi")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--tau", type=float, default=None, help="override the tuned tau")
    args = parser.parse_args()

    thresholds = load_config(args.data_dir)
    print("operating points: " + "  ".join(f"{s}={thresholds[s]:g}" for s in
          ("ahash", "phash", "hsvhash", "shash", "orb")))

    # Tune tau on multi_train (always, even for the --single report: the policy is one
    # object, tuned on multi_train, applied wherever).
    train_pairs = load_pairs("multi_train", args.data_dir)
    train_proba = multilabel_proba("multi_train", args.data_dir)
    print("\n== tuning on multi_train ==")
    tau = args.tau if args.tau is not None else tune_tau(train_pairs, train_proba, thresholds)
    weighted_d = tune_weighted(train_pairs, train_proba, thresholds, B_PANEL)

    report_split = "test" if args.single else "multi_test"
    print(f"\n== reporting on {report_split} (tau={tau}) ==")
    pairs = load_pairs(report_split, args.data_dir)
    proba = multilabel_proba(report_split, args.data_dir)
    policies = make_policies(proba, thresholds, tau)
    # weighted vote (extension, B panel) -- tuned decision from multi_train
    policies["D weighted vote (B panel, extension)"] = \
        lambda p: weighted_predict(p, proba[p.copy], thresholds, B_PANEL, weighted_d)

    overall_table(pairs, policies)
    n_pos = sum(p.is_copy for p in pairs)
    floor = flag_everything_f1(n_pos, len(pairs) - n_pos)
    print(f"\n  Base-rate caveat: at this {n_pos/len(pairs):.0%}-positive rate the flag-everything")
    print(f"  floor is {floor:.1%}, so raw F1 rewards recall -- compare RECALL at equal precision")
    print(f"  (all policies sit at ~96-97% P). 'D weighted' lands on its grid boundary (permissive")
    print(f"  collapse, the §9.6/§8.5 artifact -- NOT a routing win; real copymints are rare, §9.2).")
    if args.single:
        print("\n  (single-manip: B ~= A -- the router's relax never engages here, §9.)")
    else:
        per_composition(pairs, policies)
        print("\n  Reading: B - A ~= 0 on every composition INCLUDING pixelate x colour, because the")
        print("  2-flag relax is unreachable (pixelation erases the colour tell -- probe_multi.py /")
        print("  train_multilabel.py). C routed == C static, so routing is a no-op even with ORB.")
        print("  The swap (B' vs A) is what pays, only where spatial structure survives -- as in §9.")


if __name__ == "__main__":
    main()
