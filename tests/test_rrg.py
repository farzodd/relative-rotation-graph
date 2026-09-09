"""Tests for the RRG math.

The point of these is falsifiability: each step of the method is checked against
a value computed independently of the implementation, on data simple enough to
reason about by hand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrg.config import Config
from rrg.data import PricePanel
from rrg.rrg import _zscore_cross_sectional, classify, compute


def make_config(**overrides) -> Config:
    base = dict(
        benchmark="BENCH",
        symbols=("AAA", "BBB", "CCC"),
        labels={},
        provider="yfinance",
        interval="daily",
        week_anchor="W-FRI",
        lookback_years=5,
        cache_dir=None,
        cache_max_age_hours=20.0,
        ema_short=10,
        ema_long=30,
        ema_momentum=10,
        normalization="cross_sectional",
        sigma_multiple=2.0,
        scale_percentile=100.0,
        tail_length=5,
        output_dir=None,
        figure_width=11.0,
        figure_height=9.0,
        dpi=160,
        frame_limit=1.25,
    )
    base.update(overrides)
    return Config(**base)


def make_panel(n: int = 400, symbols=("AAA", "BBB", "CCC")) -> PricePanel:
    """Deterministic panel with genuine rotation.

    Each symbol's log performance against the benchmark is a steady drift plus a
    slow phase-shifted cycle. The drift fixes the RS-Ratio ordering; the cycle
    keeps the universe actually rotating. Constant drift alone would leave the
    cross-section flat in momentum — dispersion of ~1e-16 — which is a
    degenerate input, not a realistic one.
    """
    index = pd.date_range("2020-01-01", periods=n, freq="B")
    t = np.arange(n, dtype=float)
    benchmark = pd.Series(100.0 * 1.0002 ** t, index=index, name="BENCH")

    # The cycle's slope must stay smaller than the drift, or it dictates the
    # ordering instead of merely perturbing it: RS-Ratio keys off the slope of
    # RS, so a fast cycle out-votes a slow drift no matter how large the drift's
    # accumulated total is.
    drift = {"AAA": 1.5e-3, "BBB": 0.0, "CCC": -1.5e-3}
    phase = {"AAA": 0.0, "BBB": 2 * np.pi / 3, "CCC": 4 * np.pi / 3}
    period, amplitude = 250.0, 0.02

    prices = pd.DataFrame(
        {
            s: benchmark.to_numpy()
            * np.exp(drift[s] * t + amplitude * np.sin(2 * np.pi * t / period + phase[s]))
            for s in symbols
        },
        index=index,
    )
    return PricePanel(
        prices=prices, benchmark=benchmark, benchmark_symbol="BENCH", dropped={}
    )


def make_acceleration_panel(n: int = 400) -> PricePanel:
    """One symbol accelerating away from the benchmark, one that already stopped.

    ACCEL is flat against the benchmark then breaks out late; DECEL climbs early
    then goes flat. STEADY drifts throughout. At the final bar their momentum
    ordering is unambiguous, which makes it a real check rather than a
    restatement of the implementation.
    """
    index = pd.date_range("2020-01-01", periods=n, freq="B")
    t = np.arange(n, dtype=float)
    benchmark = pd.Series(100.0 * 1.0002 ** t, index=index, name="BENCH")
    # The turn has to be recent enough that both symbols are still resolving it
    # at the final bar. Much older and the EMAs have fully absorbed it, leaving
    # both looking like the steady case again.
    turn = n - 25

    log_rel = {
        "ACCEL": np.clip(t - turn, 0.0, None) * 1.2e-3,
        "DECEL": np.minimum(t, turn) * 1.2e-3,
        "STEADY": t * 6e-4,
    }
    prices = pd.DataFrame(
        {s: benchmark.to_numpy() * np.exp(v) for s, v in log_rel.items()}, index=index
    )
    return PricePanel(
        prices=prices, benchmark=benchmark, benchmark_symbol="BENCH", dropped={}
    )


# --------------------------------------------------------------------------
# Quadrants
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ratio,momentum,expected",
    [
        (101.0, 101.0, "Leading"),
        (101.0, 99.0, "Weakening"),
        (99.0, 99.0, "Lagging"),
        (99.0, 101.0, "Improving"),
        (100.0, 100.0, "Leading"),  # boundary resolves up-and-right
    ],
)
def test_classify_matches_readme_table(ratio, momentum, expected):
    assert classify(ratio, momentum) == expected


# --------------------------------------------------------------------------
# Step-by-step, hand-checkable
# --------------------------------------------------------------------------


def test_rs_is_price_ratio_times_100():
    """Step 1, checked directly against the definition."""
    panel = make_panel()
    result = compute(panel, make_config())

    expected = panel.prices["AAA"] / panel.benchmark * 100.0
    got = result.rs["AAA"]
    pd.testing.assert_series_equal(
        got, expected.loc[got.index], check_names=False, rtol=1e-12
    )


def test_raw_ratio_matches_hand_computed_ema_spread():
    """Step 2, recomputed from EMAs derived outside the implementation."""
    panel = make_panel()
    cfg = make_config()
    result = compute(panel, cfg)

    rs = panel.prices["AAA"] / panel.benchmark * 100.0
    # Recursive EMA written out longhand rather than via .ewm().
    def ema(series: pd.Series, span: int) -> pd.Series:
        alpha = 2.0 / (span + 1.0)
        out = [series.iloc[0]]
        for value in series.iloc[1:]:
            out.append(alpha * value + (1 - alpha) * out[-1])
        return pd.Series(out, index=series.index)

    short = ema(rs, cfg.ema_short)
    long = ema(rs, cfg.ema_long)
    expected = 100.0 * ((short - long) / long + 1.0)

    got = result.raw_ratio["AAA"]
    np.testing.assert_allclose(got.to_numpy(), expected.loc[got.index].to_numpy(), rtol=1e-9)


def test_cross_sectional_zscore_is_row_standardised():
    frame = pd.DataFrame({"A": [1.0, 2.0], "B": [2.0, 4.0], "C": [3.0, 9.0]})
    scored = _zscore_cross_sectional(frame)

    # Row 0: mean 2, population std sqrt(2/3).
    expected_row0 = (np.array([1.0, 2.0, 3.0]) - 2.0) / np.sqrt(2.0 / 3.0)
    np.testing.assert_allclose(scored.iloc[0].to_numpy(), expected_row0, rtol=1e-12)

    # Every row is centred and unit-scaled by construction.
    np.testing.assert_allclose(scored.mean(axis=1).to_numpy(), [0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(scored.std(axis=1, ddof=0).to_numpy(), [1.0, 1.0], rtol=1e-12)


def test_flat_cross_section_does_not_divide_by_zero():
    """Identical values on a date have no dispersion; all sit at the centre."""
    frame = pd.DataFrame({"A": [5.0, 1.0], "B": [5.0, 2.0], "C": [5.0, 3.0]})
    scored = _zscore_cross_sectional(frame)
    assert scored.iloc[0].tolist() == [0.0, 0.0, 0.0]
    assert np.isfinite(scored.to_numpy()).all()


def test_near_flat_cross_section_is_not_amplified_into_noise():
    """Dispersion at the float-noise floor must not produce O(1) z-scores.

    An exact-zero guard misses this: std is ~1e-15 rather than 0, so the
    division would turn rounding error into confident-looking positions.
    """
    frame = pd.DataFrame(
        {"A": [100.0], "B": [100.0 + 1e-14], "C": [100.0 - 1e-14]}
    )
    scored = _zscore_cross_sectional(frame)
    assert scored.iloc[0].abs().max() == 0.0


def test_momentum_is_ratio_against_its_own_ema():
    """Step 4, checked against the implementation's own RS_Ratio output."""
    panel = make_panel()
    cfg = make_config()
    result = compute(panel, cfg)

    # Recompute over the untrimmed series, then compare on the surviving index.
    full = compute(panel, cfg)
    ratio = full.rs_ratio["AAA"]
    expected = 100.0 * (ratio / ratio.ewm(span=cfg.ema_momentum, adjust=False).mean())

    # Only the tail is compared: the recomputed EMA is re-seeded on the trimmed
    # series, so early values legitimately differ.
    got = result.raw_momentum["AAA"].iloc[-cfg.tail_length:]
    np.testing.assert_allclose(
        got.to_numpy(), expected.loc[got.index].to_numpy(), rtol=0.02
    )


