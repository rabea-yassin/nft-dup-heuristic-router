"""Draw every chart in the final report (and the poster) from verified numbers.

Each number is hard-coded with the PROGRESS.md section it comes from, so the
figures can be regenerated without the (git-ignored) datasets and score caches.

    python3 report/figures/make_figures.py          # writes report/figures/*.pdf
                                                    # and poster/assets/*.png
    python3 report/figures/make_figures.py --deck-dir DIR   # also the slide-10
                                                    # version of the time chart
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
POSTER_ASSETS = HERE.parent.parent / "poster" / "assets"

# Validated categorical slots 1-2 (dataviz reference palette, light mode).
ORB = "#2a78d6"      # our replacement / the swap
SHASH = "#eb6834"    # sHash / the paper's detector
ORB_128 = "#86b5ec"  # ORB at 128 landmarks: a lighter step of ORB's blue
# The vote-rule chart encodes rules, not signals, so it gets its own pair
# (slots 7 and 3, validated together; aqua is direct-labelled for contrast).
RULE_2 = "#4a3aa7"
RULE_1 = "#1baf7a"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": INK_2,
    "axes.labelcolor": INK,
    "axes.linewidth": 0.6,
    "axes.facecolor": "none",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "legend.frameon": False,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.04,
})


def save(fig, name):
    fig.savefig(HERE / f"{name}.pdf")
    POSTER_ASSETS.mkdir(parents=True, exist_ok=True)
    fig.savefig(POSTER_ASSETS / f"{name}.png", dpi=300)
    plt.close(fig)


def grid_y(ax):
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def paired_bars(ax, groups, left, right, labels, colors, ymax=100):
    """Two bars per group with a 2 pt gap and value labels above each bar."""
    width, gap = 0.36, 0.02
    for i, (a, b) in enumerate(zip(left, right)):
        for x, v, c in ((i - width / 2 - gap / 2, a, colors[0]),
                        (i + width / 2 + gap / 2, b, colors[1])):
            ax.bar(x, v, width, color=c, edgecolor="white", linewidth=0.8)
            ax.text(x, v + ymax * 0.015, f"{v:.1f}", ha="center", va="bottom",
                    color=INK, fontsize=8.5)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups)
    ax.set_ylim(0, ymax)
    ax.tick_params(axis="x", length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colors]
    ax.legend(handles, labels, loc="upper left", ncol=2, fontsize=8.5,
              bbox_to_anchor=(0, 1.12))
    grid_y(ax)


def head_to_head():
    # PROGRESS §5: geometric subset, 3,600 copies / 2,400 non-copies.
    # sHash at its oracle threshold (dist <= 28), ORB train-tuned (inliers > 16).
    fig, ax = plt.subplots(figsize=(4.6, 2.8))
    paired_bars(ax, ["F1", "Recall at equal precision\n(91.2%)"],
                [76.4, 27.2], [90.6, 90.0],
                ["sHash (oracle threshold)", "ORB (train-tuned)"], [SHASH, ORB])
    ax.plot([-0.45, 0.45], [75.0, 75.0], ls=(0, (3, 2)), color=INK_2, lw=0.9)
    ax.text(0.47, 75.0, "flag-all\nF1 = 75.0", color=INK_2, fontsize=7.5,
            va="center")
    ax.set_ylabel("%")
    save(fig, "fig_head_to_head")


def by_edit_type():
    # PROGRESS §5 per-category table, ORB at inliers > 16 (overall P 91.2%).
    rows = [("Text / logo / emoji", 100.0), ("Exact copy", 100.0),
            ("Background colour", 98.9), ("Crop / resize / reposition", 98.3),
            ("Flip / rotate*", 81.7), ("Colour swap / saturate", 79.8),
            ("Pixelated", 28.5)]
    fig, ax = plt.subplots(figsize=(4.6, 2.6))
    names = [r[0] for r in rows][::-1]
    vals = [r[1] for r in rows][::-1]
    ax.barh(names, vals, height=0.62, color=ORB, edgecolor="white", lw=0.8)
    for y, v in enumerate(vals):
        ax.text(v + 1.2, y, f"{v:.1f}", va="center", color=INK, fontsize=8.5)
    ax.set_xlim(0, 110)
    ax.set_xlabel("ORB recall (% of that edit's copies caught)")
    ax.xaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)
    save(fig, "fig_by_edit_type")


def whole_detector():
    # PROGRESS §9.1 (our test set, the paper's own thresholds) and §9.5 (the authors' set), 2-of-4 vote.
    fig, ax = plt.subplots(figsize=(5.2, 2.9))
    paired_bars(ax,
                ["Paper's own\nthresholds", "Thresholds re-tuned\nto our data",
                 "Authors' set,\npaper's thresholds"],
                [77.5, 81.7, 60.0], [86.4, 87.5, 63.3],
                ["with sHash (the paper)", "with ORB in place of sHash"],
                [SHASH, ORB])
    ax.set_ylabel("F1 (%)")
    save(fig, "fig_whole_detector")


def storage_curve():
    # PROGRESS §6: oracle threshold per budget, geometric subset. Degenerate
    # rows (N=24, 16: best threshold collapsed to flag-all) are left out.
    pts = [(14560, 90.8, "455 landmarks"), (7760, 89.9, None),
           (3888, 87.5, "128"), (2928, 85.9, None), (2048, 83.5, None),
           (1488, 79.8, None), (1024, 76.1, "32")]
    fig, ax = plt.subplots(figsize=(5.0, 2.9))
    ax.axvspan(300, 500, color=GRID, lw=0)
    ax.text(390, 84.0, "one mint\ntransaction\n(300–500 B)", ha="center",
            va="top", fontsize=7.5, color=INK_2)
    xs, ys = zip(*[(p[0], p[1]) for p in pts])
    ax.plot(xs, ys, color=ORB, lw=2, marker="o", ms=5,
            markeredgecolor="white", markeredgewidth=1, label="ORB")
    for x, y, lab in pts:
        if lab:
            ax.annotate(lab, (x, y), textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=7.5, color=INK)
    ax.plot([32], [76.4], marker="D", ms=6, color=SHASH,
            markeredgecolor="white", markeredgewidth=1, ls="none",
            label="sHash (median 32 B, oracle)")
    ax.axhline(75.0, ls=(0, (3, 2)), color=INK_2, lw=0.9)
    ax.text(20000, 74.6, "flag-all 75.0", ha="right", va="top", fontsize=7.5,
            color=INK_2)
    ax.set_xscale("log")
    ax.set_xlim(20, 30000)
    ax.set_ylim(72, 93)
    ax.set_xlabel("Bytes stored per image (log scale)")
    ax.set_ylabel("F1 on geometric subset (%)")
    ax.legend(loc="upper left", fontsize=8)
    grid_y(ax)
    save(fig, "fig_storage")


def time_bars(path, figsize=(5.0, 3.4), fs=12):
    # PROGRESS §6, like for like (C sHash vs OpenCV ORB), medians per item.
    # Two panels on ordinary linear axes in ms: the fingerprint bars come out
    # about equal, and sHash's 0.1 us pair bar is invisible next to ORB's 34 ms --
    # which is the finding, so a linear scale shows it honestly.
    # ORB at 128 landmarks (the size curve's sweet spot) is the same signal, so a
    # lighter step of ORB's blue, told apart by its direct label.
    panels = [("Fingerprint a new image", [("sHash", 1.46, "1.5 ms", SHASH),
                                           ("ORB, 455", 0.94, "0.9 ms", ORB),
                                           ("ORB, 128", 0.72, "0.7 ms", ORB_128)], 2.0),
              ("Compare one pair", [("sHash", 0.000104, "≈ 0.1 µs", SHASH),
                                    ("ORB, 455", 34.5, "34 ms", ORB),
                                    ("ORB, 128", 24.3, "24 ms", ORB_128)], 40.0)]
    fig, axes = plt.subplots(2, 1, figsize=figsize, gridspec_kw=dict(hspace=1.0))
    for ax, (title, bars, xmax) in zip(axes, panels):
        names = [b[0] for b in bars][::-1]
        vals = [b[1] for b in bars][::-1]
        ax.barh(names, vals, height=0.62, color=[b[3] for b in bars][::-1], edgecolor="white", lw=0.8)
        for yy, (v, txt) in enumerate(zip(vals, [b[2] for b in bars][::-1])):
            ax.text(v + xmax * 0.02, yy, txt, va="center", fontsize=fs, color=INK, fontweight="bold")
        ax.set_xlim(0, xmax)
        ax.set_xticks([0, 0.5, 1.0, 1.5, 2.0] if xmax == 2.0 else [0, 10, 20, 30, 40])
        ax.set_title(title, loc="left", fontsize=fs + 1, fontweight="bold", color=INK, pad=8)
        ax.set_xlabel("milliseconds", fontsize=fs - 2)
        ax.set_ylim(-0.6, len(bars) - 0.4)
        ax.tick_params(axis="y", length=0, labelsize=fs)
        ax.tick_params(axis="x", labelsize=fs - 2)
        ax.xaxis.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
    fig.savefig(path, dpi=300, transparent=True)
    plt.close(fig)


def prevalence():
    # PROGRESS §9.2: static ORB panel, F1 reweighted to an assumed prevalence.
    prev = [2, 10, 25, 50, 82.6]
    k1 = [15.4, 49.1, 73.0, 87.1, 94.3]
    k2 = [26.7, 62.0, 77.4, 84.4, 87.5]
    fig, ax = plt.subplots(figsize=(4.6, 2.7))
    ax.plot(prev, k2, color=RULE_2, lw=2, marker="o", ms=5,
            markeredgecolor="white", label="2 of 4 agree (the paper's rule)")
    ax.plot(prev, k1, color=RULE_1, lw=2, marker="s", ms=5,
            markeredgecolor="white", label="1 of 4 is enough")
    ax.annotate("k = 2", (2, 26.7), textcoords="offset points", xytext=(6, 4),
                fontsize=7.5, color=INK)
    ax.annotate("k = 1", (2, 15.4), textcoords="offset points", xytext=(6, -10),
                fontsize=7.5, color=INK)
    ax.axvline(82.6, ls=(0, (3, 2)), color=INK_2, lw=0.9)
    ax.text(81, 45, "our test set\n(82.6% copies)", ha="right", fontsize=7.5,
            color=INK_2)
    ax.set_xlabel("Assumed share of copies among queries (%)")
    ax.set_ylabel("F1 (%)")
    ax.set_xlim(0, 90)
    ax.set_ylim(0, 100)
    ax.legend(loc="lower right", fontsize=8)
    grid_y(ax)
    save(fig, "fig_prevalence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--deck-dir", type=Path, help="also write the deck version of the time chart here")
    args = parser.parse_args()
    head_to_head()
    by_edit_type()
    whole_detector()
    storage_curve()
    prevalence()
    time_bars(HERE / "fig_time.pdf", figsize=(5.2, 3.0), fs=9)  # report
    time_bars(POSTER_ASSETS / "fig_time_bars.png")  # poster half-column
    if args.deck_dir:
        args.deck_dir.mkdir(parents=True, exist_ok=True)
        time_bars(args.deck_dir / "slide10_time_chart.png")
    print("figures written to", HERE, "and", POSTER_ASSETS)
