---
name: india-stock-analysis
description: Research a single Indian NSE-listed stock — business, quarterly and annual results, valuation, ownership and promoter pledge, filings, technicals and India-specific risks — and write a structured research note with bull and bear cases. Builds price, technical, dividend and event facts free from NSE data, then spends about one DalalOS credit for a sourced company report and uses web search for news. Use when the user asks to analyze an Indian stock, "how is <NSE company> doing", whether a Nifty stock looks attractive, or wants a research memo on an Indian company.
---

# India Stock Analysis

## Overview

Produce an evidence-based research note on one NSE stock. Free data first, paid data
sparingly:

| Layer | Source | Cost |
|---|---|---|
| Price, trend, RSI/ATR, liquidity, relative strength, dividends, results date | `scripts/india_stock_snapshot.py` (NSE) | free |
| Results, valuation history, ownership, pledge, dividends, recent filings | DalalOS `get_company_report` (compact) | ~1 credit |
| Detail only if needed: `get_financials`, `get_shareholding`, `get_stock_events` / `get_filing_extracts` | DalalOS | ~1 credit each |
| Media news and management commentary | WebSearch | free |

## When to Use

- "Analyze <company>" for any NSE-listed stock.
- Due diligence before adding a candidate from an India screener.
- Reviewing a holding after results or a major announcement.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-stock-analysis/requirements.txt`.
- Network access to NSE. DalalOS connector in the session for fundamentals.

## Workflow

### Step 1: Free fact sheet

```bash
python3 skills/india-stock-analysis/scripts/india_stock_snapshot.py <SYMBOL> --output-dir reports/
```

If the symbol is unknown, resolve it with DalalOS `search_stocks` (~1 credit) or the NSE
constituent lists. Mention any unexplained-jump warning (often a demerger).

### Step 2: Fundamentals (about 1 credit)

First check the cache:
`python3 skills/india-stock-analysis/scripts/_india_data.py metered get --source dalalos --tool get_company_report --args '{"query": "<SYMBOL>"}'`.
On a miss, call DalalOS `get_company_report` with `query=<SYMBOL>` (compact), save the reply,
store it with `metered put ... --ttl-days 7` and log it with `ledger record --credits 1`.
Call `get_financials`, `get_shareholding` or `get_stock_events` only when the report lacks
what the question needs.

### Step 3: News

Use WebSearch for the last few weeks of news, results commentary and analyst actions.
Prefer exchange filings and established Indian financial media; note dates.

### Step 4: Write the note

Follow `references/india_analysis_template.md`. Load `references/india_risk_checklist.md`
for India-specific risks (promoter pledge, related-party transactions, surveillance lists,
price bands, PSU and regulatory risk, auditor changes). Quote figures with their period and
source; give a bull case, a bear case, the key numbers to watch and what would change the
view. Save the note to `reports/india_stock_analysis_<SYMBOL>_YYYY-MM-DD.md`.

## Output Format

- Fact sheet: `reports/india_stock_snapshot_<SYMBOL>_YYYY-MM-DD_HHMMSS.json` and `.md`.
- Research note (written by Claude): sections per the template, in English, ₹ figures with
  Indian units (lakh/crore), sources cited.

## Resources

- `scripts/india_stock_snapshot.py` — free NSE fact sheet.
- `scripts/_india_data.py` — vendored NSE data module and metered cache / ledger CLI.
- `references/india_analysis_template.md`, `references/india_risk_checklist.md`.

## Key Principles

1. Free data first; one DalalOS company report usually suffices.
2. Evidence, not verdicts: no target prices or buy/sell calls; present scenarios.
3. Every figure carries its period and source.