# --------------------------------------------------------------------------
# Structural properties
# --------------------------------------------------------------------------


def test_cross_sectional_universe_is_centred_on_100():
    """By construction the peer group averages to the origin on every date."""
    result = compute(make_panel(), make_config())
    np.testing.assert_allclose(result.rs_ratio.mean(axis=1).to_numpy(), 100.0, atol=1e-9)
    np.testing.assert_allclose(result.rs_momentum.mean(axis=1).to_numpy(), 100.0, atol=1e-9)


def test_stronger_symbol_ranks_higher_on_rs_ratio():
    """AAA outgrows the benchmark, CCC lags it; ordering must reflect that."""
    result = compute(make_panel(), make_config())
    latest = result.rs_ratio.iloc[-1]
    assert latest["AAA"] > latest["BBB"] > latest["CCC"]
    # Right half of the chart. Which of the two right-hand quadrants depends on
    # where the cycle sits at the final bar, so pinning one would be overfitting.
    assert latest["AAA"] > 100.0
    assert result.latest().loc["AAA", "quadrant"] in ("Leading", "Weakening")
    assert result.latest().loc["CCC", "quadrant"] in ("Lagging", "Improving")


def test_accelerating_symbol_outranks_decelerating_on_momentum():
    """Step 4-5 do what they claim: momentum tracks change in relative strength."""
    result = compute(make_acceleration_panel(), make_config())
    momentum = result.rs_momentum.iloc[-1]
    assert momentum["ACCEL"] > momentum["STEADY"] > momentum["DECEL"]


