# India (NSE) Notes for Downtrend Duration Analysis

## Size tiers

US dollar cut-offs do not translate to India, where SEBI classifies stocks by market-cap
rank. The analyzer uses Nifty index membership as the rank proxy:

| Tier | Membership | Approximate SEBI band |
|---|---|---|
| Large | NIFTY 100 | Top 100 by market cap |
| Mid | NIFTY Midcap 150 | Ranks 101-250 |
| Small | NIFTY Smallcap 250 | Ranks 251-500 |
| Micro | NIFTY Microcap 250 | Ranks 501-750 |
| Unknown | none of the above | Outside the lists or list unavailable |

Index lists are rebalanced semi-annually, so a stock is tiered by *current* membership,
not its tier at the time of each decline. Treat tier statistics as approximate.

## Sectors

Sectors are NSE "Industry" labels from the constituent files (e.g. Financial Services,
Information Technology, Capital Goods, Fast Moving Consumer Goods, Healthcare). Use the
same names with `--sector`.

## History and data quality

- Default start is 2024-01-01: NSE's free archive has large holes in 2023. If an earlier
  `--start` is passed, only the latest continuous stretch (no gap longer than 14 calendar
  days) is analysed and the report counts affected symbols.
- Prices are back-adjusted for bonuses and splits, so a 1:1 bonus is not mistaken for a
  50% decline.
- Demergers arrive with a neutral factor and leave a real-looking cliff in the price
  series. Declines spanning an unexplained >20% one-day move are dropped and counted
  under "Declines dropped for unadjusted corporate actions".
- Durations are counted in NSE trading sessions from the peak close to the trough close.

## Reading the numbers

- Nearly three years of data covers only a few market cycles. Indian small and micro caps
  had a sharp correction in early 2025; tier medians will reflect that episode heavily.
- Use the median for planning and P75-P90 for conservative time stops. Groups with fewer
  than about 20 declines are anecdotes.
- Recompute after major regime changes; do not reuse US baselines for NSE stocks.
