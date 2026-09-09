# relative-rotation-graph

Generate a Relative Rotation Graph (RRG) for a universe of securities against a
benchmark, and deliver it as a scheduled email report.

Status: computation and charting work end to end. Reporting, scheduling, and
email delivery are not built yet.

## What an RRG shows

Each security is plotted on two axes at once:

- **RS-Ratio** (x-axis) — its relative strength against the benchmark.
- **RS-Momentum** (y-axis) — whether that relative strength is improving or
  deteriorating.

Level and rate of change together. A plain relative-strength line tells you a
stock is beating the index; it does not tell you the lead is shrinking.

| Quadrant | RS-Ratio | RS-Momentum | Reading |
|---|---|---|---|
| Leading | above centre | above centre | Strong and gaining |
| Weakening | above centre | below centre | Strong but fading |
| Lagging | below centre | below centre | Weak and fading |
| Improving | below centre | above centre | Weak but gaining |

Securities normally rotate clockwise: improving → leading → weakening → lagging
→ improving. Each point carries a tail covering the last N periods, so direction
and speed of travel are visible, not just today's position.

## Usage

```
uv sync --extra dev
uv run rrg
```

Writes a PNG to `output/` and prints the current position of every symbol. The
bare command gives the **absolute** view: each member measured against the
benchmark, which sits on the origin.

```
uv run rrg --profile abs_fast   # a named view (see Profiles)
uv run rrg --all-profiles       # every view, plus a diagnostics comparison
uv run rrg --benchmark RSP      # different benchmark, without editing config
uv run rrg --diagnostics        # stability statistics for one run
uv run rrg --explain XLK        # every intermediate value, for hand-checking
uv run rrg --no-chart           # summary table only
uv run rrg --no-cache           # ignore cached prices and refetch
uv run pytest                   # 69 tests
```

Daily closes are cached under `.cache/` for 20 hours, so repeated runs during
development do not re-hit the provider.

## Method

Computed in this order, on adjusted closes:

```
1. RS           = 100 * (price_security / price_benchmark)
2. raw_ratio    = 100 * ((EMA_short(RS) - EMA_long(RS)) / EMA_long(RS) + 1)
3. RS_Ratio     = 100 + scale(raw_ratio)
4. raw_mom      = 100 * (RS_Ratio / EMA_mom(RS_Ratio))
5. RS_Momentum  = 100 + scale(raw_mom)
```

`scale` has two forms, selected by `normalization`, and the choice is stamped on
every chart.

### How to read the x-axis

Step 2 is an EMA *spread*, so the x-axis measures the **rate** at which relative
strength is changing. Four constructed cases make the behaviour concrete
(`tests/test_scenarios.py`, absolute scaling):

| | cumulative vs benchmark | last 13 weeks | x-axis |
|---|---|---|---|
| STEADY — beats benchmark 0.25%/wk, always | +171% | +3.30% | **+2.66** |
| BREAKOUT — flat, then 1%/wk for a quarter | +12.7% | +12.75% | **+4.24** |
| FADED — steady for years, flat for 6 months | +155% | 0.00% | **+0.71** |
| FLAT — tracks the benchmark exactly | 0% | 0.00% | **0.00** |

A consistent outperformer sits clearly right of centre. A security that has
*stopped* outperforming drifts back toward it — FADED's 13-week relative return
is exactly 0.00%, so the centre is where it belongs. BREAKOUT outranking STEADY
follows from the same logic: over the last quarter it gained 12.75% against
3.30%.

The effective lookback is emergent rather than set: at EMA 10/30 the x-axis
behaves like a 26–52 week relative return (rank correlation +0.94 and +0.92); at
5/15 it is closer to 4 weeks and noisier.

## Normalization

`scale` in steps 3 and 5 has two forms. They are not comparable with each other
— only with their own history.

### absolute (default)

Divide by a single constant:

```
scale     = sigma_multiple * percentile(std(raw_ratio) over members, scale_percentile)
RS_Ratio  = 100 + (raw_ratio - 100) / scale
```

- **The benchmark is the origin.** Its RS against itself is flat, so `raw_ratio`
  is exactly 100. It lands on the crosshair with no special-casing and is drawn
  there.
- **Every member may lag at once**, which is the point.
- **`1.0` on an axis is a `sigma_multiple`-sigma move** of the
  `scale_percentile` member, so the axes read −1 to +1.
- Each axis gets its own constant; the two quantities have unrelated natural
  spreads and sharing a divisor would flatten one into a line.

The divisor is recomputed from the loaded history on each run, so it drifts
slowly as data accumulates, and it is stamped on the chart footer. A *rolling*
divisor would re-centre the picture as it moved and destroy the fixed benchmark
reference, which is the defect described next.

### cross_sectional

Score each member against its peers on each date. Useful for ranking within the
group, but it re-centres the universe on 100 every bar, so it cannot show how the
group is doing against the benchmark. Measured over 143 weeks:

| | range | std |
|---|---|---|
| Sectors actually beating SPY (trailing quarter) | 0 → 10 of 11 | 2.58 |
| Sectors this scaling placed in the right half | 3 → 7 of 11 | 0.80 |

Correlation between them: **−0.10**. On the 25 weeks when at most one sector was
beating SPY, it still showed a median of five in the right half. Absolute scaling
correlates **+0.60** with the same measure.

Use `cross_sectional` to ask which member is strongest relative to the others;
use `absolute` to ask whether any of them is beating the benchmark.

### Keeping one outlier from eating the chart

Two mechanisms let a single volatile member compress everyone else:

- **The divisor.** At `scale_percentile = 100` the widest member sets it. SMH's
  sigma is 2.31x the median member's. `scale_percentile = 75` sizes the chart for
  a typical member instead.
