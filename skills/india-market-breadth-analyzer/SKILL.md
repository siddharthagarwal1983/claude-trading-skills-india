---
name: india-market-breadth-analyzer
description: Measure Indian market breadth from NIFTY 500 constituents on free official NSE data — percent of stocks above their 20/50/200-day averages, net new 52-week highs, advance/decline and a McClellan oscillator — and score it 0-100 (Strong / Healthy / Neutral / Weak / Critical) with deterioration and washout flags. Use when the user asks about Indian market breadth, participation, how many NSE stocks are in uptrends, or needs the breadth input for india-exposure-coach. No API key required.
---

# India Market Breadth Analyzer

## Overview

Compute daily breadth for a Nifty universe (default NIFTY 500) from constituent prices
and score the latest session on participation, momentum, breadth trend, new highs versus
lows and the McClellan oscillator. This is an India-specific model, not a port of the US
TraderMonty-based analyzer (see `references/india_breadth_methodology.md` for why).

## When to Use

- The user asks how broad the Indian market move is, or how many NSE stocks are healthy.
- Daily regime check before NSE swing entries.
- As the breadth input to `india-exposure-coach`.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-market-breadth-analyzer/requirements.txt`.
- Network access to NSE. No API key.
- Warm the cache once (about 80 minutes for NIFTY 500):
  `python3 skills/india-market-breadth-analyzer/scripts/_india_data.py update --universe nifty500 --start 2024-01-01`.
  Later runs fetch one session per stock.

## Workflow

### Step 1: Run the analyzer

```bash
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py --output-dir reports/

# Large caps only, or a past session
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py \
  --universe nifty200 --as-of 2026-10-23 --output-dir reports/
```

### Step 2: Interpret

Load `references/india_breadth_methodology.md` for definitions, component scoring and
zones. Report the composite and zone, the five component inputs, the deterioration and
washout flags, coverage, and the percentile of today's %>200DMA within the available
history. Mention survivorship bias and the short history (2024 onward) when drawing
historical comparisons.

### Step 3: Feed the exposure decision

Pass the JSON to `india-exposure-coach --breadth`.

## Output Format

`reports/india_market_breadth_YYYY-MM-DD_HHMMSS.json` and `.md`.

JSON: `as_of`, `universe`, `coverage` (universe_size, loaded, not_loaded, verdict),
`latest` (pct_above_20/50/200, advances, declines, new_highs, new_lows,
net_new_highs_pct, breadth_8ma, breadth_50ma, mcclellan_oscillator, mcclellan_summation),
`composite` (composite_score, zone, guidance, missing_components), `components`, `flags`
(deteriorating, washout), `historical_context`, `nse_market_breadth` (NSE-wide A/D,
context only), `history` (last 60 sessions).

## Resources

- `scripts/india_breadth_analyzer.py` — CLI and data loading.
- `scripts/breadth_calculator.py` — pure breadth and scoring functions.
- `scripts/_india_data.py` — vendored NSE data module (generated; do not edit).
- `references/india_breadth_methodology.md`.

## Key Principles

1. Breadth describes participation; it does not time entries on its own.
2. Coverage below 90% of constituents is refused rather than reported as breadth.
3. Compare readings within this model only; they are not the US breadth index.
