# India Market Breadth Methodology

The US `market-breadth-analyzer` scores TraderMonty's published S&P 500 breadth series,
whose signal flags rely on about ten years of history and undisclosed backtests. NSE's free
archive is continuous only from January 2024, so the India skill computes its own breadth
from constituent prices with a simpler, fully documented model. It is not a port of the
US scores and its numbers are not comparable with them.

## Universe and data

- Constituents of a Nifty index (default NIFTY 500) from NSE's CSV files, applied to every
  past session. This carries **survivorship bias**: stocks that left the index are absent,
  which flatters historical breadth slightly.
- Daily bars from NSE bhavcopy, adjusted for bonuses and splits. Muhurat sessions are
  excluded. The NIFTY 500 index supplies the session calendar.
- A stock contributes to a measure only when it has enough history for it (20, 50 or 200
  sessions; 252 for 52-week highs/lows). Percentages are of measurable stocks.
- The run is refused if fewer than 90% of constituents load.

## Daily measures

| Measure | Definition |
|---|---|
| % above 20 / 50 / 200-day SMA | Close above its simple moving average |
| Advances / declines | Close versus the prior close |
| New 52-week high / low | High (low) beyond every high (low) of the prior 251 sessions |
| Net new highs % | (new highs − new lows) / stocks with a 52-week window |
| Breadth 8MA / 50MA | 8- and 50-session averages of % above 200-day SMA |
| McClellan oscillator | EMA19 − EMA39 of (advances − declines) / (advances + declines) × 1000 |
| Summation index | Running total of the oscillator |

## Composite score (0-100)

| Component | Weight | Input | Score points |
|---|---:|---|---|
| Participation | 30% | % above 200-day SMA | 15%→0, 30%→25, 50%→60, 70%→100 |
| Momentum | 20% | % above 50-day SMA | same as participation |
| Trend | 20% | 8MA − 50MA of %>200DMA (pp) | −10→0, −5→30, 0→60, +5→100 |
| New highs vs lows | 15% | 10-session mean of net new highs % | −5→0, 0→50, +5→100 |
| McClellan | 15% | Oscillator | −50→0, 0→50, +50→100 |

Scores interpolate linearly between points and are clipped at the ends. Missing components
(not enough history) are excluded and the remaining weights rescaled; they are listed in
`composite.missing_components`.

| Composite | Zone | Meaning |
|---|---|---|
| ≥ 80 | Strong | Broad participation; new entries supported |
| 60-79 | Healthy | Most stocks trending up |
| 40-59 | Neutral | Mixed; be selective |
| 20-39 | Weak | Narrow market; reduce new risk |
| < 20 | Critical | Broad breakdown; protect capital |

## Flags

- **Deteriorating:** breadth 8MA below its 50MA while the 50MA is falling (compared with
  five sessions earlier), the India analogue of the US "pink zone".
- **Washout:** fewer than 15% of stocks above their 50-day SMA. Historically these
  conditions sit near tradable lows, but need an FTD and improving breadth to act on.

## Reading the result

- Use the zone for posture and the trend component for direction of change.
- `historical_context.pct_above_200_percentile` places today within the available
  history (roughly mid-2024 onward), which covers few full cycles.
- NSE-wide advances/declines (all listed securities) are shown for context only; they
  include SME, ETF and debt series and are not part of the score.
