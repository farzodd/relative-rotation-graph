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
import matplotlib.ticker as mticker
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

# One entry per universe member. The list wraps if the universe outgrows it,
# and a wrapped colour is a real defect — two series become indistinguishable in
# both the plot and the legend — so keep this comfortably longer than the
# universe. 16 entries against a 13-member universe today.
SERIES_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b",
    "#e377c2", "#17becf", "#7f7f7f", "#bcbd22", "#393b79", "#00857c",
    "#b5179e", "#5c4033", "#4361ee", "#f77f00",
]


def _limits(result: RRGResult) -> tuple[float, float]:
    """Symmetric bounds about the centre, in plot coordinates.

    Absolute mode uses a *fixed* frame. Expanding to fit the widest tail is what
    let one outlier squeeze everyone else into the middle — SMH's tail reached
    1.67 while the median member's reached 0.46, so the typical member occupied
    26% of the half-frame regardless of any divisor. A fixed frame also keeps
    week-to-week charts comparable, which an auto-fitting one cannot be.
    Anything outside is drawn at the boundary, not dropped.
    """
    cfg = result.config
    if cfg.is_absolute:
        return -cfg.frame_limit, cfg.frame_limit

    n = cfg.tail_length
    xs = result.rs_ratio.iloc[-n:].to_numpy()
    ys = result.rs_momentum.iloc[-n:].to_numpy()
    reach = float(np.nanmax(np.abs(np.concatenate([xs, ys]) - 100.0)))
    pad = max(reach * 0.18, 0.15)
    return 100.0 - reach - pad, 100.0 + reach + pad


def render(result: RRGResult, path: str | Path | None = None) -> Path:
    cfg = result.config
    lo, hi = _limits(result)
    # Absolute mode plots deviations centred on 0; asinh must transform
    # around the origin, and asinh(100) is effectively linear.
    c = 0.0 if cfg.is_absolute else 100.0
    shift = c - 100.0
    def co(v):
        return v + shift

    fig, ax = plt.subplots(figsize=(cfg.figure_width, cfg.figure_height))

    # Quadrant backdrop.
    ax.add_patch(plt.Rectangle((c, c), hi - c, hi - c, color=QUADRANT_STYLE["Leading"][1], zorder=0))
    ax.add_patch(plt.Rectangle((c, lo), hi - c, c - lo, color=QUADRANT_STYLE["Weakening"][1], zorder=0))
    ax.add_patch(plt.Rectangle((lo, lo), c - lo, c - lo, color=QUADRANT_STYLE["Lagging"][1], zorder=0))
    ax.add_patch(plt.Rectangle((lo, c), c - lo, hi - c, color=QUADRANT_STYLE["Improving"][1], zorder=0))

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

    ax.axhline(c, color="#5a5a5a", lw=1.0, zorder=2)
    ax.axvline(c, color="#5a5a5a", lw=1.0, zorder=2)

    handles: list[Line2D] = []
    for i, symbol in enumerate(result.symbols):
        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        tail = result.tail(symbol)
        xs = co(tail["rs_ratio"].to_numpy())
        ys = co(tail["rs_momentum"].to_numpy())

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

        # A fixed frame means a member can sit outside it. Clamp it to the edge,
        # mark it hollow so it is not mistaken for a real position, and put the
        # true coordinate in the label — never silently drop it.
        head_x, head_y = float(xs[-1]), float(ys[-1])
        off_scale = not (lo <= head_x <= hi and lo <= head_y <= hi)
        edge = 0.985 * (hi - c)
        draw_x = min(max(head_x, c - edge), c + edge)
        draw_y = min(max(head_y, c - edge), c + edge)

        if off_scale:
            ax.scatter(
                draw_x, draw_y, s=150, marker="^", facecolor="white",
                edgecolor=color, linewidth=2.0, zorder=5,
            )
            label = f"{symbol} ({head_x - c:+.1f}, {head_y - c:+.1f})"
        else:
            ax.scatter(
                draw_x, draw_y, s=115, color=color,
                edgecolor="white", linewidth=1.6, zorder=5,
            )
            label = symbol

        # Clamped markers sit on the frame edge, where the default outward
        # offset pushes the label off-canvas or onto the quadrant caption.
        # Point it inward instead.
        ox, oy, ha = 9, 6, "left"
        if off_scale:
            near = 0.98 * edge
            if draw_x >= c + near:
                ox, ha = -12, "right"
            if draw_y <= c - near:
                oy = 14
            elif draw_y >= c + near:
                oy = -18

        ax.annotate(
            label,
            (draw_x, draw_y),
            textcoords="offset points", xytext=(ox, oy), ha=ha,
            fontsize=9.5, fontweight="bold", color=color, zorder=6,
        )
        handles.append(
            Line2D([], [], color=color, marker="o", lw=2, markersize=6,
                   label=f"{symbol} — {cfg.label(symbol)}")
        )

    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)

    if cfg.is_absolute:
        # Coordinates are stored centred on 100 so quadrant logic stays shared,
        # but the reader wants the deviation, which is the meaningful quantity.
        # Scale first: set_xscale installs its own locator and formatter, so
        # setting ours before it would be silently overwritten — which is how
        # the asinh axes ended up labelled in scientific notation.
        if cfg.axis_scale == "asinh":
            ax.set_xscale("asinh", linear_width=cfg.asinh_linear_width)
            ax.set_yscale("asinh", linear_width=cfg.asinh_linear_width)
            ticks = [-1.0, -0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5, 1.0]
            ax.set_xticks(ticks)
            ax.set_yticks(ticks)
            ax.xaxis.set_minor_locator(mticker.NullLocator())
            ax.yaxis.set_minor_locator(mticker.NullLocator())

        fmt = mticker.FuncFormatter(lambda v, _: f"{v:+.2f}".rstrip("0").rstrip("."))
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
        which = ("widest member" if cfg.scale_percentile >= 100
                 else f"the p{cfg.scale_percentile:g} member")
        unit = f"{cfg.sigma_multiple:g}σ of {which}"
        ax.set_xlabel(f"RS-Ratio vs {result.benchmark_symbol}   (1.0 = {unit})", fontsize=10.5)
        ax.set_ylabel(f"RS-Momentum   (1.0 = {unit})", fontsize=10.5)

        # The benchmark's RS against itself is flat, so it lands exactly on the
        # origin. Drawing it makes "everything is below the benchmark" legible
        # as a picture rather than something to infer from the numbers.
        ax.scatter(
            c, c, s=190, marker="P", color="#2b2b2b",
            edgecolor="white", linewidth=1.6, zorder=7,
        )
        ax.annotate(
            result.benchmark_symbol, (c, c),
            textcoords="offset points", xytext=(11, -16),
            fontsize=9.5, fontweight="bold", color="#2b2b2b", zorder=7,
        )
    else:
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
    if cfg.is_absolute and result.scale_ratio:
        footer += f"  |  {cfg.axis_scale} axes, frame ±{cfg.frame_limit:g}"
        footer += (f"  |  scale x{result.scale_ratio:.3f} y{result.scale_momentum:.3f}")
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
