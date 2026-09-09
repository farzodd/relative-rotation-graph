"""Build the weekly report: one section per view, each reviewable on its own.

The two views disagree about which quadrant a member is in — that is the point
of running both — so each gets its own chart, its own table, and its own list of
what moved. Merging them into a single table would hide exactly the
disagreements worth looking at.

"What changed" compares against the last report that was actually delivered, not
the last one rendered. Rendering a preview twice must not consume the
comparison, or the first real send after a few dry runs would report nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd

from .config import Config
from .rrg import RRGResult

QUADRANT_COLOR = {
    "Leading": "#1b7f5f",
    "Weakening": "#b8860b",
    "Lagging": "#b23a48",
    "Improving": "#2b6cb0",
}

DISCLAIMER = (
    "Public approximation of the RRG concept — not JdK RS-Ratio/RS-Momentum. "
    "A visualisation of where securities sit relative to a benchmark. "
    "Not investment advice."
)


@dataclass
class SectionData:
    """One view's contribution to the report."""

    profile: str
    description: str
    result: RRGResult
    chart_path: Path
    moves: list[tuple[str, str, str]]      # symbol, from quadrant, to quadrant
    previous_as_of: str | None


# --------------------------------------------------------------------------
# State: what the last delivered report said
# --------------------------------------------------------------------------


def _state_path(cfg: Config, profile: str) -> Path:
    return cfg.state_dir / f"{cfg.benchmark.lower()}_{profile}.json"


def load_state(cfg: Config, profile: str) -> dict | None:
    path = _state_path(cfg, profile)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_state(cfg: Config, profile: str, result: RRGResult) -> None:
    """Record what this report told the reader, for next week's comparison."""
    path = _state_path(cfg, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    latest = result.latest()
    path.write_text(
        json.dumps(
            {
                "as_of": str(result.as_of.date()),
                "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "benchmark": cfg.benchmark,
                "quadrants": {s: latest.loc[s, "quadrant"] for s in latest.index},
            },
            indent=2,
        )
        + "\n"
    )


def diff_quadrants(
    previous: dict | None, result: RRGResult
) -> tuple[list[tuple[str, str, str]], str | None]:
    """Members whose quadrant changed since the last delivered report."""
    if not previous:
        return [], None
    before = previous.get("quadrants", {})
    latest = result.latest()
    moves = [
        (symbol, before[symbol], latest.loc[symbol, "quadrant"])
        for symbol in latest.index
        if symbol in before and before[symbol] != latest.loc[symbol, "quadrant"]
    ]
    return moves, previous.get("as_of")


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def _table(cfg: Config, result: RRGResult) -> str:
    latest = result.latest()
    centre = 100.0
    rows = []
    for symbol in latest.index:
        row = latest.loc[symbol]
        colour = QUADRANT_COLOR.get(row["quadrant"], "#333")
        rows.append(
            f'<tr>'
            f'<td style="padding:5px 10px;font-weight:600;">{escape(symbol)}</td>'
            f'<td style="padding:5px 10px;color:#555;">{escape(cfg.label(symbol))}</td>'
            f'<td style="padding:5px 10px;text-align:right;font-variant-numeric:tabular-nums;">'
            f'{row["rs_ratio"] - centre:+.2f}</td>'
            f'<td style="padding:5px 10px;text-align:right;font-variant-numeric:tabular-nums;">'
            f'{row["rs_momentum"] - centre:+.2f}</td>'
            f'<td style="padding:5px 10px;color:{colour};font-weight:600;">'
            f'{escape(row["quadrant"])}</td>'
            f'</tr>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;font-size:14px;width:100%;max-width:560px;">'
        '<thead><tr style="border-bottom:2px solid #ddd;text-align:left;">'
        '<th style="padding:5px 10px;">Symbol</th>'
        '<th style="padding:5px 10px;">Name</th>'
        '<th style="padding:5px 10px;text-align:right;">RS-Ratio</th>'
        '<th style="padding:5px 10px;text-align:right;">RS-Mom</th>'
        '<th style="padding:5px 10px;">Quadrant</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
    )


def _moves_block(section: SectionData) -> str:
    if section.previous_as_of is None:
        return (
            '<p style="color:#777;font-size:14px;margin:12px 0;">'
            'No previous report to compare against — this is the first delivery '
            'for this view.</p>'
        )
    if not section.moves:
        return (
            f'<p style="color:#777;font-size:14px;margin:12px 0;">'
            f'No quadrant changes since {escape(section.previous_as_of)}.</p>'
        )
    items = "".join(
        f'<li style="margin:3px 0;"><strong>{escape(sym)}</strong> '
        f'<span style="color:{QUADRANT_COLOR.get(old, "#333")};">{escape(old)}</span>'
        f' &rarr; '
        f'<span style="color:{QUADRANT_COLOR.get(new, "#333")};font-weight:600;">'
        f'{escape(new)}</span></li>'
        for sym, old, new in section.moves
    )
    return (
        f'<p style="font-size:14px;margin:12px 0 6px;">'
        f'<strong>{len(section.moves)} change(s) since {escape(section.previous_as_of)}:</strong></p>'
        f'<ul style="font-size:14px;margin:0 0 12px;padding-left:22px;">{items}</ul>'
    )


def build_html(cfg: Config, sections: list[SectionData], as_of: str) -> str:
    blocks = []
    for i, section in enumerate(sections):
        blocks.append(
            f'<h2 style="font-size:18px;margin:32px 0 4px;">'
            f'{escape(section.profile)}</h2>'
            f'<p style="color:#666;font-size:13px;margin:0 0 14px;">'
            f'{escape(section.description)}</p>'
            f'<img src="cid:chart{i}" alt="RRG chart for {escape(section.profile)}" '
            f'style="width:100%;max-width:660px;height:auto;border:1px solid #eee;" />'
            f'{_moves_block(section)}'
            f'{_table(cfg, section.result)}'
        )
        if i < len(sections) - 1:
            blocks.append('<hr style="border:0;border-top:1px solid #e5e5e5;margin:36px 0;" />')

    # An explicit background is not decoration. Most mail clients default to dark
    # mode now, and with a transparent background this document's near-black text
    # renders on a dark surface and is unreadable. Setting both colours together
    # is what keeps the pair legible wherever it lands.
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,'
        'Arial,sans-serif;color:#222222;background-color:#ffffff;'
        'max-width:700px;margin:0 auto;padding:16px;">'
        f'<h1 style="font-size:22px;margin:0 0 2px;">Sector Relative Rotation</h1>'
        f'<p style="color:#666;font-size:14px;margin:0;">'
        f'vs {escape(cfg.benchmark)} &middot; week ending {escape(as_of)}</p>'
        + "".join(blocks)
        + '<hr style="border:0;border-top:1px solid #e5e5e5;margin:36px 0 12px;" />'
        f'<p style="color:#999;font-size:11px;line-height:1.5;">{escape(DISCLAIMER)}</p>'
        '</div>'
    )


