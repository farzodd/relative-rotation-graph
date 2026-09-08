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

Step 3 and step 5 can z-score two different ways, and the choice materially changes the chart:

- **Time-series** — each security against its own history. Positioning is roughly absolute.
- **Cross-sectional** — every security against the rest of the universe on each date. Positioning is relative to the peer group.

Neither is wrong. Mixing them across runs makes tails incomparable. This project picks one and records it in the config.

## Configuration

Single source of truth is [`config.toml`](config.toml). This table mirrors it; change both together.

| Parameter | Value |
|---|---|
| Benchmark | SPY |
| Universe | 11 SPDR select sector funds (XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY) |
| Bar interval | Weekly, week ending Friday, resampled locally from daily closes |
| Tail length (N periods) | 12 |
| EMA_short / EMA_long / EMA_mom | 10 / 30 / 10 |
| Z-score window | 60 (only used by time-series normalization) |
| Normalization basis | Cross-sectional |
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
uv run rrg --explain XLK      # every intermediate, for hand-checking
uv run rrg --no-chart         # summary table only
uv run rrg --no-cache         # ignore cached prices and refetch
uv run pytest                 # 35 tests
```

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
