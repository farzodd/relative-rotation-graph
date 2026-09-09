"""Load and validate config.toml.

The config is the single source of truth for a run. Anything that changes the
numbers lives here, gets validated at load time, and is stamped onto the chart
so a saved PNG can always be traced back to the settings that produced it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NORMALIZATIONS = ("cross_sectional", "time_series")
PROVIDERS = ("tiingo", "yfinance")


class ConfigError(ValueError):
    """Raised when config.toml is internally inconsistent or missing a field."""


@dataclass(frozen=True)
class Config:
    benchmark: str
    symbols: tuple[str, ...]
    labels: dict[str, str]

    provider: str
    interval: str
    week_anchor: str
    lookback_years: int
    cache_dir: Path
    cache_max_age_hours: float

    ema_short: int
    ema_long: int
    ema_momentum: int
    normalization: str
    zscore_window: int

    tail_length: int
    output_dir: Path
    figure_width: float
    figure_height: float
    dpi: int

    root: Path = field(default_factory=Path.cwd)
    profile: str = ""
    profile_description: str = ""

    def label(self, symbol: str) -> str:
        return self.labels.get(symbol, symbol)

    @property
    def is_cross_sectional(self) -> bool:
        return self.normalization == "cross_sectional"

    def warmup_bars(self) -> int:
        """Bars consumed before the first trustworthy RRG point.

        An EMA with ``adjust=False`` is seeded from the first observation and
        needs roughly 3x its span before the seed stops mattering. Two EMAs run
        in sequence here (EMA_long on RS, then EMA_momentum on RS_Ratio), so
        their warmups add. The tail then needs its own bars on top.

        Time-series normalization additionally consumes a full z-score window;
        cross-sectional does not, because it scores across the universe on each
        date rather than across history.
        """
        warmup = 3 * self.ema_long + 3 * self.ema_momentum
        if not self.is_cross_sectional:
            warmup += self.zscore_window
        return warmup + self.tail_length

    def stamp(self) -> str:
        """One-line provenance string for the chart footer."""
        prefix = f"profile {self.profile} | " if self.profile else ""
        return (
            f"{prefix}{self.interval} bars | EMA {self.ema_short}/{self.ema_long}/{self.ema_momentum} "
            f"| {self.normalization} | tail {self.tail_length} | source {self.provider}"
        )


def _require(table: dict, key: str, where: str):
    if key not in table:
        raise ConfigError(f"config.toml: missing [{where}] {key}")
    return table[key]


def profile_names(path: str | Path = "config.toml") -> list[str]:
    """Profiles declared in config.toml, in declaration order."""
    path = Path(path).expanduser().resolve()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return list(raw.get("profiles", {}))


def load_config(path: str | Path = "config.toml", profile: str | None = None) -> Config:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    root = path.parent
    universe = _require(raw, "universe", "root")
    data = _require(raw, "data", "root")
    method = dict(_require(raw, "method", "root"))
    chart = dict(_require(raw, "chart", "root"))

    profile_description = ""
    if profile is not None:
        profiles = raw.get("profiles", {})
        if profile not in profiles:
            known = ", ".join(profiles) or "none declared"
            raise ConfigError(f"config.toml: unknown profile {profile!r} (have: {known})")
        overrides = dict(profiles[profile])
        profile_description = str(overrides.pop("description", ""))
        # A profile may override anything in [method] or [chart]; the universe,
        # benchmark, and data source stay shared so profiles remain comparable.
        unknown = set(overrides) - set(method) - set(chart) - {"tail_length"}
        if unknown:
            raise ConfigError(
                f"config.toml: profile {profile!r} sets unknown key(s): {', '.join(sorted(unknown))}. "
                "Profiles may only override [method] and [chart] settings."
            )
        for key, value in overrides.items():
            if key in chart or key == "tail_length":
                chart[key] = value
            else:
                method[key] = value

    benchmark = _require(universe, "benchmark", "universe")
    symbols = tuple(dict.fromkeys(_require(universe, "symbols", "universe")))

    if not symbols:
        raise ConfigError("config.toml: [universe] symbols is empty")
    if benchmark in symbols:
        raise ConfigError(
            f"config.toml: benchmark {benchmark!r} also appears in the universe. "
            "Its RS against itself is constant at 100, which distorts a "
            "cross-sectional z-score for every other symbol."
        )

    cfg = Config(
        benchmark=benchmark,
        symbols=symbols,
        labels=dict(universe.get("labels", {})),
        provider=_require(data, "provider", "data"),
        interval=data.get("interval", "weekly"),
        week_anchor=data.get("week_anchor", "W-FRI"),
        lookback_years=int(data.get("lookback_years", 5)),
        cache_dir=root / data.get("cache_dir", ".cache"),
        cache_max_age_hours=float(data.get("cache_max_age_hours", 20)),
        ema_short=int(_require(method, "ema_short", "method")),
        ema_long=int(_require(method, "ema_long", "method")),
        ema_momentum=int(_require(method, "ema_momentum", "method")),
        normalization=_require(method, "normalization", "method"),
        zscore_window=int(method.get("zscore_window", 60)),
        tail_length=int(chart.get("tail_length", 12)),
        output_dir=root / chart.get("output_dir", "output"),
        figure_width=float(chart.get("figure_width", 11.0)),
        figure_height=float(chart.get("figure_height", 9.0)),
        dpi=int(chart.get("dpi", 160)),
        root=root,
        profile=profile or "",
        profile_description=profile_description,
    )

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.provider not in PROVIDERS:
        raise ConfigError(
            f"config.toml: provider {cfg.provider!r} not one of {PROVIDERS}"
        )
    if cfg.normalization not in NORMALIZATIONS:
        raise ConfigError(
            f"config.toml: normalization {cfg.normalization!r} not one of {NORMALIZATIONS}"
        )
    if cfg.interval not in ("daily", "weekly"):
        raise ConfigError(f"config.toml: interval {cfg.interval!r} not 'daily' or 'weekly'")
    if cfg.ema_short >= cfg.ema_long:
        raise ConfigError(
            f"config.toml: ema_short ({cfg.ema_short}) must be < ema_long ({cfg.ema_long}); "
            "otherwise the RS-Ratio spread inverts sign."
        )
    if min(cfg.ema_short, cfg.ema_long, cfg.ema_momentum) < 2:
        raise ConfigError("config.toml: EMA spans must be >= 2")
    if cfg.tail_length < 2:
        raise ConfigError("config.toml: tail_length must be >= 2 to show direction")

    if cfg.is_cross_sectional and len(cfg.symbols) < 3:
        raise ConfigError(
            f"config.toml: cross-sectional normalization needs >= 3 symbols, got "
            f"{len(cfg.symbols)}. With fewer, the z-score is degenerate."
        )

    # Enough bars to fill the warmup, with the trading-calendar shortfall in mind.
    bars_per_year = 52 if cfg.interval == "weekly" else 252
    available = cfg.lookback_years * bars_per_year
    needed = cfg.warmup_bars()
    if available < needed:
        years = needed / bars_per_year
        raise ConfigError(
            f"config.toml: lookback_years={cfg.lookback_years} gives ~{available} "
            f"{cfg.interval} bars but the method needs {needed} before the first "
            f"plotted point. Raise lookback_years to at least {years:.1f}."
        )
