"""Tests for absolute normalization.

The point of absolute mode is that position is measured against the benchmark
rather than against peers, so these tests are about the properties
cross-sectional scoring structurally cannot have: a fixed origin, and a universe
that is allowed to be entirely on one side of it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrg.config import ConfigError, load_config
from rrg.data import PricePanel
from rrg.rrg import absolute_scale, compute

from test_rrg import make_config, make_panel


def losers_panel(n: int = 400, k: int = 4) -> PricePanel:
    """Every member underperforms the benchmark, at different rates.

    This is the case cross-sectional scoring cannot represent: it will always
    place roughly half the universe above the centre line.
    """
    index = pd.date_range("2020-01-01", periods=n, freq="B")
    t = np.arange(n, dtype=float)
    benchmark = pd.Series(100.0 * 1.0004 ** t, index=index, name="BENCH")
    drifts = np.linspace(-2.0e-3, -0.4e-3, k)
    prices = pd.DataFrame(
        {f"S{i}": benchmark.to_numpy() * np.exp(d * t) for i, d in enumerate(drifts)},
        index=index,
    )
    return PricePanel(prices=prices, benchmark=benchmark, benchmark_symbol="BENCH", dropped={})


def test_absolute_lets_the_whole_universe_lag():
    """The headline property. Everything below the benchmark must render as such."""
    panel = losers_panel()
    result = compute(panel, make_config(normalization="absolute"))
    final = result.rs_ratio.iloc[-1]
    assert (final < 100).all(), f"expected every member below the benchmark, got {final.to_dict()}"


def test_cross_sectional_cannot_show_a_universal_laggard():
    """Contrast case, documenting exactly what absolute mode fixes."""
    panel = losers_panel()
    result = compute(panel, make_config(normalization="cross_sectional"))
    final = result.rs_ratio.iloc[-1]
    # Forced to mean 100 every date, so someone is always "ahead" even though
    # every member is losing to the benchmark.
    assert (final > 100).any()
    np.testing.assert_allclose(final.mean(), 100.0, atol=1e-9)


def test_ordering_survives_the_change_of_basis():
    """Absolute rescales; it must not reorder."""
    panel = losers_panel()
    cross = compute(panel, make_config(normalization="cross_sectional")).rs_ratio.iloc[-1]
    absolute = compute(panel, make_config(normalization="absolute")).rs_ratio.iloc[-1]
    assert list(cross.sort_values().index) == list(absolute.sort_values().index)


def test_scale_is_two_sigma_of_the_widest_member():
    frame = pd.DataFrame({"A": [100.0, 101.0, 99.0, 100.0], "B": [100.0, 100.2, 99.8, 100.0]})
    cfg = make_config(normalization="absolute", sigma_multiple=2.0)
    assert absolute_scale(frame, cfg) == pytest.approx(2.0 * frame.std(ddof=0).max())


def test_sigma_multiple_is_the_axis_unit():
    """Doubling the multiple halves the coordinates: 1.0 always means the same
    thing in sigma terms, which is what makes charts comparable over time."""
    panel = losers_panel()
    two = compute(panel, make_config(normalization="absolute", sigma_multiple=2.0))
    four = compute(panel, make_config(normalization="absolute", sigma_multiple=4.0))
    dev_two = (two.rs_ratio.iloc[-1] - 100).abs()
    dev_four = (four.rs_ratio.iloc[-1] - 100).abs()
    np.testing.assert_allclose(dev_four.to_numpy(), dev_two.to_numpy() / 2, rtol=1e-9)


def test_scale_excludes_warmup_bars():
    """EMA seeding inflates early dispersion; including it would inflate the
    divisor for every subsequent chart."""
    panel = losers_panel(n=500)
    cfg = make_config(normalization="absolute")
    result = compute(panel, cfg)
    warmup = cfg.warmup_bars() - cfg.tail_length
    with_warmup = absolute_scale(result.raw_ratio, cfg, 0)
    without = absolute_scale(result.raw_ratio, cfg, warmup)
    assert without <= with_warmup


def test_scales_are_recorded_for_reproducibility():
    result = compute(losers_panel(), make_config(normalization="absolute"))
    assert result.scale_ratio and result.scale_ratio > 0
    assert result.scale_momentum and result.scale_momentum > 0
    # Each axis gets its own; sharing one would flatten the narrower into a line.
    assert result.scale_ratio != result.scale_momentum


def test_other_modes_record_no_scale():
    result = compute(make_panel(), make_config(normalization="cross_sectional"))
    assert result.scale_ratio is None and result.scale_momentum is None


def test_absolute_needs_no_zscore_window_in_its_warmup():
    absolute = make_config(normalization="absolute")
    series = make_config(normalization="time_series", zscore_window=60)
    assert absolute.warmup_bars() < series.warmup_bars()
    assert absolute.warmup_bars() == make_config().warmup_bars()


def test_absolute_works_with_a_single_member(tmp_path):
    """Cross-sectional needs a peer group; absolute needs only the benchmark."""
    panel = losers_panel(k=1)
    result = compute(panel, make_config(normalization="absolute", symbols=("S0",)))
    assert len(result.symbols) == 1
    assert result.rs_ratio.iloc[-1].iloc[0] < 100


def test_palette_covers_the_configured_universe():
    """A wrapped colour makes two series indistinguishable in plot and legend.

    This broke silently when the universe grew from 11 to 13.
    """
    from pathlib import Path

    from rrg.chart import SERIES_COLORS

    cfg = load_config(Path(__file__).parent.parent / "config.toml")
    assert len(SERIES_COLORS) >= len(cfg.symbols), (
        f"{len(cfg.symbols)} symbols but only {len(SERIES_COLORS)} colours; "
        "series would share a colour"
    )
    assert len(set(SERIES_COLORS)) == len(SERIES_COLORS), "palette has duplicates"


def test_config_accepts_absolute_and_reports_it(tmp_path):
    body = """
    [universe]
    benchmark = "SPY"
    symbols = ["XLK", "XLF", "XLE"]
    [data]
    provider = "yfinance"
    interval = "weekly"
    lookback_years = 5
    [method]
    ema_short = 10
    ema_long = 30
    ema_momentum = 10
    normalization = "absolute"
    sigma_multiple = 2.5
    [chart]
    tail_length = 12
    """
    import textwrap
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    cfg = load_config(path)
    assert cfg.is_absolute and not cfg.is_cross_sectional
    assert cfg.sigma_multiple == 2.5
    assert "absolute" in cfg.stamp()
