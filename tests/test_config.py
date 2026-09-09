"""Tests for config loading and validation."""

from __future__ import annotations

import textwrap

import pytest

from rrg.config import ConfigError, load_config

VALID = """
[universe]
benchmark = "SPY"
symbols = ["XLK", "XLF", "XLE"]

[data]
provider = "tiingo"
interval = "weekly"
lookback_years = 5

[method]
ema_short = 10
ema_long = 30
ema_momentum = 10
normalization = "cross_sectional"

[chart]
tail_length = 12
"""


def write(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_loads_valid_config(tmp_path):
    cfg = load_config(write(tmp_path, VALID))
    assert cfg.benchmark == "SPY"
    assert cfg.symbols == ("XLK", "XLF", "XLE")
    assert cfg.is_cross_sectional


def test_duplicate_symbols_are_collapsed(tmp_path):
    cfg = load_config(write(tmp_path, VALID.replace(
        '["XLK", "XLF", "XLE"]', '["XLK", "XLF", "XLK", "XLE"]'
    )))
    assert cfg.symbols == ("XLK", "XLF", "XLE")


def test_benchmark_inside_universe_is_rejected(tmp_path):
    body = VALID.replace('["XLK", "XLF", "XLE"]', '["XLK", "SPY", "XLE"]')
    with pytest.raises(ConfigError, match="also appears in the universe"):
        load_config(write(tmp_path, body))


def test_short_ema_must_be_faster_than_long(tmp_path):
    body = VALID.replace("ema_short = 10", "ema_short = 40")
    with pytest.raises(ConfigError, match="must be <"):
        load_config(write(tmp_path, body))


def test_cross_sectional_needs_a_peer_group(tmp_path):
    body = VALID.replace('["XLK", "XLF", "XLE"]', '["XLK", "XLF"]')
    with pytest.raises(ConfigError, match="needs >= 3 symbols"):
        load_config(write(tmp_path, body))


def test_lookback_too_short_for_warmup_is_rejected(tmp_path):
    body = VALID.replace("lookback_years = 5", "lookback_years = 1")
    with pytest.raises(ConfigError, match="lookback_years"):
        load_config(write(tmp_path, body))


def test_unknown_provider_is_rejected(tmp_path):
    body = VALID.replace('provider = "tiingo"', 'provider = "bloomberg"')
    with pytest.raises(ConfigError, match="provider"):
        load_config(write(tmp_path, body))


def test_unknown_normalization_is_rejected(tmp_path):
    body = VALID.replace('normalization = "cross_sectional"', 'normalization = "vibes"')
    with pytest.raises(ConfigError, match="normalization"):
        load_config(write(tmp_path, body))


