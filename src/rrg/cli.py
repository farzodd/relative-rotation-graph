"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from .chart import render
from .config import ConfigError, load_config, profile_names
from .data import DataError, build_price_panel
from .diagnostics import compute_diagnostics
from . import mailer, report
from .rrg import compute


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rrg",
        description="Generate a Relative Rotation Graph for a universe against a benchmark.",
    )
    parser.add_argument("-c", "--config", default="config.toml", help="path to config.toml")
    parser.add_argument("-o", "--output", default=None, help="output PNG path")
    parser.add_argument("-p", "--profile", default=None, help="named profile from config.toml")
    parser.add_argument(
        "-b", "--benchmark", default=None,
        help="override the benchmark (e.g. RSP) without editing config.toml",
    )
    parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="render every profile and print a comparison of their diagnostics",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="print stability/responsiveness statistics for the run",
    )
    parser.add_argument(
        "--explain",
        metavar="SYMBOL",
        help="print every intermediate value for one symbol and exit without charting",
    )
    parser.add_argument(
        "--periods", type=int, default=15, help="rows to show with --explain (default 15)"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="ignore cached prices and refetch"
    )
    parser.add_argument("--no-chart", action="store_true", help="compute and print, skip the PNG")
    parser.add_argument(
        "--report", action="store_true",
        help="build the weekly report and write an HTML preview (does not send)",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="with --report, actually deliver it. Without this nothing is sent.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.report or args.send:
        return _run_report(args)

    if args.all_profiles:
        return _run_all_profiles(args)

    try:
        cfg = load_config(args.config, profile=args.profile, benchmark=args.benchmark)
        panel = build_price_panel(cfg, use_cache=not args.no_cache)
        result = compute(panel, cfg)
    except (ConfigError, DataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if panel.dropped:
        print("Dropped symbols:")
        for symbol, reason in sorted(panel.dropped.items()):
            print(f"  {symbol}: {reason}")
        print()

    if args.explain:
        try:
            frame = result.explain(args.explain.upper(), periods=args.periods)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        with pd.option_context("display.float_format", lambda v: f"{v:.4f}"):
            print(f"Intermediates for {args.explain.upper()} vs {cfg.benchmark} "
                  f"({cfg.normalization}, {cfg.interval} bars):\n")
            print(frame.to_string())
        return 0

    latest = result.latest()
    with pd.option_context("display.float_format", lambda v: f"{v:.2f}"):
        print(f"RRG vs {cfg.benchmark} as of {result.as_of.date()} "
              f"({len(result.symbols)} symbols, {cfg.normalization}):\n")
        print(latest.to_string())

    if args.diagnostics:
        diag = compute_diagnostics(result)
        print(f"\nDiagnostics ({diag.profile}, {diag.bars} bars):")
        for key, value in diag.as_row().items():
            if key != "profile":
                print(f"  {key:20} {value}")

    if not args.no_chart:
        path = render(result, args.output)
        print(f"\nChart: {path}")

    return 0




def _run_all_profiles(args) -> int:
    """Render every profile off one data fetch and compare their diagnostics."""
    try:
        names = profile_names(args.config)
    except (OSError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not names:
        print("error: no [profiles.*] declared in config.toml", file=sys.stderr)
        return 1

    rows, charts = [], []
    for name in names:
        try:
            cfg = load_config(args.config, profile=name, benchmark=args.benchmark)
            panel = build_price_panel(cfg, use_cache=not args.no_cache)
            result = compute(panel, cfg)
        except (ConfigError, DataError, ValueError) as exc:
            print(f"error in profile {name!r}: {exc}", file=sys.stderr)
            return 1

        diag = compute_diagnostics(result)
        rows.append(diag.as_row())
        if not args.no_chart:
            charts.append(render(result))

        print(f"\n=== {name} — {cfg.profile_description}")
        latest = result.latest()
        with pd.option_context("display.float_format", lambda v: f"{v:.2f}"):
            print(latest.to_string())

    print("\n\nProfile comparison")
    print(pd.DataFrame(rows).set_index("profile").to_string())

    if charts:
        print("\nCharts:")
        for path in charts:
            print(f"  {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def _run_report(args) -> int:
    """Build the report; send only when explicitly asked."""
    try:
        base = load_config(args.config, benchmark=args.benchmark)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not base.report_profiles:
        print("error: [report] profiles is empty in config.toml", file=sys.stderr)
        return 1

    sections, charts = [], []
    for name in base.report_profiles:
        try:
            cfg = load_config(args.config, profile=name, benchmark=args.benchmark)
            panel = build_price_panel(cfg, use_cache=not args.no_cache)
            result = compute(panel, cfg)
        except (ConfigError, DataError, ValueError) as exc:
            print(f"error in report profile {name!r}: {exc}", file=sys.stderr)
            return 1

        chart_path = render(result)
        moves, previous_as_of = report.diff_quadrants(report.load_state(cfg, name), result)
        sections.append(
            report.SectionData(
                profile=name,
                description=cfg.profile_description,
                result=result,
                chart_path=chart_path,
                moves=moves,
                previous_as_of=previous_as_of,
            )
        )
        charts.append(chart_path)

    as_of = str(sections[0].result.as_of.date())
    subject = base.report_subject.format(as_of=as_of)
    html = report.build_html(base, sections, as_of)
    text = report.build_text(base, sections, as_of)
    preview = report.write_preview(base, html, sections, as_of)

    for section in sections:
        label = (
            "first delivery" if section.previous_as_of is None
            else f"{len(section.moves)} change(s) since {section.previous_as_of}"
        )
        print(f"  {section.profile}: {label}")
        for symbol, old, new in section.moves:
            print(f"    {symbol}: {old} -> {new}")
    print(f"\nPreview: {preview}")

    mail = mailer.EmailSettings.from_env()
    check = mailer.preflight(mail, subject, charts)
    print("\nDelivery:")
    print(check.render())

    if not args.send:
        print("\nNothing sent. Re-run with --send to deliver.")
        return 0

    try:
        message_id = mailer.send_report(mail, subject, html, text, charts)
    except mailer.MailError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    # State advances only on a delivered report, so dry runs do not consume the
    # week-over-week comparison.
    for section in sections:
        cfg = load_config(args.config, profile=section.profile, benchmark=args.benchmark)
        report.save_state(cfg, section.profile, section.result)
    print(f"\nSent. SendGrid message id: {message_id}")
    return 0
