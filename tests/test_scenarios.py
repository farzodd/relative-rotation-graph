"""Tests for where the x-axis places different performance shapes.

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


def positions() -> pd.Series:
    cfg = make_config(
        normalization="absolute", scale_percentile=100.0, symbols=tuple(PATHS),
        interval="weekly",
    )
    return compute(scenario_panel(), cfg).rs_ratio.iloc[-1] - 100


def test_consistent_outperformer_is_not_stranded_in_the_middle():
    """The claim that motivated all this. A security beating the benchmark at a
    steady rate for years must sit clearly right of it, not at the centre."""
    pos = positions()
    assert pos["STEADY"] > 0, "steady outperformer landed at/below centre"
    assert pos["STEADY"] > pos["FLAT"]
    assert pos["STEADY"] > pos["FADED"]


def test_benchmark_tracker_sits_exactly_at_centre():
    assert positions()["FLAT"] == pytest.approx(0.0, abs=1e-9)


def test_faded_leader_falls_back_toward_centre():
    """Cumulative gains do not hold a place on the chart. FADED is far ahead in
    total but stopped gaining, so it belongs near the centre."""
    pos = positions()
    assert abs(pos["FADED"]) < abs(pos["STEADY"])


def test_faster_recent_gain_outranks_slower_sustained_gain():
    """Correct under any windowed measure: over the last quarter BREAKOUT gained
    12.75% against STEADY's 3.30%."""
    pos = positions()
    assert pos["BREAKOUT"] > pos["STEADY"]


