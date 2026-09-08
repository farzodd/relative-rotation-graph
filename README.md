# relative-rotation-graph

Generate a Relative Rotation Graph (RRG) for a universe of securities against a benchmark, and deliver it as a scheduled email report.

Status: early. Method is specified, nothing is implemented yet.

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

### On normalization

Step 3 and step 5 can z-score two different ways, and the choice materially changes the chart:

- **Time-series** — each security against its own history. Positioning is roughly absolute.
- **Cross-sectional** — every security against the rest of the universe on each date. Positioning is relative to the peer group.

Neither is wrong. Mixing them across runs makes tails incomparable. This project picks one and records it in the config.

## Configuration

Single source of truth. Not yet fixed:

| Parameter | Value |
|---|---|
| Benchmark | TBD |
| Universe | TBD |
| Bar interval | TBD |
| Tail length (N periods) | TBD |
| EMA_short / EMA_long / EMA_mom | TBD (10 / 30 / 10 default) |
| Z-score window | TBD |
| Normalization basis | TBD |
| Data source | TBD |
| Report format | TBD |
| Cadence | TBD |

## Data handling

- Adjusted closes unless stated otherwise.
- Lookback must cover the longest EMA plus the z-score window before the first plotted tail point. Points still warming up are not plotted.
- Gaps, holidays, mismatched calendars between benchmark and constituents, and symbols with insufficient history are handled explicitly. A symbol that can't be computed is dropped and named in the report — never interpolated or forward-filled silently.

## Roadmap

- [ ] Data retrieval and validation for a single symbol
- [ ] RS-Ratio and RS-Momentum for a single symbol, hand-checkable
- [ ] Extend to full universe
- [ ] Chart rendering with tails
- [ ] Report layout and summary table
- [ ] Scheduling
- [ ] Email delivery

## Caveats

The JdK RS-Ratio and JdK RS-Momentum formulas are proprietary to RRG Research and licensed to StockCharts. What's implemented here is a public approximation built from the same published concept. Output will not match StockCharts and should not be described as if it does.

This is a visualization and analysis tool. It describes where securities sit relative to a benchmark. It does not produce trade recommendations and is not investment advice.
