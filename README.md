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
3. RS_Ratio     = 100 + zscore(raw_ratio, window)
4. raw_mom      = 100 * (RS_Ratio / EMA_mom(RS_Ratio))
5. RS_Momentum  = 100 + zscore(raw_mom, window)
```

### What RS-Ratio actually measures

Worth being precise about, because the intuitive reading of the x-axis is wrong.

Step 2 is an EMA *spread*. It asks whether RS is above its own slower average —
that is, whether relative strength is currently rising — not how far ahead the
security has got. A security that has quietly beaten the benchmark for two years
at a steady rate scores near the middle of the x-axis, because its EMA spread is
small and constant. A security flat for two years that broke out last month
scores high.

So both axes are derivatives of RS: RS-Ratio tracks its trend, RS-Momentum the
change in that trend. The x-axis is not cumulative outperformance, and reading it
that way will mislead you. `test_rs_ratio_measures_trend_in_rs_not_cumulative_outperformance`
pins this down with a case where the two orderings are opposite.

This is inherent to the public approximation, not a defect. It is one of the
reasons output will not match StockCharts.

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
- **`1.0` on an axis is a `sigma_multiple`-sigma move** of the widest member, so
  the axes read −1 to +1 and the frame is held at ±1 even on a quiet week.
- Each axis gets its own constant; the two quantities have unrelated natural
  spreads and sharing a divisor would flatten one into a line.

The divisor is recomputed from the loaded history on each run, so it drifts
slowly as data accumulates, and it is stamped on the chart footer. A *rolling*
divisor was considered and rejected: re-scaling by something that moves would
reintroduce the exact defect above, in the time dimension rather than the
cross-section.

The two modes answer different questions. Absolute: "should I be in this asset
class at all." Cross-sectional: "which member within it." Run both.

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
| Normalization basis | Cross-sectional; `absolute` available (see below) |
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

```
uv run rrg --profile fast     # one named profile
uv run rrg --all-profiles     # every profile, plus a diagnostics comparison
uv run rrg --diagnostics      # stability statistics for a single run
uv run rrg --explain XLK      # every intermediate, for hand-checking
uv run rrg --no-chart         # summary table only
uv run rrg --no-cache         # ignore cached prices and refetch
uv run pytest                 # 60 tests
```

## Profiles

Three named settings in `config.toml` override `[method]` and `[chart]` while
sharing the universe, benchmark, bar interval, and data source — so the outputs
are the same data seen at different speeds, and stay comparable. A profile that
tried to change the universe is rejected at load.

| Profile | EMA | Tail | Intent |
|---|---|---|---|
| `fast` | 5/15/5 | 8 | Crosses quadrant boundaries early, accepts more false crossings |
| `balanced` | 10/30/10 | 12 | The original specification |
| `slow` | 13/40/13 | 16 | Smooth, legible tails; later signals |
| `absolute` | 10/30/10 | 12 | Measured against the benchmark, not peers |

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
