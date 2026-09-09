"""RS-Ratio and RS-Momentum.

Implements the five-step method from the README, in that order:

    1. RS          = 100 * (price_security / price_benchmark)
    2. raw_ratio   = 100 * ((EMA_short(RS) - EMA_long(RS)) / EMA_long(RS) + 1)
    3. RS_Ratio    = 100 + zscore(raw_ratio)
    4. raw_mom     = 100 * (RS_Ratio / EMA_mom(RS_Ratio))
    5. RS_Momentum = 100 + zscore(raw_mom)

This is a public approximation of the concept. The JdK RS-Ratio and JdK
RS-Momentum formulas are proprietary; output here will not match StockCharts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .data import PricePanel

QUADRANTS = ("Leading", "Weakening", "Lagging", "Improving")


@dataclass
class RRGResult:
    """Computed RRG coordinates plus the intermediates that produced them."""

    rs_ratio: pd.DataFrame
    rs_momentum: pd.DataFrame
    rs: pd.DataFrame
    raw_ratio: pd.DataFrame
    raw_momentum: pd.DataFrame
    config: Config
    benchmark_symbol: str
    dropped: dict[str, str]
    # Absolute mode only: the divisors used, for the chart footer.
    scale_ratio: float | None = None
    scale_momentum: float | None = None

    @property
    def symbols(self) -> list[str]:
        return list(self.rs_ratio.columns)

    @property
    def as_of(self) -> pd.Timestamp:
        return self.rs_ratio.index[-1]

    def tail(self, symbol: str) -> pd.DataFrame:
        """Last N periods of coordinates for one symbol, oldest first."""
        n = self.config.tail_length
        return pd.DataFrame(
            {
                "rs_ratio": self.rs_ratio[symbol].iloc[-n:],
                "rs_momentum": self.rs_momentum[symbol].iloc[-n:],
            }
        )

    def explain(self, symbol: str, periods: int = 15) -> pd.DataFrame:
        """Every intermediate for one symbol, for hand-checking the math."""
        if symbol not in self.rs_ratio.columns:
            raise KeyError(f"{symbol!r} is not in the computed universe: {self.symbols}")
        frame = pd.DataFrame(
            {
                "RS": self.rs[symbol],
                "raw_ratio": self.raw_ratio[symbol],
                "RS_Ratio": self.rs_ratio[symbol],
                "raw_mom": self.raw_momentum[symbol],
                "RS_Momentum": self.rs_momentum[symbol],
            }
        )
        return frame.iloc[-periods:]

    def latest(self) -> pd.DataFrame:
        """Current position per symbol, sorted by quadrant then strength."""
        ratio = self.rs_ratio.iloc[-1]
        momentum = self.rs_momentum.iloc[-1]
        prev_ratio = self.rs_ratio.iloc[-2]
        prev_momentum = self.rs_momentum.iloc[-2]

        frame = pd.DataFrame(
            {
                "rs_ratio": ratio,
                "rs_momentum": momentum,
                "quadrant": [classify(r, m) for r, m in zip(ratio, momentum)],
                # How far the point travelled this period; a proxy for how fast
                # the security is rotating, not for how far it will keep going.
                "speed": np.hypot(ratio - prev_ratio, momentum - prev_momentum),
            }
        )
        frame.index.name = "symbol"
        order = {q: i for i, q in enumerate(QUADRANTS)}
        return frame.sort_values(
            by=["quadrant", "rs_ratio"],
            key=lambda col: col.map(order) if col.name == "quadrant" else col,
            ascending=[True, False],
        )


def classify(rs_ratio: float, rs_momentum: float) -> str:
    """Quadrant for a coordinate pair, per the README table."""
    if rs_ratio >= 100:
        return "Leading" if rs_momentum >= 100 else "Weakening"
    return "Improving" if rs_momentum >= 100 else "Lagging"


def _ema(frame: pd.DataFrame, span: int) -> pd.DataFrame:
    # adjust=False is the trading-platform convention: recursive, seeded from
    # the first observation. It is why warmup_bars() budgets ~3 spans per EMA.
    return frame.ewm(span=span, adjust=False).mean()


# Dispersion below this fraction of the series level counts as none at all.
# Exact-zero checks are not enough: a near-flat cross-section has a std of ~1e-16
# and deviations of ~1e-16, so the division amplifies floating point noise into
# O(1) z-scores. Both inputs here are centred near 100, so a relative threshold
# is well defined.
_ZERO_DISPERSION = 1e-9


def _zscore_cross_sectional(frame: pd.DataFrame) -> pd.DataFrame:
    """Score each row against its own peer group.

    Population std (ddof=0): the universe on a given date is the entire
    population being compared, not a sample drawn from something larger.
    """
    mean = frame.mean(axis=1)
    std = frame.std(axis=1, ddof=0)
    has_spread = std > _ZERO_DISPERSION * mean.abs().clip(lower=1.0)

    scored = frame.sub(mean, axis=0).div(std.where(has_spread), axis=0)
    # A flat cross-section has no dispersion to rank on; every symbol sits at
    # the centre rather than being scaled by ~0.
    scored[~has_spread] = 0.0
    return scored


def _zscore_time_series(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """Score each column against its own trailing history.

    Rows whose trailing window is incomplete stay NaN and are dropped later; a
    complete but flat window scores 0, which is information, not absence.
    """
    rolling = frame.rolling(window=window, min_periods=window)
    mean = rolling.mean()
    std = rolling.std(ddof=0)
    has_spread = std > _ZERO_DISPERSION * mean.abs().clip(lower=1.0)

    scored = (frame - mean) / std.where(has_spread)
    return scored.mask(~has_spread & mean.notna(), 0.0)


def absolute_scale(frame: pd.DataFrame, cfg: Config, warmup: int = 0) -> float:
    """Axis unit for absolute mode: a sigma_multiple-sigma move of the widest member.

    One constant for the whole universe and the whole history — not a per-date
    or per-symbol statistic. That is the entire point: dividing by something
    that moves would re-centre the picture and destroy the benchmark reference,
    which is exactly the failure cross-sectional scoring has.

    Warmup bars are excluded because EMA seeding inflates early dispersion and
    would inflate the divisor for every subsequent chart.
    """
    usable = frame.iloc[warmup:] if warmup else frame
    spread = usable.std(ddof=0).max()
    if not np.isfinite(spread) or spread <= 0:
        return 1.0
    return float(cfg.sigma_multiple * spread)


def _normalize(frame: pd.DataFrame, cfg: Config, scale: float | None = None) -> pd.DataFrame:
    if cfg.is_absolute:
        # Deviation from the benchmark, in axis units. A member matching the
        # benchmark sits at exactly 100; every member can be below it at once.
        return 100.0 + (frame - 100.0) / (scale or 1.0)
    if cfg.is_cross_sectional:
        return 100.0 + _zscore_cross_sectional(frame)
    return 100.0 + _zscore_time_series(frame, cfg.zscore_window)


def compute(panel: PricePanel, cfg: Config) -> RRGResult:
    """Run the five-step method over an aligned price panel."""
    prices = panel.prices
    benchmark = panel.benchmark

    # 1. Relative strength against the benchmark.
    rs = prices.div(benchmark, axis=0) * 100.0

    # 2. Normalised spread between a fast and slow EMA of RS. The `+ 1` recentres
    #    the ratio on 1.0 before scaling, so raw_ratio oscillates around 100.
    ema_short = _ema(rs, cfg.ema_short)
    ema_long = _ema(rs, cfg.ema_long)
    raw_ratio = 100.0 * ((ema_short - ema_long) / ema_long + 1.0)

    # Points still inside the warmup are not trustworthy and are not plotted.
    warmup = cfg.warmup_bars() - cfg.tail_length

    # 3. Normalise into RS-Ratio.
    scale_ratio = absolute_scale(raw_ratio, cfg, warmup) if cfg.is_absolute else None
    rs_ratio = _normalize(raw_ratio, cfg, scale_ratio)

    # 4. Rate of change of RS-Ratio against its own EMA.
    raw_momentum = 100.0 * (rs_ratio / _ema(rs_ratio, cfg.ema_momentum))

    # 5. Normalise into RS-Momentum. The momentum axis gets its own constant:
    #    the two quantities have unrelated natural spreads, and sharing a
    #    divisor would flatten one axis into a line.
    scale_momentum = absolute_scale(raw_momentum, cfg, warmup) if cfg.is_absolute else None
    rs_momentum = _normalize(raw_momentum, cfg, scale_momentum)
    frames = [rs, raw_ratio, rs_ratio, raw_momentum, rs_momentum]
    trimmed = [f.iloc[warmup:] for f in frames]

    # Belt and braces: time-series normalization leaves NaNs for its own window,
    # and those rows must not reach the chart.
    valid = trimmed[2].notna().all(axis=1) & trimmed[4].notna().all(axis=1)
    trimmed = [f.loc[valid] for f in trimmed]

    if len(trimmed[2]) < cfg.tail_length:
        raise ValueError(
            f"only {len(trimmed[2])} usable bars after warmup, need at least "
            f"{cfg.tail_length} for a tail. Raise lookback_years in config.toml."
        )

    rs, raw_ratio, rs_ratio, raw_momentum, rs_momentum = trimmed

    return RRGResult(
        rs_ratio=rs_ratio,
        rs_momentum=rs_momentum,
        rs=rs,
        raw_ratio=raw_ratio,
        raw_momentum=raw_momentum,
        config=cfg,
        benchmark_symbol=panel.benchmark_symbol,
        dropped=panel.dropped,
        scale_ratio=scale_ratio,
        scale_momentum=scale_momentum,
    )
