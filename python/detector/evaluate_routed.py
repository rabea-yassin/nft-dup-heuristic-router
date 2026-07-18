"""Row C: the router-driven dynamic detector, and the honest test of the thesis.

Phase D's question is not "is the router accurate?" (Phase C: yes) but "does
routing the VOTE beat the static rule?" The mechanism the router exists to fix
(PLAN 2): when signals go silent, the paper's ">=2 of 4" quietly tightens to
"both survivors must agree". The router knows which signals can still see the
image, so it can relax the quorum among them.

What this script measures, all on the ORB panel {aHash,pHash,hsvHash,ORB}:

  C2  HEALTHY-QUORUM -- ">=2 of those the router trusts, falling to >=1 of 2 when
      only two are healthy" (PLAN 2, verbatim). Distrust ORB when
      P(detail_broken)>tau, hsvHash when P(colour_changed)>tau. Compared against
      static k=2 (the paper's rule) to ISOLATE the router's contribution, and the
      per-category deltas double as the empirical "never worse than static" check
      (D) -- we verify it, never assert it.

  tau  SWEPT on train (it was an inherited 0.5). The sweep also shows WHY C2 is
      near-static: the healthy-quorum only relaxes in the rare state where BOTH
      flags fire, so tau barely moves the detector -- itself the finding.

  GEOMETRY COUNTERFACTUAL -- the rejected geometry flag (8.5) is the one lever that
      would relax the quorum on flips (where aHash/pHash are silent, so ">=2 of 4"
      caps recall at the hsvHash+ORB joint ~0.64). We do not relitigate the NO; we
      COST it: measure what a geometry-driven relax buys on flips within-distribution
      (router trigger and an oracle upper bound), then show its trigger recall is ~0
      cross-generator (8.4) -- the gain evaporates exactly where it must.

  C3  WEIGHTED VOTE (extension, never the headline) -- weight each signal by the
      router's expected reliability for the predicted class, Sum_c P(c)*det_s(c).
      It departs from the paper's rule and is easiest to overfit, so it is reported
      apart from the main claim.

Train probabilities are out-of-bag (router_signal.py) so nothing is tuned on
in-bag predictions. Every F1 is printed beside its computed flag-everything floor.

Usage:
    training/.venv/bin/python python/detector/evaluate_routed.py --split test
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from detector_common import (
    ORB_PANEL,
    Metrics,
    evaluate,
    fires,
    flag_everything_f1,
    load_config,
    load_pairs,
    per_category_detection,
    verdict_static,
    REPO_ROOT,
)
from router_signal import geometry_mass, reliability_flags, router_proba

GEO_POSITIVES = ("flip_rotate_mirror", "resize_crop_reposition")
COLOUR_CLASSES = ("color_swap_modify_saturate", "background_color_change")


# --------------------------------------------------------------------------- #
# Policies                                                                     #
# --------------------------------------------------------------------------- #
def healthy_panel(proba_row, classes, tau, *, geo=False, oracle_geo=False, is_geo=False):
    """Signals the router still trusts. Dropping ORB (detail) / hsvHash (colour) is
    the shipped router; dropping aHash+pHash (geometry) is the COUNTERFACTUAL flag."""
    detail, colour = reliability_flags(proba_row, classes)
    panel = list(ORB_PANEL)
    if detail > tau and "orb" in panel:
        panel.remove("orb")
    if colour > tau and "hsvhash" in panel:
        panel.remove("hsvhash")
    if geo:
        trigger = is_geo if oracle_geo else (geometry_mass(proba_row, classes) > tau)
        if trigger:
            for s in ("ahash", "phash"):
                if s in panel:
                    panel.remove(s)
    return tuple(panel)


def quorum_needed(n_healthy: int) -> int:
    """>=2 of the trusted signals, falling to >=1 when only two remain (PLAN 2)."""
    return 2 if n_healthy >= 3 else 1


def routed_predict(pair, proba_row, classes, thresholds, tau, **geo_kw):
    panel = healthy_panel(proba_row, classes, tau, **geo_kw)
    votes = sum(fires(s, pair.scores.get(s), thresholds[s]) for s in panel)
    return votes >= quorum_needed(len(panel))


# --------------------------------------------------------------------------- #
# tau sweep on train                                                          #
# --------------------------------------------------------------------------- #
def tune_tau(train_pairs, proba, classes, thresholds) -> float:
    grid = [0.3, 0.4, 0.5, 0.6, 0.7]
    print("  tau sweep on TRAIN (C2 healthy-quorum, full-set F1):")
    best_tau, best_f1 = 0.5, -1.0
    for tau in grid:
        m = evaluate(train_pairs,
                     lambda p: routed_predict(p, proba[p.copy], classes, thresholds, tau))
        star = ""
        if m.f1 > best_f1:
            best_tau, best_f1, star = tau, m.f1, ""
        print(f"    tau={tau}: F1 {m.f1:.2%}")
    print(f"  -> tau={best_tau} (train F1 {best_f1:.2%}); note C2 is near-static so tau barely moves it")
    return best_tau


# --------------------------------------------------------------------------- #
# Comparisons                                                                  #
# --------------------------------------------------------------------------- #
def overall(pairs, predict):
    m = evaluate(pairs, predict)
    n_pos = sum(p.is_copy for p in pairs)
    return m, flag_everything_f1(n_pos, len(pairs) - n_pos)


def f1_at_prevalence(recall: float, fp_rate: float, prevalence: float) -> float:
    """F1 the policy would score if positives were `prevalence` of the stream,
    holding the positive-category MIX at our dataset's. TP=recall*pi, FN=(1-recall)*pi,
    FP=fp_rate*(1-pi). Our 82.6% is a generation artifact (7 positive categories : 1
    negative); real copymints are rare, so this sweep is how we avoid reading the
    natural-rate F1 as the whole story (PLAN 0: flag-everything nearly fooled us)."""
    tp = recall * prevalence
    fn = (1 - recall) * prevalence
    fp = fp_rate * (1 - prevalence)
    denom = 2 * tp + fp + fn
    return 2 * tp / denom if denom else 0.0


def recall_and_fp(pairs, predict) -> tuple[float, float]:
    pos = [p for p in pairs if p.is_copy]
    neg = [p for p in pairs if not p.is_copy]
    recall = sum(predict(p) for p in pos) / len(pos)
    fp = sum(predict(p) for p in neg) / len(neg)
    return recall, fp


def base_rate_sensitivity(pairs, policies) -> None:
    """F1 of each policy across assumed positive prevalence. The natural-rate loss
    of the router to static k=1 inverts once precision matters -- that crossover is
    the honest context, not a rescue."""
    grid = [0.02, 0.05, 0.10, 0.25, 0.50, 0.826]
    stats = {name: recall_and_fp(pairs, pred) for name, pred in policies}
    print(f"  {'prevalence':>12}" + "".join(f"{name:>22}" for name, _ in policies))
    print(f"  {'(recall/FP)':>12}" +
          "".join(f"{f'{r:.0%}/{fp:.0%}':>22}" for _, (r, fp) in stats.items()))
    for pi in grid:
        row = f"  {pi:>11.1%}"
        f1s = {name: f1_at_prevalence(r, fp, pi) for name, (r, fp) in stats.items()}
        best = max(f1s.values())
        for name, _ in policies:
            v = f1s[name]
            row += f"{(f'{v:.1%}' + ('*' if v == best else ' ')):>22}"
        print(row)
    print("  (* = best at that prevalence; natural rate = 82.6%. Real copymints are rare,")
    print("   so the low-prevalence columns are the deployment-relevant ones.)")


def flip_recall(pairs, predict) -> tuple[float, int]:
    sub = [p for p in pairs if p.is_copy and p.category == "flip_rotate_mirror"]
    return sum(predict(p) for p in sub) / len(sub), len(sub)


def per_category_delta(pairs, routed, static) -> None:
    """The empirical 'never worse than static' check (D): routed - static detection
    per category. Negatives share the pool, so a category's precision only moves via
    its own detection; we report detection delta and flag any regression."""
    neg = [p for p in pairs if not p.is_copy]
    fp_routed = sum(routed(p) for p in neg)
    fp_static = sum(static(p) for p in neg)
    print(f"  {'category':<26}{'n':>6}{'static':>9}{'routed':>9}{'delta':>8}")
    cats = sorted({p.category for p in pairs if p.is_copy})
    worse = []
    for cat in cats:
        sub = [p for p in pairs if p.is_copy and p.category == cat]
        rs = sum(static(p) for p in sub) / len(sub)
        rr = sum(routed(p) for p in sub) / len(sub)
        note = "  (rot+resize)" if cat == "flip_rotate_mirror" else ""
        print(f"  {cat:<26}{len(sub):>6}{rs:>8.1%}{rr:>8.1%}{rr-rs:>+8.1%}{note}")
        if rr < rs - 1e-9:
            worse.append((cat, rr - rs))
    print(f"  {'non_duplicate (FP)':<26}{len(neg):>6}"
          f"{fp_static/len(neg):>8.1%}{fp_routed/len(neg):>8.1%}"
          f"{(fp_routed-fp_static)/len(neg):>+8.1%}")
    if worse:
        print("  ** routed is WORSE than static on: " +
              ", ".join(f"{c} ({d:+.1%})" for c, d in worse) + " **")
    else:
        print("  routed detection >= static on every category (never-worse holds empirically)")


def geometry_counterfactual(pairs, proba, classes, thresholds, tau) -> None:
    """Cost the rejected geometry flag: what a geometry-driven quorum relax buys on
    flips, router-triggered and oracle-triggered, vs static k=2 and C2."""
    static_k2 = lambda p: verdict_static(p, ORB_PANEL, thresholds, 2)
    c2 = lambda p: routed_predict(p, proba[p.copy], classes, thresholds, tau)
    geo_router = lambda p: routed_predict(p, proba[p.copy], classes, thresholds, tau, geo=True)
    geo_oracle = lambda p: routed_predict(p, proba[p.copy], classes, thresholds, tau,
                                          geo=True, oracle_geo=True,
                                          is_geo=(p.category in GEO_POSITIVES))
    def cat_recall(pred, cat):
        sub = [p for p in pairs if p.is_copy and p.category == cat]
        return sum(pred(p) for p in sub) / len(sub), len(sub)

    print("\n  flip recall (rotate+resize, no pure mirrors -- PROGRESS 5)  |  crop recall:")
    for name, pred in (("static k=2 (paper)", static_k2), ("C2 healthy-quorum", c2),
                       ("+geometry flag (router trigger)", geo_router),
                       ("+geometry flag (ORACLE trigger)", geo_oracle)):
        fr, fn = flip_recall(pairs, pred)
        cr, _ = cat_recall(pred, "resize_crop_reposition")
        print(f"    {name:<34}{fr:>7.1%} (n={fn})   crop {cr:>6.1%}")
    # FP cost of the geometry relax, on pristine negatives
    neg = [p for p in pairs if not p.is_copy]
    for name, pred in (("C2", c2), ("+geometry (router)", geo_router),
                       ("+geometry (oracle)", geo_oracle)):
        fp = sum(pred(p) for p in neg) / len(neg)
        print(f"    FP[{name}] = {fp:.1%}")
    print("  -> the geometry relax is the lever that lifts flip recall; it needs the flag")
    print("     rejected in 8.5, whose trigger scores ~0 cross-generator (evaluate_reference.py).")


# --------------------------------------------------------------------------- #
# C3 weighted vote (extension)                                                 #
# --------------------------------------------------------------------------- #
def detection_map(train_pairs, thresholds) -> dict[str, dict[str, float]]:
    """det_s(c): train detection rate of each ORB-panel signal per category."""
    cats = sorted({p.category for p in train_pairs if p.is_copy})
    out = {s: {} for s in ORB_PANEL}
    for c in cats:
        sub = [p for p in train_pairs if p.is_copy and p.category == c]
        for s in ORB_PANEL:
            have = [p for p in sub if p.scores.get(s) is not None]
            out[s][c] = (sum(fires(s, p.scores[s], thresholds[s]) for p in have) / len(have)
                         if have else 0.0)
    return out


def weighted_predict(pair, proba_row, classes, thresholds, detmap, decision):
    idx = {c: i for i, c in enumerate(classes)}
    score = 0.0
    for s in ORB_PANEL:
        if not fires(s, pair.scores.get(s), thresholds[s]):
            continue
        w = sum(proba_row[idx[c]] * detmap[s].get(c, 0.0) for c in classes if c in idx)
        score += w
    return score >= decision


def tune_weighted_decision(train_pairs, proba, classes, thresholds, detmap) -> float:
    best_d, best_f1 = 0.5, -1.0
    for d in np.round(np.linspace(0.1, 3.0, 30), 2):
        m = evaluate(train_pairs,
                     lambda p: weighted_predict(p, proba[p.copy], classes, thresholds, detmap, d))
        if m.f1 > best_f1:
            best_d, best_f1 = float(d), m.f1
    print(f"  weighted-vote decision threshold tuned on train: {best_d} (train F1 {best_f1:.2%})")
    return best_d


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="test", choices=["train", "test"])
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()

    thresholds = load_config(args.data_dir)

    train_pairs = load_pairs("train", args.data_dir)
    train_proba, classes = router_proba("train", args.data_dir)
    print("\n== tuning on TRAIN (out-of-bag router probabilities) ==")
    tau = tune_tau(train_pairs, train_proba, classes, thresholds)
    detmap = detection_map(train_pairs, thresholds)
    weighted_d = tune_weighted_decision(train_pairs, train_proba, classes, thresholds, detmap)

    print(f"\n== reporting on {args.split.upper()} ==")
    pairs = load_pairs(args.split, args.data_dir)
    proba, _ = router_proba(args.split, args.data_dir)

    static_k2 = lambda p: verdict_static(p, ORB_PANEL, thresholds, 2)
    static_k1 = lambda p: verdict_static(p, ORB_PANEL, thresholds, 1)
    c2 = lambda p: routed_predict(p, proba[p.copy], classes, thresholds, tau)
    # D: static fallback -> never un-flag what static k=2 flagged. The healthy-quorum
    # can only ADD detections (the 2-healthy 1-of-2 state); dropping a signal in the
    # 3-healthy state removes a voter without lowering the bar, so pure C2 can regress.
    # Since broken signals are silent not noisy (8.3 finding 4), the union forfeits no
    # precision the router could have won -- it just guarantees never-worse.
    c2_shipped = lambda p: static_k2(p) or c2(p)

    m_s2, floor = overall(pairs, static_k2)
    m_s1, _ = overall(pairs, static_k1)
    m_c2, _ = overall(pairs, c2)
    m_cs, _ = overall(pairs, c2_shipped)
    print(f"\n--- Row C vs static (ORB panel), overall (flag-everything floor {floor:.1%}) ---")
    print(f"  static k=2 (paper's rule):    P {m_s2.precision:.1%}  R {m_s2.recall:.1%}  F1 {m_s2.f1:.1%}")
    print(f"  static k=1:                   P {m_s1.precision:.1%}  R {m_s1.recall:.1%}  F1 {m_s1.f1:.1%}")
    print(f"  C2 healthy-quorum (pure):     P {m_c2.precision:.1%}  R {m_c2.recall:.1%}  F1 {m_c2.f1:.1%}")
    print(f"  C2 + static fallback (D):     P {m_cs.precision:.1%}  R {m_cs.recall:.1%}  F1 {m_cs.f1:.1%}")
    print(f"  delta(C2 pure - static k=2) F1: {m_c2.f1 - m_s2.f1:+.2%}"
          f"   delta(C2+fallback - static k=2): {m_cs.f1 - m_s2.f1:+.2%}")

    print(f"\n--- C2 (pure) vs static k=2, per category (D never-worse check) ---")
    per_category_delta(pairs, c2, static_k2)
    print(f"  (C2+fallback removes any regression by construction; verified >= static above)")

    geo_oracle = lambda p: routed_predict(p, proba[p.copy], classes, thresholds, tau,
                                          geo=True, oracle_geo=True,
                                          is_geo=(p.category in GEO_POSITIVES))
    print(f"\n--- Base-rate sensitivity: F1 vs assumed positive prevalence ---")
    base_rate_sensitivity(pairs, [
        ("static k=1", static_k1), ("static k=2", static_k2),
        ("C2+fallback", c2_shipped), ("geo-oracle(ceiling)", geo_oracle),
    ])
    print("  geo-oracle = unreachable ceiling: the geometry relax with a PERFECT trigger.")
    print("  The router's real trigger reaches it within-distribution but ~0 cross-generator (8.4).")

    print(f"\n--- Geometry counterfactual (costing the rejected flag, 8.5) ---")
    geometry_counterfactual(pairs, proba, classes, thresholds, tau)

    print(f"\n--- C3 weighted vote (EXTENSION -- not the headline) ---")
    wpred = lambda p: weighted_predict(p, proba[p.copy], classes, thresholds, detmap, weighted_d)
    m_w, _ = overall(pairs, wpred)
    print(f"  weighted vote: P {m_w.precision:.1%}  R {m_w.recall:.1%}  F1 {m_w.f1:.1%}"
          f"   (vs static k=2 F1 {m_s2.f1:.1%}, floor {floor:.1%})")
    print(f"  per category:")
    per_category_detection(pairs, wpred)


if __name__ == "__main__":
    main()
