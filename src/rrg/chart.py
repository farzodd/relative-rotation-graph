"""Render the RRG.

Four quadrants centred on (100, 100), one fading tail per symbol showing the
last N periods of travel, and a head marker at the current position. Axes are
scaled symmetrically about 100 so that visual distance from the centre means the
same thing in every direction.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this runs unattended

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from .rrg import RRGResult

# Quadrant fills. Deliberately low-saturation: the tails carry the information,
# the quadrants are only a backdrop.
QUADRANT_STYLE = {
    "Leading": ("#1b7f5f", "#e8f5f0"),
    "Weakening": ("#b8860b", "#fbf4e0"),
    "Lagging": ("#b23a48", "#fbecee"),
    "Improving": ("#2b6cb0", "#e8f0fa"),
}

# Distinguishable at 11 colours without relying on hue alone to separate
# neighbours in the legend.
SERIES_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b",
    "#e377c2", "#17becf", "#7f7f7f", "#bcbd22", "#393b79",
]


def _limits(result: RRGResult) -> tuple[float, float]:
    """Symmetric bounds about 100 covering every plotted point."""
    n = result.config.tail_length
    xs = result.rs_ratio.iloc[-n:].to_numpy()
    ys = result.rs_momentum.iloc[-n:].to_numpy()
    reach = float(np.nanmax(np.abs(np.concatenate([xs, ys]) - 100.0)))
    pad = max(reach * 0.18, 0.15)
    return 100.0 - reach - pad, 100.0 + reach + pad


def render(result: RRGResult, path: str | Path | None = None) -> Path:
    cfg = result.config
    lo, hi = _limits(result)

    fig, ax = plt.subplots(figsize=(cfg.figure_width, cfg.figure_height))

    # Quadrant backdrop.
    ax.add_patch(plt.Rectangle((100, 100), hi - 100, hi - 100, color=QUADRANT_STYLE["Leading"][1], zorder=0))
    ax.add_patch(plt.Rectangle((100, lo), hi - 100, 100 - lo, color=QUADRANT_STYLE["Weakening"][1], zorder=0))
    ax.add_patch(plt.Rectangle((lo, lo), 100 - lo, 100 - lo, color=QUADRANT_STYLE["Lagging"][1], zorder=0))
    ax.add_patch(plt.Rectangle((lo, 100), 100 - lo, hi - 100, color=QUADRANT_STYLE["Improving"][1], zorder=0))

    corners = {
        "Leading": (hi, hi, "right", "top"),
        "Weakening": (hi, lo, "right", "bottom"),
        "Lagging": (lo, lo, "left", "bottom"),
        "Improving": (lo, hi, "left", "top"),
    }
    for name, (x, y, ha, va) in corners.items():
        nudge_x = -0.012 * (hi - lo) if ha == "right" else 0.012 * (hi - lo)
        nudge_y = -0.012 * (hi - lo) if va == "top" else 0.012 * (hi - lo)
        ax.text(
            x + nudge_x, y + nudge_y, name.upper(),
            ha=ha, va=va, fontsize=11, fontweight="bold",
            color=QUADRANT_STYLE[name][0], alpha=0.65, zorder=1,
        )

    ax.axhline(100, color="#5a5a5a", lw=1.0, zorder=2)
    ax.axvline(100, color="#5a5a5a", lw=1.0, zorder=2)

    handles: list[Line2D] = []
    for i, symbol in enumerate(result.symbols):
        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        tail = result.tail(symbol)
        xs = tail["rs_ratio"].to_numpy()
        ys = tail["rs_momentum"].to_numpy()

        # Segment-by-segment so the tail fades from oldest to newest — direction
        # of travel is readable without an arrow at every point.
        for j in range(len(xs) - 1):
            ax.plot(
                xs[j:j + 2], ys[j:j + 2],
                color=color, lw=1.1 + 1.4 * (j / max(len(xs) - 2, 1)),
                alpha=0.25 + 0.55 * (j / max(len(xs) - 2, 1)),
                solid_capstyle="round", zorder=3,
            )

        ax.scatter(xs[:-1], ys[:-1], s=9, color=color, alpha=0.35, zorder=3, linewidths=0)
        ax.scatter(
            xs[-1], ys[-1], s=115, color=color,
            edgecolor="white", linewidth=1.6, zorder=5,
        )
        ax.annotate(
            symbol,
            (xs[-1], ys[-1]),
            textcoords="offset points", xytext=(9, 6),
            fontsize=9.5, fontweight="bold", color=color, zorder=6,
        )
        handles.append(
            Line2D([], [], color=color, marker="o", lw=2, markersize=6,
                   label=f"{symbol} — {cfg.label(symbol)}")
        )

    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("RS-Ratio  (relative strength vs benchmark)", fontsize=10.5)
    ax.set_ylabel("RS-Momentum  (rate of change of relative strength)", fontsize=10.5)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, ls=":", lw=0.5, color="#9a9a9a", alpha=0.4, zorder=1)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#bbbbbb")

    as_of = result.as_of.date()
    heading = f"Relative Rotation Graph vs {result.benchmark_symbol}"
    if cfg.profile:
        heading += f"  —  {cfg.profile}"
    ax.set_title(
        f"{heading}\n{cfg.tail_length}-period tail, as of {as_of}",
        fontsize=13.5, fontweight="bold", pad=14,
    )

    ax.legend(
        handles=handles, loc="center left", bbox_to_anchor=(1.02, 0.5),
        frameon=False, fontsize=9, title="Universe", title_fontsize=9.5,
    )

    footer = cfg.stamp()
    if result.dropped:
        footer += f"  |  dropped: {', '.join(sorted(result.dropped))}"
    fig.text(0.5, 0.015, footer, ha="center", fontsize=7.5, color="#666666")
    fig.text(
        0.5, 0.001,
        "Public approximation of the RRG concept — not JdK RS-Ratio/RS-Momentum. "
        "Analysis tool, not investment advice.",
        ha="center", fontsize=6.8, color="#999999",
    )

    if path is None:
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        # The profile belongs in the filename: without it, rendering every
        # profile in one run has each overwrite the last.
        suffix = f"_{cfg.profile}" if cfg.profile else ""
        path = cfg.output_dir / f"rrg_{result.benchmark_symbol.lower()}{suffix}_{as_of}.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=cfg.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path
