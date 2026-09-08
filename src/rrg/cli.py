"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from .chart import render
from .config import ConfigError, load_config
from .data import DataError, build_price_panel
from .rrg import compute


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rrg",
        description="Generate a Relative Rotation Graph for a universe against a benchmark.",
    )
    parser.add_argument("-c", "--config", default="config.toml", help="path to config.toml")
    parser.add_argument("-o", "--output", default=None, help="output PNG path")
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
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
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

    if not args.no_chart:
        path = render(result, args.output)
        print(f"\nChart: {path}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
