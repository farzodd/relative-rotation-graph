"""Measure how a parameterization behaves, so profiles can be compared on numbers.

These describe the *chart*: whether a setting produces a stable, legible signal.
That is a property of the indicator and is knowable from the data in hand. None
of it says anything about whether the chart predicts returns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .rrg import RRGResult, classify


@dataclass
class ProfileDiagnostics:
    profile: str
    description: str
    ema: str
    tail: int
    bars: int
    signals_per_year: float
    median_dwell_weeks: float
    reversal_rate: float
    momentum_spread: float

    def as_row(self) -> dict:
        return {
            "profile": self.profile,
            "EMA": self.ema,
            "tail": self.tail,
            "signals/yr": round(self.signals_per_year, 1),
            "median dwell (wk)": round(self.median_dwell_weeks, 1),
            "reversal rate": f"{self.reversal_rate:.0%}",
            "mom spread": round(self.momentum_spread, 2),
        }


def quadrant_frame(result: RRGResult) -> pd.DataFrame:
    """Quadrant label per symbol per date."""
    ratio, momentum = result.rs_ratio, result.rs_momentum
    return pd.DataFrame(
        {
            symbol: [
                classify(r, m) for r, m in zip(ratio[symbol], momentum[symbol])
            ]
            for symbol in result.symbols
        },
        index=ratio.index,
    )


def _runs(labels: list[str]) -> list[tuple[str, int, int]]:
    """Consecutive runs as (label, start_index, length)."""
    runs: list[tuple[str, int, int]] = []
    if not labels:
        return runs
    start, current = 0, labels[0]
    for i, label in enumerate(labels[1:], start=1):
        if label != current:
            runs.append((current, start, i - start))
            start, current = i, label
    runs.append((current, start, len(labels) - start))
    return runs


def compute_diagnostics(result: RRGResult, reversal_window: int = 4) -> ProfileDiagnostics:
    """Stability and responsiveness statistics for one parameterization.

    `reversal_window` — bars within which returning to the previous quadrant
    counts as a whipsaw. Four weekly bars is about a month, which matches a
    weekly review cadence.
    """
    cfg = result.config
    quadrants = quadrant_frame(result)
    bars = len(quadrants)
    bars_per_year = 52 if cfg.interval == "weekly" else 252

    dwells: list[int] = []
    transitions = 0
    reversals = 0

    for symbol in result.symbols:
        runs = _runs(list(quadrants[symbol]))
        # Interior runs only: the first and last are truncated by the window
        # edge, so their lengths understate the true dwell.
        dwells.extend(length for _, _, length in runs[1:-1])
        transitions += max(len(runs) - 1, 0)

        # A reversal is entering a new quadrant and returning to the previous
        # one before `reversal_window` bars have passed.
        for i in range(1, len(runs) - 1):
            previous_label = runs[i - 1][0]
            _, _, length = runs[i]
            if length <= reversal_window and runs[i + 1][0] == previous_label:
                reversals += 1

    n_symbols = max(len(result.symbols), 1)
    years = bars / bars_per_year
    signals_per_year = transitions / n_symbols / years if years else 0.0
    median_dwell = float(np.median(dwells)) if dwells else float("nan")
    reversal_rate = reversals / transitions if transitions else 0.0

    momentum_spread = float(
        result.rs_momentum.iloc[-1].max() - result.rs_momentum.iloc[-1].min()
    )

    return ProfileDiagnostics(
        profile=cfg.profile or "(default)",
        description=cfg.profile_description,
        ema=f"{cfg.ema_short}/{cfg.ema_long}/{cfg.ema_momentum}",
        tail=cfg.tail_length,
        bars=bars,
        signals_per_year=signals_per_year,
        median_dwell_weeks=median_dwell,
        reversal_rate=reversal_rate,
        momentum_spread=momentum_spread,
    )

