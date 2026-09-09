# relative-rotation-graph

Generate a Relative Rotation Graph (RRG) for a universe of securities against a benchmark, and deliver it as a scheduled email report.

Status: computation and charting work end to end. Reporting, scheduling, and
email delivery are not built yet.

## What an RRG shows

An RRG plots each security on two axes at once, both centered at 100:

- **RS-Ratio** (x-axis) — how strong the security is relative to the benchmark right now.
- **RS-Momentum** (y-axis) — whether that relative strength is improving or deteriorating.

Level and rate of change together. A plain relative-strength line tells you a stock is beating the index; it does not tell you the lead is shrinking. The RRG does.

The four quadrants:

| Quadrant | RS-Ratio | RS-Momentum | Reading |
|---|---|---|---|
| Leading | > 100 | > 100 | Strong and gaining |
| Weakening | > 100 | < 100 | Strong but fading |
| Lagging | < 100 | < 100 | Weak and fading |
| Improving | < 100 | > 100 | Weak but gaining |

Securities normally rotate clockwise: improving → leading → weakening → lagging → improving. Each point is drawn with a tail covering the last N periods, so direction and speed of travel are visible, not just today's position.

## Method

Computed in this order, on adjusted closes:

```
1. RS           = 100 * (price_security / price_benchmark)
2. raw_ratio    = 100 * ((EMA_short(RS) - EMA_long(RS)) / EMA_long(RS) + 1)
3. RS_Ratio     = 100 + scale(raw_ratio)
4. raw_mom      = 100 * (RS_Ratio / EMA_mom(RS_Ratio))
5. RS_Momentum  = 100 + scale(raw_mom)
```

Steps 3 and 5 have three forms, selected by `normalization`, described below and
stamped on every chart.

### What RS-Ratio actually measures

An earlier version of this section claimed a steadily-outperforming security
"scores near the middle of the x-axis". **That was wrong**, and it is worth
stating plainly because it is the kind of error that makes a reader distrust a
chart that is behaving correctly.

Four constructed cases, five years of weekly bars, measured in
`tests/test_scenarios.py`:

| | cumulative vs benchmark | last 13 weeks | x-axis |
|---|---|---|---|
| STEADY — beats benchmark 0.25%/wk, always | +171% | +3.30% | **+2.66** |
| BREAKOUT — flat, then 1%/wk for a quarter | +12.7% | +12.75% | **+4.24** |
| FADED — steady for years, flat for 6 months | +155% | 0.00% | **+0.71** |
| FLAT — tracks the benchmark exactly | 0% | 0.00% | **0.00** |

A consistent outperformer lands clearly right of centre. Only a security that
has *stopped* outperforming drifts back toward it, and FADED's 13-week relative
return is exactly 0.00% — so the centre is the honest place for it.

BREAKOUT outranking STEADY is also correct rather than a flaw: over the last
quarter it gained 12.75% against 3.30%. That ordering holds under any windowed
measure. Only cumulative-since-inception would reverse it, and that quantity
answers a different question than a rotation chart is asking.

What *is* true is narrower: the x-axis measures a **rate**, and its effective
lookback is emergent rather than chosen. At EMA 10/30 it behaves like a 26–52
week relative return (rank correlation +0.94 and +0.92); at 5/15 it is closer to
4 weeks and noisier. Worth knowing when reading it, but it does not bury a
consistent performer.

### On normalization

Step 3 and step 5 can be scaled three ways, and the choice materially changes
what the chart is capable of showing:

- **Cross-sectional** — every security against the rest of the universe on each
  date. Positioning is relative to the peer group.
- **Time-series** — each security against its own history.
- **Absolute** — divide by a constant. Positioning is relative to the benchmark.

Mixing them across runs makes tails incomparable. The config records which is in
use and the chart footer stamps it.

### What cross-sectional scoring cannot show

Dividing by a per-date statistic re-centres the universe on 100 every bar. It is
a ranking, not a measurement — someone is always top of the class, including
when the whole class is failing. Measured over 143 weeks of the sector universe:

| | range | std |
|---|---|---|
| Sectors actually beating SPY (trailing quarter) | 0 → 10 of 11 | 2.58 |
| Sectors the chart placed in the right half | 3 → 7 of 11 | 0.80 |

Correlation between the two: **−0.10**. On the 25 weeks when at most one sector
was beating SPY, the chart still showed a median of five in the right half.

