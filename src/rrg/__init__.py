"""Relative Rotation Graph generation."""

from .config import Config, ConfigError, load_config
from .data import DataError, PricePanel, build_price_panel
from .rrg import RRGResult, classify, compute

__all__ = [
    "Config",
    "ConfigError",
    "DataError",
    "PricePanel",
    "RRGResult",
    "build_price_panel",
    "classify",
    "compute",
    "load_config",
]

__version__ = "0.1.0"