def build_text(cfg: Config, sections: list[SectionData], as_of: str) -> str:
    """Plain-text alternative. Some clients prefer it, and its absence hurts
    deliverability."""
    out = [f"Sector Relative Rotation vs {cfg.benchmark} — week ending {as_of}", ""]
    for section in sections:
        out += [f"== {section.profile} ==", section.description, ""]
        if section.previous_as_of is None:
            out.append("No previous report to compare against.")
        elif not section.moves:
            out.append(f"No quadrant changes since {section.previous_as_of}.")
        else:
            out.append(f"{len(section.moves)} change(s) since {section.previous_as_of}:")
            out += [f"  {s}: {a} -> {b}" for s, a, b in section.moves]
        out.append("")
        latest = section.result.latest()
        for symbol in latest.index:
            row = latest.loc[symbol]
            out.append(
                f"  {symbol:<5} {row['rs_ratio'] - 100:+6.2f} {row['rs_momentum'] - 100:+6.2f}  "
                f"{row['quadrant']}"
            )
        out.append("")
    out += ["-" * 60, DISCLAIMER]
    return "\n".join(out)


def write_preview(
    cfg: Config, html: str, sections: list[SectionData], as_of: str
) -> Path:
    """Save the rendered HTML so it can be reviewed before anything is sent."""
    path = cfg.output_dir / f"report_{cfg.benchmark.lower()}_{as_of}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Charts are referenced by cid: in the email; for the on-disk preview point
    # each one at its own PNG. Mapping by section index rather than by globbing
    # the directory — a glob would silently pair the wrong chart with a section
    # whenever filenames sort differently from profile order.
    preview = html
    for i, section in enumerate(sections):
        preview = preview.replace(f'src="cid:chart{i}"', f'src="{section.chart_path.name}"')
    path.write_text(preview)
    return path