So the question "is anything beating the benchmark, or should I just hold the
benchmark?" is one cross-sectional mode discards by construction.

### Absolute mode

`normalization = "absolute"` divides by a single constant instead:

```
scale     = sigma_multiple * max over members of std(raw_ratio)
RS_Ratio  = 100 + (raw_ratio - 100) / scale
```

One constant for the whole universe and the whole history. Consequences:

- **The benchmark is the origin.** Its RS against itself is flat, so `raw_ratio`
  is exactly 100 — it lands on the crosshair with no special-casing, and is
  drawn on the chart. The un-normalized quantity correlates **+0.60** with true
  breadth, against −0.10 for the cross-sectional version.
- **Every member may lag at once**, which is the point.
- **`1.0` on an axis is a `sigma_multiple`-sigma move** of the `scale_percentile`
  member, so the axes read −1 to +1.
- Each axis gets its own constant; the two quantities have unrelated natural
  spreads and sharing a divisor would flatten one into a line.

### Keeping one outlier from eating the chart

Two separate mechanisms let a single volatile member compress everyone else, and
both had to be fixed:

- **The divisor.** At `scale_percentile = 100` the widest member sets it. SMH's
  sigma is 2.31x the median member's, so everyone else was squeezed toward the
  centre. `scale_percentile = 75` sizes the chart for a typical member instead.
- **The frame.** Auto-expanding to the widest tail undid any divisor change.
  Measured: the median member occupied 26.0% of the half-frame at
  `sigma_multiple = 2.0`, 26.1% at 1.0, and 26.5% at 0.5 — *changing
  `sigma_multiple` alone is a no-op*, altering only the tick labels. The frame
  is now fixed at `frame_limit`, which also makes week-to-week charts
  comparable. Members outside it are drawn on the boundary as hollow triangles
  captioned with their true coordinate, never dropped.


## Configuration

Single source of truth is [`config.toml`](config.toml). This table mirrors it; change both together.

| Parameter | Value |
|---|---|
| Benchmark | SPY |
| Universe | 11 SPDR sectors + ITA (aerospace/defense) + SMH (semiconductors) |
| Bar interval | Weekly, week ending Friday, resampled locally from daily closes |
| Tail length (N periods) | 12 (`balanced`; see Profiles) |
| EMA_short / EMA_long / EMA_mom | 10 / 30 / 10 (`balanced`; see Profiles) |
| Z-score window | 60 (only used by time-series normalization) |
| Normalization basis | **Absolute** (default); `cross_sectional` via the `fast`/`balanced` profiles |
| Data source | yfinance by default (no key); Tiingo via `provider = "tiingo"` |
| Report format | Not yet implemented |
| Cadence | Not yet implemented |

The benchmark may not appear in the universe. Its RS against itself is a constant
100, which would drag the cross-sectional mean and distort every other symbol.
This is enforced at config load.

### Data source

Tiingo's free tier covers this workload outright — 500 unique symbols/month and
50 requests/hour against a universe of 12 fetched weekly, with 30+ years of
history. It is personal-use-only; the paid tier is $30/month. Set
`TIINGO_API_KEY` and flip `provider` to use it.

The default is `yfinance` purely so the project runs with no signup. It is an
unofficial scraper and can break without warning.

Polygon (now "Massive") is a poor fit despite being the obvious name: its free
tier caps history at 2 years, and the warmup below needs roughly 3.

### Warmup

Nothing is plotted until the method has enough history to mean anything. With
`adjust=False`, an EMA is seeded from its first observation and needs ~3 spans
before the seed stops showing. Two EMAs run in sequence — EMA_long over RS, then
EMA_mom over RS_Ratio — so their warmups add, and the tail needs its own bars on
top:

```
warmup = 3*EMA_long + 3*EMA_mom + tail          (cross-sectional)
       = 3*EMA_long + 3*EMA_mom + zscore_window + tail   (time-series)
```

At the settings above that is 132 weekly bars, ~2.5 years, which is why
`lookback_years = 5`. Cross-sectional needs no z-score window because it scores
across the universe on each date rather than across history.

### Choice of benchmark

The benchmark should be whatever you would hold instead — it defines what
"beating the market" means for the chart.

