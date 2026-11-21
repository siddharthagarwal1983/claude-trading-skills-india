# India (NSE) Notes for VCP Screening

## Data

- **Source:** official NSE bhavcopy end-of-day data via NSE's public MCP server
  (`mcp.nseindia.in`). Free, no key; terms describe it as for educational/research use.
- **Adjustment:** NSE history is *as traded*. The shared India data module back-adjusts
  prices (× factor) and volumes (÷ factor) for bonuses and splits from NSE corporate
  actions before any moving average is computed. Without this, a 1:1 bonus looks like a
  50% crash and wrecks the 50/150/200-day averages.
- **Demergers:** NSE reports them with a neutral factor, so prices before the ex-date stay
  unadjusted. The screener skips any stock with an unexplained >20% one-day move inside
  the last ~260 sessions (`unadjusted_corporate_action` in the coverage block).
- **History depth:** the free archive is continuous from January 2024 (with holes in
  2023). The screener needs ~230 sessions, which 2024 onward covers.
- **Freshness:** a session's bhavcopy is treated as final after 19:00 IST. Running
  earlier in the evening screens the previous session. Stocks whose last bar is older
  than the expected session are reported as `stale_price_data`.

## Universe and benchmark

- Default universe: NIFTY 500 constituents (NSE CSV, cached for 7 days). Other choices:
  nifty50/100/200, midcap150, smallcap250, microcap250, totalmarket (750).
- Relative strength is measured against an NSE index, default **NIFTY 500** (the closest
  analogue to Minervini's use of the S&P 500). Use `--benchmark "NIFTY 50"` for a
  large-cap lens or `"NIFTY MIDCAP 150"` when screening mid caps.

## India-specific thresholds

| Check | US default | India default | Why |
|---|---|---|---|
| Minimum price | $10 | ₹50 | Excludes penny stocks, which are dominated by operator activity on NSE/BSE |
| Liquidity | 200,000 shares/day | ₹5 crore average daily turnover (50 days) | Share counts mean little when prices range from ₹50 to ₹1,00,000+ |
| Benchmark | S&P 500 (SPY) | NIFTY 500 index | Broad Indian market |

Tighten liquidity (`--min-turnover-cr 20`) for larger positions; a common rule is to
keep a position under ~1% of the stock's average daily turnover.

## Execution realities on NSE

- **Price bands (circuits).** Many stocks have 5%, 10% or 20% daily bands. A breakout
  that locks at the upper circuit may be unfillable that day, and a stop can be gapped
  through at a lower circuit. Prefer names in the F&O segment or with 20% bands for
  breakout entries.
- **Surveillance lists (ASM/GSM).** Stocks under additional surveillance need higher
  margins and may move to trade-for-trade settlement; avoid fresh breakout entries there.
- **Pre-open session (09:00-09:15 IST).** Opening prices are set by call auction; gap
  entries above the pivot are common after strong results.
- **Settlement:** T+1. No fractional shares.

## Hand-off

Size candidates with `india-position-sizer` (charges included). The US
`breakout-trade-planner` emits Alpaca order templates and does not apply to NSE.
