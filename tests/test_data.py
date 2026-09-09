"""Tests for local resampling of provider data.

`_to_interval` is the seam where daily closes become the bars the method runs
on. Its contract is deliberate — we resample locally rather than asking a
provider for weekly bars, so the week-ending convention is ours and not theirs —
and everything downstream assumes the result has no NaN gaps.
"""

from __future__ import annotations

import pandas as pd

from rrg.config import Config
from rrg.data import _to_interval


def make_config(**overrides) -> Config:
    base = dict(
        benchmark="BENCH",
        symbols=("AAA", "BBB", "CCC"),
        labels={},
        provider="yfinance",
        interval="weekly",
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


def test_daily_interval_is_left_alone():
    """Daily is already the provider's native grain; resampling it would be a no-op
    that only risks shifting dates."""
    daily = pd.Series(
        range(5), index=pd.bdate_range("2024-01-01", periods=5), dtype=float
    )
    assert _to_interval(daily, make_config(interval="daily")) is daily


def test_weekly_takes_the_last_close_of_each_week():
    """A weekly bar is the week's closing price, not its mean or its open."""
    daily = pd.Series(
        range(10), index=pd.bdate_range("2024-01-01", periods=10), dtype=float
    )
    weekly = _to_interval(daily, make_config())

    # Two full Mon-Fri weeks; the last close of each is index 4 and index 9.
    assert weekly.tolist() == [4.0, 9.0]
    assert [str(d.date()) for d in weekly.index] == ["2024-01-05", "2024-01-12"]


def test_week_anchor_is_honoured():
    """The anchor is config, not a constant. A W-WED run must end its bars on
    Wednesdays — this is the whole reason we resample locally."""
    daily = pd.Series(
        range(12), index=pd.bdate_range("2024-01-01", periods=12), dtype=float
    )
    weekly = _to_interval(daily, make_config(week_anchor="W-WED"))

    assert {d.day_name() for d in weekly.index} == {"Wednesday"}


def test_weeks_with_no_trading_are_dropped_not_left_as_nan():
    """Resampling spans the calendar, so a week the market never opened comes back
    as a NaN row. Those must not survive: the RS line would be dividing by a hole,
    and the warmup budget counts bars, so phantom rows would overstate history.
    """
    index = pd.to_datetime(
        [
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
            # Week ending 2024-01-19 has no observations at all.
            "2024-01-22", "2024-01-23",
        ]
    )
    daily = pd.Series(range(len(index)), index=index, dtype=float)
    weekly = _to_interval(daily, make_config())

    assert weekly.notna().all()
    assert pd.Timestamp("2024-01-19") not in weekly.index
    assert [str(d.date()) for d in weekly.index] == [
        "2024-01-05",
        "2024-01-12",
        "2024-01-26",
    ]
