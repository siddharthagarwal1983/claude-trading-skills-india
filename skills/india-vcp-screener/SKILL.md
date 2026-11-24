---
name: india-vcp-screener
description: Screen Indian NSE stocks (default NIFTY 500) for Mark Minervini's Volatility Contraction Pattern using free official NSE end-of-day data adjusted for bonuses and splits, rupee-turnover liquidity gates, and relative strength against an NSE index. Use when the user asks for VCP, Minervini, Stage 2 or tight-base breakout candidates in the Indian market, Nifty stocks, or NSE swing setups. No API key required.
---

# India VCP Screener

## Overview

Find Stage 2 NSE stocks forming Volatility Contraction Patterns near a breakout pivot.
The scoring engine is the same as the US `vcp-screener` (trend template, contraction
analysis, volume dry-up, pivot proximity, relative strength, execution state, state caps);
the data and filters are Indian:

- Universe from NSE index constituent files (default NIFTY 500).
- Prices from NSE bhavcopy history, back-adjusted for bonuses and splits.
- Liquidity on average daily turnover in ₹ crore; minimum price ₹50.
- Relative strength against an NSE index (default NIFTY 500).
- Stocks with an unadjusted >20% jump (e.g. a demerger) are skipped, not mis-scored.

## When to Use

- The user asks for VCP / Minervini / Stage 2 / tight-base setups in Indian stocks.
- The user wants breakout candidates from the Nifty 500 or another Nifty index.
- Before sizing an NSE swing trade with `india-position-sizer`.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-vcp-screener/requirements.txt`
  (`pandas-market-calendars` for the NSE calendar).
- Network access to `mcp.nseindia.in` and `nsearchives.nseindia.com`. No API key.
- The first full NIFTY 500 run downloads ~3 years of history per stock (allow an hour);
  later runs fetch one session per stock. Cache: `./.cache/india-data/`.

## Workflow

### Step 1: Confirm the market allows new risk

If a regime check exists for the day, honour it: do not hunt breakouts when new entries
are not allowed.

### Step 2: Run the screener

```bash
# Default: NIFTY 500, benchmark NIFTY 500
python3 skills/india-vcp-screener/scripts/screen_india_vcp.py --output-dir reports/

# Only entry-ready names near the pivot
python3 skills/india-vcp-screener/scripts/screen_india_vcp.py --mode prebreakout --output-dir reports/

# Mid caps against a mid-cap benchmark, stricter liquidity
python3 skills/india-vcp-screener/scripts/screen_india_vcp.py \
  --universe niftymidcap150 --benchmark "NIFTY MIDCAP 150" --min-turnover-cr 10 --output-dir reports/

# Explicit symbols
python3 skills/india-vcp-screener/scripts/screen_india_vcp.py --symbols TRENT DIXON POLYCAB --output-dir reports/
```

Tuning flags match the US screener (`--trend-min-score`, `--min-contractions`,
`--t1-depth-min`, `--contraction-ratio`, `--breakout-volume-ratio`, `--strict`, ...).
Relative strength joins stock and benchmark bars on trading dates, so a session
missing from either side never shifts the comparison; each result's
`relative_strength.alignment` lists dropped dates. `--rs-alignment offset` restores
the earlier position-by-position comparison.

### Step 3: Read the results

Load `references/scoring_system.md` for score bands and `references/vcp_methodology.md`
for pattern rules. Load `references/india_market_notes.md` when explaining data
adjustments, skipped stocks, liquidity thresholds or NSE execution risks (circuits,
surveillance lists, pre-open gaps).

Check `evaluation_coverage` first. A `partial` verdict means some stocks were not
evaluated (missing or stale data, unadjusted corporate actions); say so rather than
presenting the list as complete.

### Step 4: Hand off

For each candidate the user wants to pursue, take the pivot as entry and the last
contraction low as stop into `india-position-sizer`. Prices are ₹ end-of-day values;
the user places orders manually.

## Output Format

`reports/india_vcp_screener_YYYY-MM-DD_HHMMSS.json` and `.md`.

JSON: `metadata` (exchange, currency, as_of session, benchmark, universe, liquidity,
funnel), `results` (per stock: composite_score, rating, execution_state, pattern_type,
valid_vcp, entry_ready, distance_from_pivot_pct, avg_turnover_cr, trend_template,
vcp_pattern, volume_pattern, pivot_proximity, relative_strength), `evaluation_coverage`.

## Resources

- `scripts/screen_india_vcp.py` — NSE screening pipeline.
- `scripts/calculators/`, `scripts/scorer.py`, `scripts/report_generator.py` — VCP engine.
- `scripts/_india_data.py` — vendored NSE data module (generated; do not edit).
- `references/vcp_methodology.md`, `references/scoring_system.md`,
  `references/india_market_notes.md`.

## Key Principles

1. Screen output is a candidate list, not a buy signal; confirm on the chart.
2. Report coverage honestly; never treat a partial run as "no setups".
3. Respect NSE price bands and surveillance lists when judging entries.