SPY is cap-weighted, and over 2021-09 to 2026-09 it returned **+80.9%** against
equal-weighted RSP's **+50.1%** — a 30.7 percentage point concentration premium.
That is why most sectors show as lagging: they are measured against an index a
handful of mega-caps are carrying. XLK and SMH are themselves inside that
concentration, so measuring them against SPY understates them; XLK's edge is
+35.3% vs SPY but +63.0% vs RSP.

Switching benchmark does not reorder the field, though. Measured across 143
weeks, the cross-sectional rank correlation of RS-Ratio between the two
benchmarks is **+0.998**, and at the latest bar all 13 members landed in the
same quadrant under both. What it does is shift everyone by a near-common
amount, which flips members sitting close to a boundary — quadrant agreement
across the full history is only **67.8%**.

So the benchmark is second-order for anything clearly ahead or behind, and
decisive for anything near the line. Pick the one you would actually hold, and
use `--benchmark` to check how much of a borderline call depends on it.

## Data handling

- Adjusted closes unless stated otherwise.
- Lookback must cover the longest EMA plus the z-score window before the first plotted tail point. Points still warming up are not plotted.
- Gaps, holidays, mismatched calendars between benchmark and constituents, and symbols with insufficient history are handled explicitly. A symbol that can't be computed is dropped and named in the report — never interpolated or forward-filled silently.

## Usage

```
uv sync --extra dev
uv run rrg
```

Writes a PNG to `output/` and prints the current position of every symbol.
The bare command gives the **absolute** view: each member measured against the
benchmark, which sits on the origin.

```
uv run rrg --profile abs_fast # one named profile
uv run rrg --benchmark RSP    # override the benchmark without editing config
uv run rrg --all-profiles     # every profile, plus a diagnostics comparison
uv run rrg --diagnostics      # stability statistics for a single run
uv run rrg --explain XLK      # every intermediate, for hand-checking
uv run rrg --no-chart         # summary table only
uv run rrg --no-cache         # ignore cached prices and refetch
uv run pytest                 # 73 tests
```

## Profiles

Three named settings in `config.toml` override `[method]` and `[chart]` while
sharing the universe, benchmark, bar interval, and data source — so the outputs
are the same data seen at different speeds, and stay comparable. A profile that
tried to change the universe is rejected at load.

| Profile | Basis | EMA | Tail |
|---|---|---|---|
| `(none)` | absolute | 10/30/10 | 12 |
| `abs_balanced` | absolute | 10/30/10 | 12 |
| `abs_fast` | absolute | 5/15/5 | 8 |
| `cross_balanced` | cross-sectional | 10/30/10 | 12 |
| `cross_fast` | cross-sectional | 5/15/5 | 8 |

Two dimensions, both spelled out in the name so a profile cannot mean something
its name does not say. `abs_*` measures against the benchmark and lets the whole
universe lag at once; `cross_*` measures against the peer group, which
re-centres every bar. `*_fast` crosses quadrant boundaries earlier at roughly
double the reversal rate. `abs_balanced` repeats the default on purpose, so
every view has a name.

### Choosing between them

`--all-profiles` prints diagnostics so the choice rests on numbers:

- **signals/yr** — quadrant crossings per symbol per year. Responsiveness.
- **median dwell** — how long a symbol stays put once it crosses. If dwell is
  shorter than your review cadence, you are reacting to states that have
  already passed.
- **reversal rate** — crossings that return to the previous quadrant within four
  bars. The false-signal rate, and the price of responsiveness.
- **mom spread** — width of the RS-Momentum axis across the universe. A narrow
  spread means genuine turns and noise look alike.

These describe the *indicator*: whether it is stable and legible. They say
nothing about whether it predicts returns. The forward-return table printed
alongside them is one five-year window on one universe with no costs — a
description of what happened, not evidence of an edge.

Daily closes are cached under `.cache/` for 20 hours, so repeated runs during
development do not re-hit the provider.

## Roadmap

- [x] Data retrieval and validation for a single symbol
- [x] RS-Ratio and RS-Momentum for a single symbol, hand-checkable
- [x] Extend to full universe
- [x] Chart rendering with tails
- [ ] Report layout and summary table
- [ ] Scheduling
- [ ] Email delivery

## Caveats

The JdK RS-Ratio and JdK RS-Momentum formulas are proprietary to RRG Research and licensed to StockCharts. What's implemented here is a public approximation built from the same published concept. Output will not match StockCharts and should not be described as if it does.

This is a visualization and analysis tool. It describes where securities sit relative to a benchmark. It does not produce trade recommendations and is not investment advice.
