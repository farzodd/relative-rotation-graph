"""Tests for how step 2 turns RS into the x-axis.

The motivating question was whether a consistently-outperforming security gets
buried in the middle of the chart. It does not, under either basis — these tests
pin that down so the claim cannot quietly stop being true.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrg.config import ConfigError, load_config
from rrg.data import PricePanel
from rrg.rrg import compute

from test_rrg import make_config

PATHS = {
    "STEADY": lambda t, n: 0.0025 * t,                       # outperforms forever
    "BREAKOUT": lambda t, n: np.clip(t - (n - 13), 0, None) * 0.010,
    "FADED": lambda t, n: np.minimum(t, n - 26) * 0.0025,    # stopped 6 months ago
    "FLAT": lambda t, n: np.zeros(n),                        # tracks the benchmark
}


def scenario_panel(n: int = 400) -> PricePanel:
    index = pd.date_range("2020-01-01", periods=n, freq="W-FRI")
    t = np.arange(n, dtype=float)
    benchmark = pd.Series(100.0 * 1.001 ** t, index=index)
    prices = pd.DataFrame(
        {k: benchmark.to_numpy() * np.exp(f(t, n)) for k, f in PATHS.items()},
        index=index,
    )
    return PricePanel(
        prices=prices, benchmark=benchmark, benchmark_symbol="B", dropped={}
    )


def positions(basis: str, window: int = 26) -> pd.Series:
    cfg = make_config(
        normalization="absolute", scale_percentile=100.0, symbols=tuple(PATHS),
        interval="weekly", ratio_basis=basis, ratio_window=window,
    )
    return compute(scenario_panel(), cfg).rs_ratio.iloc[-1] - 100


@pytest.mark.parametrize("basis", ["ema_spread", "rolling_return"])
def test_consistent_outperformer_is_not_stranded_in_the_middle(basis):
    """The claim that motivated all this. A security beating the benchmark at a
    steady rate for years must sit clearly right of it, not at the centre."""
    pos = positions(basis)
    assert pos["STEADY"] > 0, f"{basis}: steady outperformer landed at/below centre"
    assert pos["STEADY"] > pos["FLAT"]
    assert pos["STEADY"] > pos["FADED"]


@pytest.mark.parametrize("basis", ["ema_spread", "rolling_return"])
def test_benchmark_tracker_sits_exactly_at_centre(basis):
    assert positions(basis)["FLAT"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("basis", ["ema_spread", "rolling_return"])
def test_faded_leader_falls_back_toward_centre(basis):
    """Cumulative gains do not hold a place on the chart. FADED is far ahead in
    total but stopped gaining, so it belongs near the centre."""
    pos = positions(basis)
    assert abs(pos["FADED"]) < abs(pos["STEADY"])


@pytest.mark.parametrize("basis", ["ema_spread", "rolling_return"])
def test_faster_recent_gain_outranks_slower_sustained_gain(basis):
    """Correct under any windowed measure: over the last quarter BREAKOUT gained
    12.75% against STEADY's 3.30%."""
    pos = positions(basis)
    assert pos["BREAKOUT"] > pos["STEADY"]


def test_rolling_return_rewards_consistency_more_than_ema_spread():
    """Same ordering, different spacing: the sustained performer sits closer to
    the breakout under an explicit window."""
    spread = positions("ema_spread")
    rolling = positions("rolling_return")
    assert (rolling["STEADY"] / rolling["BREAKOUT"]) > (spread["STEADY"] / spread["BREAKOUT"])


def test_rolling_return_reads_as_the_realised_window_return():
    """The point of the basis: the x-axis is the relative return over the window,
    not a quantity whose lookback has to be inferred."""
    window = 26
    cfg = make_config(
        normalization="cross_sectional", symbols=tuple(PATHS), interval="weekly",
        ratio_basis="rolling_return", ratio_window=window,
    )
    panel = scenario_panel()
    result = compute(panel, cfg)

    rs = result.rs
    realised = (rs.iloc[-1] / rs.iloc[-(window + 1)] - 1.0) * 100
    reported = result.raw_ratio.iloc[-1] - 100
    # Smoothing shifts the magnitude, but the ranking must be exact.
    assert list(realised.sort_values().index) == list(reported.sort_values().index)


def test_window_length_changes_the_reading():
    short = positions("rolling_return", window=8)
    long = positions("rolling_return", window=52)
    # FADED stopped 26 bars ago: an 8-bar window sees nothing, a 52-bar window
    # still catches the tail of its run.
    assert long["FADED"] > short["FADED"]


def test_rolling_return_warmup_uses_its_window_not_the_long_ema():
    spread = make_config(ratio_basis="ema_spread", ema_long=30, ema_momentum=10)
    rolling = make_config(
        ratio_basis="rolling_return", ratio_window=26, ema_short=10, ema_momentum=10
    )
    assert spread.warmup_bars() == 3 * 30 + 3 * 10 + spread.tail_length
    assert rolling.warmup_bars() == 3 * 10 + 26 + 3 * 10 + rolling.tail_length


def test_stamp_names_the_basis_in_use():
    assert "rolling 26-bar return" in make_config(
        ratio_basis="rolling_return", ratio_window=26
    ).stamp()
    assert "EMA 10/30/10" in make_config(ratio_basis="ema_spread").stamp()


def test_rejects_unknown_basis_and_tiny_window(tmp_path):
    import textwrap

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
    normalization = "cross_sectional"
    {extra}
    [chart]
    tail_length = 12
    """

    def write(extra):
        path = tmp_path / "c.toml"
        path.write_text(textwrap.dedent(body).format(extra=extra))
        return path

    with pytest.raises(ConfigError, match="ratio_basis"):
        load_config(write('ratio_basis = "momentum"'))
    with pytest.raises(ConfigError, match="ratio_window"):
        load_config(write('ratio_basis = "rolling_return"\n    ratio_window = 1'))
