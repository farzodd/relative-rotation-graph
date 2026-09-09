"""Tests for named profiles and the diagnostics that compare them."""

from __future__ import annotations

import textwrap

import pandas as pd
import pytest

from rrg.config import ConfigError, load_config, profile_names
from rrg.diagnostics import _runs, compute_diagnostics, quadrant_frame
from rrg.rrg import compute

from test_rrg import make_config, make_panel

BASE = """
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

[chart]
tail_length = 12

[profiles.fast]
description = "quick"
ema_short = 5
ema_long = 15
ema_momentum = 5
tail_length = 8

[profiles.slow]
ema_short = 13
ema_long = 40
"""


def write(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_profile_names_preserve_declaration_order(tmp_path):
    assert profile_names(write(tmp_path, BASE)) == ["fast", "slow"]


def test_profile_overrides_method_and_chart(tmp_path):
    cfg = load_config(write(tmp_path, BASE), profile="fast")
    assert (cfg.ema_short, cfg.ema_long, cfg.ema_momentum) == (5, 15, 5)
    assert cfg.tail_length == 8
    assert cfg.profile == "fast"
    assert cfg.profile_description == "quick"


def test_partial_profile_inherits_the_rest(tmp_path):
    cfg = load_config(write(tmp_path, BASE), profile="slow")
    assert (cfg.ema_short, cfg.ema_long) == (13, 40)
    assert cfg.ema_momentum == 10   # inherited from [method]
    assert cfg.tail_length == 12    # inherited from [chart]


def test_no_profile_leaves_base_settings_untouched(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    assert (cfg.ema_short, cfg.ema_long) == (10, 30)
    assert cfg.profile == ""


def test_profiles_share_universe_and_benchmark(tmp_path):
    """Profiles must stay comparable — only method/chart may differ."""
    path = write(tmp_path, BASE)
    fast, slow = load_config(path, profile="fast"), load_config(path, profile="slow")
    assert fast.symbols == slow.symbols
    assert fast.benchmark == slow.benchmark
    assert fast.interval == slow.interval
    assert fast.normalization == slow.normalization


def test_benchmark_override_beats_the_file(tmp_path):
    cfg = load_config(write(tmp_path, BASE), benchmark="RSP")
    assert cfg.benchmark == "RSP"
    assert load_config(write(tmp_path, BASE)).benchmark == "SPY"


def test_benchmark_override_still_cannot_collide_with_the_universe(tmp_path):
    """The override must not bypass validation — a benchmark inside the universe
    has constant RS against itself and distorts the whole cross-section."""
    with pytest.raises(ConfigError, match="also appears in the universe"):
        load_config(write(tmp_path, BASE), benchmark="XLK")


def test_benchmark_override_composes_with_a_profile(tmp_path):
    cfg = load_config(write(tmp_path, BASE), profile="fast", benchmark="RSP")
    assert cfg.benchmark == "RSP"
    assert cfg.ema_short == 5


def test_unknown_profile_names_the_available_ones(tmp_path):
    with pytest.raises(ConfigError, match="fast, slow"):
        load_config(write(tmp_path, BASE), profile="turbo")


def test_profile_cannot_override_the_universe(tmp_path):
    body = BASE + '\n[profiles.sneaky]\nbenchmark = "QQQ"\n'
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write(tmp_path, body), profile="sneaky")


def test_profile_appears_in_the_provenance_stamp(tmp_path):
    cfg = load_config(write(tmp_path, BASE), profile="fast")
    assert "profile fast" in cfg.stamp()
    assert "EMA 5/15/5" in cfg.stamp()


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


def test_runs_groups_consecutive_labels():
    assert _runs(["A", "A", "B", "A"]) == [("A", 0, 2), ("B", 2, 1), ("A", 3, 1)]
    assert _runs([]) == []
    assert _runs(["A"]) == [("A", 0, 1)]


def test_quadrant_frame_matches_classify_on_every_cell():
    result = compute(make_panel(), make_config())
    frame = quadrant_frame(result)
    assert frame.shape == result.rs_ratio.shape
    assert set(frame.to_numpy().ravel()) <= {"Leading", "Weakening", "Lagging", "Improving"}


def test_shorter_emas_produce_more_signals():
    """The core claim the profiles rest on: faster settings cross more often."""
    panel = make_panel(n=600)
    fast = compute_diagnostics(compute(panel, make_config(ema_short=4, ema_long=12, ema_momentum=4)))
    slow = compute_diagnostics(compute(panel, make_config(ema_short=13, ema_long=40, ema_momentum=13)))
    assert fast.signals_per_year > slow.signals_per_year


def test_reversal_counts_only_round_trips_inside_the_window(monkeypatch):
    """A -> B -> A within the window is a whipsaw; A -> B -> C is not."""
    result = compute(make_panel(), make_config())

    whipsaw = pd.DataFrame({"X": ["A"] * 5 + ["B"] * 2 + ["A"] * 5}, index=result.rs_ratio.index[:12])
    progression = pd.DataFrame({"X": ["A"] * 5 + ["B"] * 2 + ["C"] * 5}, index=result.rs_ratio.index[:12])

    def rate(frame):
        runs = _runs(list(frame["X"]))
        reversals = sum(
            1 for i in range(1, len(runs) - 1)
            if runs[i][2] <= 4 and runs[i + 1][0] == runs[i - 1][0]
        )
        return reversals

    assert rate(whipsaw) == 1
    assert rate(progression) == 0


def test_diagnostics_report_the_settings_they_describe():
    cfg = make_config(ema_short=5, ema_long=15, ema_momentum=5, tail_length=8)
    diag = compute_diagnostics(compute(make_panel(n=600), cfg))
    assert diag.ema == "5/15/5"
    assert diag.tail == 8
    assert 0.0 <= diag.reversal_rate <= 1.0
    assert diag.median_dwell_weeks > 0
    assert set(diag.forward_return.index) == {"Leading", "Weakening", "Lagging", "Improving"}
