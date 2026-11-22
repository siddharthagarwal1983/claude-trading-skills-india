---
name: india-earnings-calendar
description: Show which Indian NSE companies (default NIFTY 500) report quarterly results in the coming days, enriched with NSE industry, Nifty size tier, last close and rupee turnover, and flag held stocks that report inside the window. Keeps a local results-date archive fed by Drishti's free upcoming-earnings call so past dates later feed india-earnings-trade-analyzer at no cost. Use when the user asks for the Indian results calendar, which Nifty stocks report this week, or whether a holding reports before a planned exit.
---

# India Earnings Calendar

## Overview

List NSE board-meeting dates for quarterly results in a window (default 14 days) for a
Nifty universe, with tier, industry and liquidity, and call out holdings that report.
Results dates come from connectors that only Claude can call, so the workflow is: fetch
in-session (free) → save → `ingest` into the archive → `upcoming` report.

## When to Use

- "Which Nifty 500 companies report results this week?"
- Before holding an NSE swing position through a results date.
- To build the results-date history that `india-earnings-trade-analyzer` reads.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-earnings-calendar/requirements.txt`.
- Drishti connector (free call) or DalalOS connector (1 credit) in the Claude session.
- Network access to NSE for prices (or `--no-prices`).

## Workflow

### Step 1: Get the symbol filter (free)

```bash
python3 skills/india-earnings-calendar/scripts/india_results_calendar.py symbols --universe nifty500
```

### Step 2: Fetch upcoming results in the session (free)

Call Drishti `get_upcoming_earnings` (0 credits) with
`{"query": {"symbols": "<comma list from step 1>", "limit": 100}}`. Repeat with
`"page": 2, 3, ...` while `has_next` is true. Save each reply verbatim, e.g.
`reports/drishti_upcoming_YYYY-MM-DD.json`, and log it:

```bash
python3 skills/india-earnings-calendar/scripts/_india_data.py ledger record \
  --source drishti --tool get_upcoming_earnings --credits 0
```

Fallback only if Drishti is unavailable: DalalOS `get_upcoming_results` (1 credit, up to
90 days). Avoid DalalOS `get_market_calendar` for past dates across the whole market: one
reply holds at most ~250 rows and a single results-season day can exceed that.

### Step 3: Ingest and report

```bash
python3 skills/india-earnings-calendar/scripts/india_results_calendar.py \
  ingest --events "reports/drishti_upcoming_*.json"
python3 skills/india-earnings-calendar/scripts/india_results_calendar.py \
  upcoming --days 14 --holdings TCS,INFY --output-dir reports/
```

Run the fetch and ingest daily during results season (mid-January to mid-February,
mid-April to May, mid-July to mid-August, mid-October to mid-November) so the archive keeps
every date once it passes.

### Step 4: Advise

Load `references/india_results_calendar_notes.md`. For each held stock reporting in the
window, tell the user to decide beforehand whether to hold, trim or exit: results can gap a
stock through its stop and through price bands.

## Output Format

`reports/india_earnings_calendar_YYYY-MM-DD_HHMMSS.json` and `.md`: window, count,
`holdings_reporting`, counts by tier, and per date the symbol, company, tier, industry,
quarter, last close, 50-day turnover (₹ crore), days until and a held flag.
The archive lives at `.cache/india-data/results_calendar.json`.

## Resources

- `scripts/india_results_calendar.py` — symbols / ingest / upcoming CLI.
- `scripts/_india_data.py` — vendored NSE data module (generated; do not edit), also the
  credit-ledger CLI.
- `references/india_results_calendar_notes.md`.

## Key Principles

1. Use the free Drishti call; spend DalalOS credits only as a fallback.
2. Board-meeting dates can change; re-fetch before acting on a date.
3. Flag holdings that report; the hold-or-exit decision is the user's.