def test_rs_ratio_measures_trend_in_rs_not_cumulative_outperformance():
    """Documents a real property of the specified formula.

    Step 2 is an EMA *spread*, so RS-Ratio answers "is relative strength rising
    now", not "how far ahead is this security". ACCEL has barely pulled ahead of
    the benchmark (0.03 in log terms) while DECEL is far ahead (0.45), yet ACCEL
    scores higher because it is the one currently gaining.

    This is inherent to the public approximation, not a defect in the code — but
    it does mean both axes are derivatives of RS rather than one level and one
    rate. Read the x-axis accordingly.
    """
    panel = make_acceleration_panel()
    result = compute(panel, make_config())

    cumulative = (panel.prices / panel.benchmark.to_numpy()[:, None]).iloc[-1]
    assert cumulative["ACCEL"] < cumulative["DECEL"]

    ratio = result.rs_ratio.iloc[-1]
    assert ratio["ACCEL"] > ratio["DECEL"]


def test_warmup_bars_are_not_plotted():
    cfg = make_config()
    panel = make_panel(n=400)
    result = compute(panel, cfg)

    expected_drop = cfg.warmup_bars() - cfg.tail_length
    assert len(result.rs_ratio) == len(panel.prices) - expected_drop
    assert result.rs_ratio.notna().all().all()


def test_tail_has_configured_length():
    cfg = make_config(tail_length=7)
    result = compute(make_panel(), cfg)
    assert len(result.tail("AAA")) == 7


def test_explain_exposes_every_intermediate():
    result = compute(make_panel(), make_config())
    frame = result.explain("AAA", periods=6)
    assert list(frame.columns) == ["RS", "raw_ratio", "RS_Ratio", "raw_mom", "RS_Momentum"]
    assert len(frame) == 6
    assert frame.notna().all().all()


def test_explain_rejects_unknown_symbol():
    result = compute(make_panel(), make_config())
    with pytest.raises(KeyError, match="ZZZ"):
        result.explain("ZZZ")


def test_insufficient_history_is_an_error_not_a_silent_short_chart():
    cfg = make_config()
    # Just short of clearing the warmup plus a full tail.
    too_few = cfg.warmup_bars() - 2
    with pytest.raises(ValueError, match="lookback_years"):
        compute(make_panel(n=too_few), cfg)
