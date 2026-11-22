---
layout: default
title: "India Earnings Trade Analyzer"
grand_parent: English
parent: Skill Guides
nav_order: 41
lang_peer: /ja/skills/india-earnings-trade-analyzer/
permalink: /en/skills/india-earnings-trade-analyzer/
generated: true
---

# India Earnings Trade Analyzer
{: .no_toc }

Score recent Indian NSE results reactions with the 5-factor earnings-trade model (reaction size, pre-results trend, volume trend, 200- and 50-day average position) and grade A-D, using free NSE prices and results dates from the india-earnings-calendar archive or a saved Drishti/DalalOS calendar file. Measures the two-day reaction because NSE does not publish announcement times, and grades only positive reactions by default. Use when the user asks which Nifty stocks reacted best to results, for post-results momentum candidates in India, or for earnings gap analysis on NSE.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-earnings-trade-analyzer.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-earnings-trade-analyzer){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

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

---

## 2. When to Use

- During or just after an Indian results season, to find post-results leaders.
- As the candidate source for post-results drift (PEAD-style) swing trades on NSE.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-earnings-trade-analyzer/requirements.txt`.
- Results dates: the `india-earnings-calendar` archive (free), or one saved DalalOS
  `get_market_calendar` reply for a narrow past window (~1 credit per ~250 rows).
- Network access to NSE for prices. No API key.

---

## 4. Quick Start

```bash
# Archive-based, last 7 days of board meetings
python3 skills/india-earnings-trade-analyzer/scripts/analyze_india_earnings_trades.py --output-dir reports/

# A saved calendar file, wider window, both directions for research
python3 skills/india-earnings-trade-analyzer/scripts/analyze_india_earnings_trades.py \
  --events "reports/dalalos_results_*.json" --lookback-days 14 --direction both --output-dir reports/
```

---

## 5. Workflow

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

---

## 6. Resources

**References:**

- `skills/india-earnings-trade-analyzer/references/scoring_methodology.md`

**Scripts:**

- `skills/india-earnings-trade-analyzer/scripts/_india_data.py`
- `skills/india-earnings-trade-analyzer/scripts/analyze_india_earnings_trades.py`
- `skills/india-earnings-trade-analyzer/scripts/report_generator.py`
- `skills/india-earnings-trade-analyzer/scripts/scorer.py`
