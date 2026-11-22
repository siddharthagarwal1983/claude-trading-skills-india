# India Exposure Framework

## Purpose

Turn the India regime evidence into one posture for an NSE swing book: an equity
exposure ceiling (0-100%) and an action. It sizes risk; it does not pick stocks or time
entries.

## Inputs and weights

| Input | Weight | Source | Score mapping |
|---|---:|---|---|
| Breadth (critical) | 25% | `india-market-breadth-analyzer` composite | used directly (0-100) |
| Distribution (critical) | 20% | `india-ibd-distribution-day-monitor` overall level | NORMAL 85, CAUTION 60, HIGH 35, SEVERE 15 |
| Trend (critical) | 20% | NIFTY 50 vs its 50/200-day SMA (fetched) | above both and 50 > 200: 85; above 200: 55; above 50 only: 40; below both: 15 |
| Volatility | 15% | INDIA VIX (fetched) | < 12: 85, 12-16: 75, 16-20: 55, 20-25: 35, ≥ 25: 15; −15 if VIX rose > 20% in 5 sessions |
| FTD | 10% | `india-ftd-detector` | FTD_CONFIRMED: quality score; FTD_WINDOW 45; RALLY_ATTEMPT 40; CORRECTION 20; RALLY_FAILED / FTD_INVALIDATED 15; NO_SIGNAL: not applicable |
| FII/DII flows | 5% | optional JSON (e.g. DalalOS daily flows) | 20-session net: both buying 80; combined positive 60; mixed negative 40; both selling 20 |
| Valuation | 5% | NIFTY 50 P/E percentile (fetched) | ≤ 20th: 80; 20-80th: 60; ≥ 80th: 40; ≥ 95th: 30 |

Missing inputs are excluded and weights rescaled. Each missing **critical** input then
subtracts 10 points. Inputs older than `--max-age-sessions` (default 2) or dated after the
evaluation session are ignored and listed as stale.

## Decision

| Composite | Exposure ceiling |
|---|---|
| ≥ 80 | 90-100% |
| 65-79 | 70-89% |
| 50-64 | 50-69% |
| 35-49 | 30-49% |
| 20-34 | 10-29% |
| < 20 | 0-9% |

Action, checked in order:

1. **CASH_PRIORITY** if composite < 30, or distribution score < 25 (SEVERE).
2. **REDUCE_ONLY** if composite < 50, distribution score < 40 (HIGH), or two or more
   critical inputs are missing.
3. **NEW_ENTRY_ALLOWED** otherwise.

Participation: BROAD when ≥ 60% of stocks are above their 200-day SMA and ≥ 55% above
their 50-day; MODERATE when ≥ 40% are above the 200-day; NARROW otherwise.

Confidence: HIGH with ≥ 6 inputs and no missing critical input; MEDIUM with ≥ 4 inputs and
at most one missing critical input; LOW otherwise.

## FII/DII flow file

Any JSON list (or a dict with `rows`, `flows` or `data`) of daily rows with a `date` and
keys starting `fii`/`dii` that contain `net`, values in ₹ crore:

```json
{"rows": [{"date": "2026-11-20", "fii_net_cr": -1250.4, "dii_net_cr": 1980.2}]}
```

Inside a Claude session the data can come from DalalOS `get_market_fii_dii_flow` (one
call returns a date range). Cache the response with
`python3 skills/india-exposure-coach/scripts/_india_data.py metered put --source dalalos --tool get_market_fii_dii_flow ...`
and log the credit with `ledger record`, so a weekly call serves several daily runs.

## Differences from the US exposure coach

- Inputs are the India skills plus NSE market context; US macro regime, top-risk, theme
  and sector inputs are not used.
- A missing FTD is "not applicable" in an uptrend instead of a zero score that would drag
  a healthy market down.
- INDIA VIX and FII/DII flows replace US volatility and 13F institutional inputs.
