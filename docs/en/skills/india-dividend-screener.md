---
layout: default
title: "India Dividend Screener"
grand_parent: English
parent: Skill Guides
nav_order: 36
lang_peer: /ja/skills/india-dividend-screener/
permalink: /en/skills/india-dividend-screener/
generated: true
---

# India Dividend Screener
{: .no_toc }

Screen Indian NSE stocks for dividend income quality — trailing yield, consistency across Indian fiscal years, dividend growth and cuts, P/E (when a DalalOS screen is supplied) and an RSI pullback — using free NSE corporate-action history with dividends restated for later bonuses and splits. Use when the user asks for Indian dividend stocks, high-yield Nifty stocks, dividend growers on NSE, or dividend stocks on a pullback.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-dividend-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-dividend-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Rank NSE dividend payers on a 0-100 score: regular yield (30), consistency (25), growth
(20, minus 5 per cut), valuation by P/E (15, only when known) and RSI pullback (10). Five
years of dividend history come free from NSE corporate actions. Amounts are restated per
today's share for later bonuses and splits — without this, a stock that split 10-for-1
after a dividend shows a yield ten times too high (some data vendors make exactly this
error). Fiscal years run April-March.

---

## 2. When to Use

- "Find good dividend stocks in the Nifty 500" / "high-yield Indian stocks".
- Building an income portfolio; checking a holding's dividend record.
- Dividend growers that have pulled back (RSI ≤ 40).

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-dividend-screener/requirements.txt`.
- Network access to NSE. No API key for the free path.
- Optional: one DalalOS `screen_stocks` call (~1 credit) for a fundamentals-filtered
  candidate list with P/E, P/B, ROE and debt/equity.

---

## 4. Quick Start

```bash
python3 skills/india-dividend-screener/scripts/screen_india_dividends.py \
  --screen-json "reports/dalalos_dividend_screen_*.json" --output-dir reports/

python3 skills/india-dividend-screener/scripts/screen_india_dividends.py \
  --universe nifty500 --min-yield 2 --min-paying-years 3 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Choose the candidate source

- **Fundamentals-first (recommended weekly, ~1 credit):** call DalalOS `screen_stocks` with
  e.g. `{"index": "NIFTY 500", "dividend_yield_min": 0.02, "pe_max": 30, "roe_min": 0.12}`,
  `sort_by: "dividend_yield"`, `limit: 100`. Save the reply to
  `reports/dalalos_dividend_screen_YYYY-MM-DD.json`, cache it with
  `scripts/_india_data.py metered put --source dalalos --tool screen_stocks --args '<filters JSON>' --payload-file <file> --ttl-days 7`
  and log the credit with `ledger record`.
- **Free:** `--universe nifty500` (or nifty100, ...) screens every constituent on NSE data
  alone (no valuation component).

### Step 2: Run the screener

```bash
python3 skills/india-dividend-screener/scripts/screen_india_dividends.py \
  --screen-json "reports/dalalos_dividend_screen_*.json" --output-dir reports/

python3 skills/india-dividend-screener/scripts/screen_india_dividends.py \
  --universe nifty500 --min-yield 2 --min-paying-years 3 --output-dir reports/
```

### Step 3: Interpret

Load `references/india_dividend_notes.md` for scoring, Indian dividend taxation and
payout patterns (PSU and commodity payers). Report grade, yield (regular and special),
fiscal-year totals, CAGR, cuts, RSI and, when present, P/E, ROE and debt/equity. Point out
cyclical payers (metals, oil, PSU banks) whose high yields depend on commodity prices or
government policy.

---

## 6. Resources

**References:**

- `skills/india-dividend-screener/references/india_dividend_notes.md`

**Scripts:**

- `skills/india-dividend-screener/scripts/_india_data.py`
- `skills/india-dividend-screener/scripts/dividend_calculator.py`
- `skills/india-dividend-screener/scripts/screen_india_dividends.py`
