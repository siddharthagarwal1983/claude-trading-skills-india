---
name: india-downtrend-duration-analyzer
description: Analyze how long peak-to-trough declines in Indian NSE stocks typically last, grouped by NSE industry and by size tier (Nifty 100 / Midcap 150 / Smallcap 250 / Microcap 250), using free official NSE end-of-day data adjusted for bonuses and splits, and render an interactive HTML histogram. Use when the user asks how long corrections last in Indian stocks, typical downtrend duration by sector or market cap on NSE, or wants time-stop baselines for Nifty stocks. No API key required.
---

# India Downtrend Duration Analyzer

## Overview

Detect local peaks and troughs in daily NSE closes, measure each decline of at least
`--min-depth` percent, and summarise durations (median, mean, P25/P75/P90) overall, by NSE
industry and by size tier. Method identical to the US `downtrend-duration-analyzer`; data
and grouping are Indian (see `references/india_market_notes.md`).

## When to Use

- The user asks how long corrections typically last for Indian stocks or a Nifty sector.
- Setting time-based stops or patience thresholds for NSE swing trades.
- Comparing large-cap versus small-cap recovery behaviour on NSE.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-downtrend-duration-analyzer/requirements.txt`
  (numpy, pandas, pandas-market-calendars).
- Network access to NSE, no API key. The first NIFTY 500 run downloads history since
  2024 for every stock (allow about an hour); reruns use the cache in `./.cache/india-data/`.

## Workflow

### Step 1: Run the analysis

```bash
# NIFTY 500 since 2024-01-01
python3 skills/india-downtrend-duration-analyzer/scripts/analyze_india_downtrends.py \
  --output-dir reports/

# One industry
python3 skills/india-downtrend-duration-analyzer/scripts/analyze_india_downtrends.py \
  --sector "Information Technology" --output-dir reports/

# Deeper declines only, small caps
python3 skills/india-downtrend-duration-analyzer/scripts/analyze_india_downtrends.py \
  --universe niftysmallcap250 --min-depth 10 --output-dir reports/
```

### Step 2: Generate the histogram

```bash
python3 skills/india-downtrend-duration-analyzer/scripts/generate_india_histogram_html.py \
  --input "reports/india_downtrend_analysis_*.json" --output-dir reports/
```

### Step 3: Interpret

Load `references/downtrend_methodology.md` for the peak/trough method and percentile use,
and `references/india_market_notes.md` for tier definitions, data caveats and dropped
declines. Quote durations in trading sessions. Flag thin groups and the short history
(2024 onward) when drawing conclusions.

## Output Format

- `reports/india_downtrend_analysis_YYYY-MM-DD_HHMMSS.json`: `parameters` (universe,
  start/end dates, windows, depth), `summary`, `by_sector`, `by_market_cap` (Large / Mid /
  Small / Micro / Unknown), `downtrends` (symbol, sector, tier, peak/trough dates and
  prices, duration in sessions, depth %), `data_notes` (skipped symbols, dropped declines,
  archive gaps, tier-list problems).
- `reports/india_downtrend_analysis_YYYY-MM-DD_HHMMSS.md`: summary tables.
- `reports/india_downtrend_histogram_YYYY-MM-DD_HHMMSS.html`: interactive histogram with
  industry and tier filters.

## Resources

- `scripts/analyze_india_downtrends.py` — analysis and CLI.
- `scripts/generate_india_histogram_html.py` — HTML histogram.
- `scripts/_india_data.py` — vendored NSE data module (generated; do not edit).
- `references/downtrend_methodology.md`, `references/india_market_notes.md`.

## Key Principles

1. Durations describe history, not a forecast for the next decline.
2. Small samples and a three-year window limit confidence; say so.
3. Corporate-action artefacts are removed, not counted as corrections.
