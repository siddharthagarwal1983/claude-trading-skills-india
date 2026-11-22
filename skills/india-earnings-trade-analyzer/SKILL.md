---
name: india-earnings-trade-analyzer
description: Score recent Indian NSE results reactions with the 5-factor earnings-trade model (reaction size, pre-results trend, volume trend, 200- and 50-day average position) and grade A-D, using free NSE prices and results dates from the india-earnings-calendar archive or a saved Drishti/DalalOS calendar file. Measures the two-day reaction because NSE does not publish announcement times, and grades only positive reactions by default. Use when the user asks which Nifty stocks reacted best to results, for post-results momentum candidates in India, or for earnings gap analysis on NSE.
---

# India Earnings Trade Analyzer

## Overview

Find NSE stocks with strong, well-supported reactions to quarterly results. Same weights
and grades as the US `earnings-trade-analyzer` (reaction 25%, pre-results 20-day trend 30%,
volume trend 20%, MA200 position 15%, MA50 position 10%), with Indian adaptations:

- Results dates from the local archive (`india-earnings-calendar`) or a saved calendar file.
- Reaction = two-day move (close after the board meeting vs close before it), because many
  Indian companies announce during market hours. `--reaction next_open` uses the US
  next-session gap instead.
- Only positive reactions are graded by default (`--direction up`): the grade guidance
  describes long entries.
- Liquidity gates in rupees: price ≥ ₹50 and 50-day turnover ≥ ₹5 crore.

## When to Use

- During or just after an Indian results season, to find post-results leaders.
- As the candidate source for post-results drift (PEAD-style) swing trades on NSE.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-earnings-trade-analyzer/requirements.txt`.
- Results dates: the `india-earnings-calendar` archive (free), or one saved DalalOS
  `get_market_calendar` reply for a narrow past window (~1 credit per ~250 rows).
- Network access to NSE for prices. No API key.

## Workflow

### Step 1: Make sure results dates exist

Prefer the archive maintained by `india-earnings-calendar` (daily free Drishti fetch). To
backfill a gap, call DalalOS `get_market_calendar` with `types=["results"]` and a window of
one or two days, save the reply to `reports/dalalos_results_<dates>.json`, and log the
credit with `scripts/_india_data.py ledger record --source dalalos --tool get_market_calendar --credits 1`.

### Step 2: Run the analyzer

```bash
# Archive-based, last 7 days of board meetings
python3 skills/india-earnings-trade-analyzer/scripts/analyze_india_earnings_trades.py --output-dir reports/

# A saved calendar file, wider window, both directions for research
python3 skills/india-earnings-trade-analyzer/scripts/analyze_india_earnings_trades.py \
  --events "reports/dalalos_results_*.json" --lookback-days 14 --direction both --output-dir reports/
```

### Step 3: Interpret

Load `references/scoring_methodology.md` for factor scoring and grades (India section on
reaction timing). Report grade, score, two-day reaction, next-session gap, trend, volume
ratio and MA distances. List `metadata.skipped` reasons (awaiting reaction session,
outside universe, liquidity, negative reaction) so the user knows what was excluded.

### Step 4: Hand off

Chart-check A/B names, then size with `india-position-sizer`. Check
`india-exposure-coach` first; post-results momentum fails more often in weak markets.

## Output Format

`reports/india_earnings_trade_YYYY-MM-DD_HHMMSS.json` and `.md`: per stock grade, score,
`gap_pct` (the scored reaction), `two_day_reaction_pct`, `next_session_gap_pct`, quarter,
price, `avg_turnover_cr`, industry and the five components; metadata with the reaction
measure, direction, window, events files and skipped symbols.

## Resources

- `scripts/analyze_india_earnings_trades.py` — CLI and NSE adaptation.
- `scripts/calculators/`, `scripts/scorer.py`, `scripts/report_generator.py` — 5-factor engine.
- `scripts/_india_data.py` — vendored NSE data module (generated; do not edit).
- `references/scoring_methodology.md`.

## Key Principles

1. A strong reaction is a candidate, not an entry; wait for a chart setup.
2. Results dates without a completed reaction session are skipped, never guessed.
3. Spend DalalOS credits only on narrow backfills; the archive is free.
