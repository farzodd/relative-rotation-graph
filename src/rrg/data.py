"""Price retrieval and validation.

Two providers behind one interface: Tiingo (needs a key, generous free tier) and
yfinance (no key, unofficial). Daily adjusted closes are fetched and cached, then
resampled locally to the configured bar interval — we never ask a provider for
weekly bars, because their week-ending convention is not ours to assume.

The README's data-handling contract is enforced in `build_price_panel`: symbols
that cannot be computed are dropped and named, never interpolated or
forward-filled into existence.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .config import Config

log = logging.getLogger(__name__)

TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"


class DataError(RuntimeError):
    """Raised when data cannot be retrieved at all."""


@dataclass
class PricePanel:
    """Aligned adjusted closes for a universe plus its benchmark.

    `prices` columns are the surviving universe symbols; `benchmark` is a Series
    on the identical index. Both are on the configured bar interval.
    """

    prices: pd.DataFrame
    benchmark: pd.Series
    benchmark_symbol: str
    dropped: dict[str, str]

    @property
    def symbols(self) -> list[str]:
        return list(self.prices.columns)

    @property
    def as_of(self) -> pd.Timestamp:
        return self.prices.index[-1]


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def _fetch_tiingo(symbol: str, start: datetime, end: datetime) -> pd.Series:
    token = os.environ.get("TIINGO_API_KEY")
    if not token:
        raise DataError(
            "TIINGO_API_KEY is not set. Get a free key at tiingo.com, then:\n"
            "  export TIINGO_API_KEY=...\n"
            "Or set provider = \"yfinance\" in config.toml to run without a key."
        )

    resp = requests.get(
        TIINGO_URL.format(symbol=symbol),
        params={
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
            "format": "json",
            "resampleFreq": "daily",
            "token": token,
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code == 404:
        raise DataError(f"{symbol}: not found on Tiingo")
    if resp.status_code == 429:
        raise DataError(f"{symbol}: Tiingo rate limit hit (free tier is 50 req/hour)")
    resp.raise_for_status()

    rows = resp.json()
    if not rows:
        raise DataError(f"{symbol}: Tiingo returned no rows for the requested window")

    frame = pd.DataFrame(rows)
    if "adjClose" not in frame:
        raise DataError(f"{symbol}: Tiingo response has no adjClose field")

    index = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None).dt.normalize()
    return pd.Series(frame["adjClose"].to_numpy(dtype=float), index=index, name=symbol)


def _fetch_yfinance(symbol: str, start: datetime, end: datetime) -> pd.Series:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - environment problem
        raise DataError("yfinance is not installed; run `uv sync`") from exc

    frame = yf.download(
        symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=True,  # 'Close' becomes the split/dividend adjusted close
        progress=False,
        actions=False,
        threads=False,
    )
    if frame is None or frame.empty:
        raise DataError(f"{symbol}: yfinance returned no rows")

    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)

    closes = frame["Close"].astype(float)
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    closes.name = symbol
    return closes


_FETCHERS = {"tiingo": _fetch_tiingo, "yfinance": _fetch_yfinance}


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def _cache_path(cfg: Config, symbol: str) -> Path:
    return cfg.cache_dir / cfg.provider / f"{symbol.upper()}.csv"


def _read_cache(path: Path, max_age_hours: float) -> pd.Series | None:
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        return None
    series = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    series.index = pd.to_datetime(series.index)
    return series.astype(float)


def _write_cache(path: Path, series: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    series.to_frame(name="adj_close").to_csv(path)


def fetch_daily_closes(cfg: Config, symbol: str, *, use_cache: bool = True) -> pd.Series:
    """Adjusted daily closes for one symbol, cached on disk."""
    path = _cache_path(cfg, symbol)
    if use_cache:
        cached = _read_cache(path, cfg.cache_max_age_hours)
        if cached is not None:
            log.debug("%s: cache hit (%d rows)", symbol, len(cached))
            return cached

    end = datetime.now(timezone.utc)
    # Pad the window: calendars lose ~30% of days to weekends and holidays, and
    # the warmup is counted in bars, not days.
    start = end - timedelta(days=int(cfg.lookback_years * 365.25) + 10)

    series = _FETCHERS[cfg.provider](symbol, start, end)
    series = series[~series.index.duplicated(keep="last")].sort_index()
    series = series.dropna()
    if series.empty:
        raise DataError(f"{symbol}: no usable closes after cleaning")

    _write_cache(path, series)
    return series


# --------------------------------------------------------------------------
# Panel assembly and validation
# --------------------------------------------------------------------------


def _to_interval(daily: pd.Series, cfg: Config) -> pd.Series:
    if cfg.interval == "daily":
        return daily
    # Last observed close in each week. Resampling introduces a row for every
    # calendar week in the span, including holiday weeks with no trading, so
    # drop the empties rather than let them become NaN gaps downstream.
    return daily.resample(cfg.week_anchor).last().dropna()


def build_price_panel(cfg: Config, *, use_cache: bool = True) -> PricePanel:
    """Fetch, resample, align, and validate the universe against the benchmark.

    Alignment is an inner join on the benchmark's dates: the benchmark defines
    the calendar, and a symbol only contributes on dates the benchmark also
    traded. Nothing is forward-filled — a symbol that cannot supply a real close
    on a date simply has no observation there, and if that leaves it too short
    it is dropped and reported.
    """
    dropped: dict[str, str] = {}

    try:
        bench_daily = fetch_daily_closes(cfg, cfg.benchmark, use_cache=use_cache)
    except DataError as exc:
        raise DataError(f"benchmark {cfg.benchmark} could not be retrieved: {exc}") from exc

    benchmark = _to_interval(bench_daily, cfg)
    needed = cfg.warmup_bars()
    if len(benchmark) < needed:
        raise DataError(
            f"benchmark {cfg.benchmark} has {len(benchmark)} {cfg.interval} bars but "
            f"the method needs {needed}. Raise lookback_years in config.toml."
        )

    columns: dict[str, pd.Series] = {}
    for symbol in cfg.symbols:
        try:
            series = _to_interval(fetch_daily_closes(cfg, symbol, use_cache=use_cache), cfg)
        except (DataError, requests.RequestException, KeyError) as exc:
            dropped[symbol] = f"retrieval failed: {exc}"
            continue

        aligned = series.reindex(benchmark.index)
        overlap = int(aligned.notna().sum())
        if overlap < needed:
            dropped[symbol] = (
                f"only {overlap} of {needed} required bars overlap the benchmark calendar "
                f"(history starts {series.index[0].date()})"
            )
            continue

        # Interior holes mean the symbol and benchmark disagree on trading days.
        # A handful is normal (halts, listing quirks); a lot means the symbol is
        # on a different calendar and the RS line would be comparing stale prices.
        trailing = aligned.loc[aligned.first_valid_index():]
        holes = int(trailing.isna().sum())
        if holes:
            hole_share = holes / len(trailing)
            if hole_share > 0.02:
                dropped[symbol] = (
                    f"{holes} missing closes ({hole_share:.1%}) on the benchmark calendar; "
                    "likely a different trading calendar"
                )
                continue
            log.warning(
                "%s: %d missing closes on the benchmark calendar; those dates are "
                "excluded rather than filled",
                symbol,
                holes,
            )

        columns[symbol] = aligned

    if not columns:
        raise DataError(
            "every symbol was dropped during validation:\n  "
            + "\n  ".join(f"{s}: {r}" for s, r in dropped.items())
        )

    prices = pd.DataFrame(columns)

    # Any date where some symbol lacks a real close is dropped for everyone.
    # Cross-sectional normalization compares symbols to each other on a date, so
    # a ragged date would score the survivors against a different peer group.
    complete = prices.dropna(how="any")
    lost = len(prices) - len(complete)
    if lost:
        log.warning(
            "dropped %d date(s) where at least one symbol had no close; "
            "cross-sectional scoring needs a complete peer group per date",
            lost,
        )
    prices = complete

    if len(prices) < needed:
        raise DataError(
            f"only {len(prices)} complete {cfg.interval} bars survived alignment but "
            f"{needed} are required. Widen lookback_years or trim the universe."
        )

    return PricePanel(
        prices=prices,
        benchmark=benchmark.reindex(prices.index),
        benchmark_symbol=cfg.benchmark,
        dropped=dropped,
    )