- **The frame.** An auto-expanding frame undoes any divisor change: the median
  member occupied 26.0% of the half-frame at `sigma_multiple = 2.0`, 26.1% at
  1.0, and 26.5% at 0.5. The frame is fixed at `frame_limit`, which also keeps
  week-to-week charts comparable. Members outside it are drawn on the boundary as
  hollow triangles captioned with their true coordinate, never dropped.

## Profiles

| Profile | Basis | EMA | Tail |
|---|---|---|---|
| `(none)` | absolute | 10/30/10 | 12 |
| `abs_balanced` | absolute | 10/30/10 | 12 |
| `abs_fast` | absolute | 5/15/5 | 8 |
| `cross_balanced` | cross_sectional | 10/30/10 | 12 |
| `cross_fast` | cross_sectional | 5/15/5 | 8 |

Both dimensions appear in every name, so a profile cannot mean something its name
does not say. `abs_balanced` repeats the default deliberately, so every view has
a name. Profiles override `[method]` and `[chart]` only; a profile that tries to
change the universe is rejected at load.

`--all-profiles` prints diagnostics so the choice rests on numbers:

- **signals/yr** — quadrant crossings per symbol per year. Responsiveness.
- **median dwell** — how long a symbol stays put once it crosses. If dwell is
  shorter than your review cadence, you are reacting to states that have passed.
- **reversal rate** — crossings that return to the previous quadrant within four
  bars. The price of responsiveness: 31% for `*_fast` against 16–21%.
- **mom spread** — width of the RS-Momentum axis across the universe. A narrow
  spread means genuine turns and noise look alike.

These describe the indicator's stability. They say nothing about whether it
predicts returns.

## Configuration

Single source of truth is [`config.toml`](config.toml). This table mirrors it;
change both together.

| Parameter | Value |
|---|---|
| Benchmark | SPY |
| Universe | 11 SPDR sectors + ITA (aerospace/defense) + SMH (semiconductors) |
| Bar interval | Weekly, week ending Friday, resampled locally from daily closes |
| Normalization | absolute (default); cross_sectional via the `cross_*` profiles |
| EMA_short / EMA_long / EMA_mom | 10 / 30 / 10 |
| Tail length | 12 |
| sigma_multiple / scale_percentile | 2.0 / 75 |
| frame_limit | 1.25 |
| Data source | yfinance by default (no key); Tiingo via `provider = "tiingo"` |
| Report format | Not yet implemented |
| Cadence | Not yet implemented |

The benchmark may not appear in the universe. Its RS against itself is a constant
100, which would drag the cross-sectional mean and distort every other symbol.
This is enforced at config load, including when overridden with `--benchmark`.

ITA and SMH are subsets of sectors already listed — ITA of Industrials, SMH of
Technology. Under `cross_sectional` a sector and its own sub-industry are scored
against each other, which double-counts the theme; under `absolute` each is
measured against the benchmark alone and there is no interaction.

### Choice of benchmark

The benchmark should be whatever you would hold instead — it defines what
"beating the market" means for the chart.

SPY is cap-weighted, and over 2021-09 to 2026-09 it returned **+80.9%** against
equal-weighted RSP's **+50.1%** — a 30.7 percentage point concentration premium.
That is why most sectors show as lagging. XLK and SMH are themselves inside that
concentration, so measuring them against SPY understates them: XLK is +35.3% vs
SPY but +63.0% vs RSP.

Switching benchmark does not reorder the field. Across 143 weeks the
cross-sectional rank correlation of RS-Ratio between the two is **+0.998**, and
at the latest bar all 13 members landed in the same quadrant under both. It
shifts everyone by a near-common amount, so quadrant agreement across the full
history is **67.8%** — irrelevant for anything clearly ahead or behind, decisive
for anything near the line.

### Data source

Tiingo's free tier covers this workload outright — 500 unique symbols/month and
50 requests/hour against a universe of 14 fetched weekly, with 30+ years of
history. It is personal-use-only; the paid tier is $30/month. Set `TIINGO_API_KEY`
and flip `provider` to use it.

The default is `yfinance` so the project runs with no signup. It is an unofficial
scraper and can break without warning.

### Warmup

Nothing is plotted until the method has enough history to mean anything. With
`adjust=False`, an EMA is seeded from its first observation and needs ~3 spans
before the seed stops showing. Two EMAs run in sequence — EMA_long over RS, then
EMA_mom over RS_Ratio — so their warmups add, and the tail needs its own bars on
top:

```
warmup = 3*EMA_long + 3*EMA_mom + tail
```

At the settings above that is 132 weekly bars, ~2.5 years, which is why
`lookback_years = 5`.

## Data handling

- Adjusted closes unless stated otherwise.
- Points still inside the warmup are not plotted.
- Gaps, holidays, mismatched calendars between benchmark and constituents, and
  symbols with insufficient history are handled explicitly. A symbol that cannot
  be computed is dropped and named in the output — never interpolated or
  forward-filled silently.
- The benchmark defines the calendar. A date where any member lacks a real close
  is dropped for everyone, so cross-sectional scoring always compares a complete
  peer group.

## Roadmap

- [x] Data retrieval and validation for a single symbol
- [x] RS-Ratio and RS-Momentum for a single symbol, hand-checkable
- [x] Extend to full universe
- [x] Chart rendering with tails
- [ ] Report layout and summary table
- [ ] Scheduling
- [ ] Email delivery

## Caveats

The JdK RS-Ratio and JdK RS-Momentum formulas are proprietary to RRG Research and
licensed to StockCharts. What's implemented here is a public approximation built
from the same published concept. Output will not match StockCharts and should not
be described as if it does.

This is a visualization and analysis tool. It describes where securities sit
relative to a benchmark. It does not produce trade recommendations and is not
investment advice.
