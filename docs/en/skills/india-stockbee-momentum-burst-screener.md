---
layout: default
title: "India Stockbee Momentum Burst Screener"
grand_parent: English
parent: Skill Guides
nav_order: 50
lang_peer: /ja/skills/india-stockbee-momentum-burst-screener/
permalink: /en/skills/india-stockbee-momentum-burst-screener/
generated: true
---

# India Stockbee Momentum Burst Screener
{: .no_toc }

Screen Indian NSE stocks for Stockbee-style 3-5 day momentum burst candidates (4% breakout, rupee breakout, range expansion) with rupee-turnover liquidity gates, NSE session freshness, and an upper-circuit warning, using free official NSE end-of-day data. Use when the user asks for momentum bursts, Stockbee setups, 4% breakouts, or short swing momentum candidates in the Indian market or Nifty stocks. No API key required.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-stockbee-momentum-burst-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-stockbee-momentum-burst-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Find NSE stocks that just fired a Stockbee momentum burst: a 4% breakout, a rupee
breakout or a range expansion on expanding volume out of a tight base, with a close near
the high and a nearby stop. Scoring, states and failure filters match the US
`stockbee-momentum-burst-screener`; the data, calendar and thresholds are Indian (see
`references/india_market_notes.md`).

States: `ACTIONABLE_DAY1`, `MANUAL_REVIEW`, `MANUAL_REVIEW_ONLY`, `WATCH_ONLY`, `REJECTED`.

---

## 2. When to Use

- The user asks for momentum bursts, 4% breakouts or Stockbee setups on NSE.
- An end-of-day scan of the Nifty 500 (or another Nifty index) for 3-5 day swing ideas.
- Building a model book of Indian momentum bursts.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-stockbee-momentum-burst-screener/requirements.txt`.
- Network access to NSE (`mcp.nseindia.in`, `nsearchives.nseindia.com`), or an offline
  `--prices-json` file. No API key.
- Run after 19:00 IST to screen the same day's session.

---

## 4. Quick Start

```bash
# NIFTY 500 scan
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --universe nifty500 --market-gate neutral --output-dir reports/

# Explicit symbols, include rejected names in the markdown
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --symbols TATAMOTORS TRENT BEL --include-rejected --output-dir reports/

# Offline OHLCV JSON (symbol -> list of daily bars)
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --prices-json data/nse_daily.json --output-dir reports/

# A past session
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --universe nifty200 --as-of 2026-11-13 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Set the market gate

Pass the day's regime decision with `--market-gate allowed|neutral|restrictive`. With
`restrictive`, strong setups become `MANUAL_REVIEW_ONLY`.

### Step 2: Run the screener

```bash
# NIFTY 500 scan
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --universe nifty500 --market-gate neutral --output-dir reports/

# Explicit symbols, include rejected names in the markdown
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --symbols TATAMOTORS TRENT BEL --include-rejected --output-dir reports/

# Offline OHLCV JSON (symbol -> list of daily bars)
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --prices-json data/nse_daily.json --output-dir reports/

# A past session
python3 skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py \
  --universe nifty200 --as-of 2026-11-13 --output-dir reports/
```

India thresholds: `--min-price` (₹50), `--min-turnover-cr` (₹2 crore),
`--rupee-threshold` (₹25), `--rupee-breakout-min-pct` (1.5%), `--high-volume-threshold`
(5,000,000 shares).

### Step 3: Review candidates

Load `references/momentum_burst_methodology.md` and `references/scoring_system.md` to
explain triggers and scores, `references/entry_exit_rules.md` for the 3-5 day exit
template, and `references/india_market_notes.md` for circuits and India-specific gates.

Treat `possible_upper_circuit` as a warning: check the stock's price band before planning
an entry. Read `metadata.fetch_errors` and the report warnings before concluding there
were no setups.

### Step 4: Hand off

Send `ACTIONABLE_DAY1` and upgraded `MANUAL_REVIEW` names to chart review, then to
`india-position-sizer` with `entry_reference` and `stop_reference`. Orders are placed
manually.

---

## 6. Resources

**References:**

- `skills/india-stockbee-momentum-burst-screener/references/entry_exit_rules.md`
- `skills/india-stockbee-momentum-burst-screener/references/india_market_notes.md`
- `skills/india-stockbee-momentum-burst-screener/references/momentum_burst_methodology.md`
- `skills/india-stockbee-momentum-burst-screener/references/scoring_system.md`

**Scripts:**

- `skills/india-stockbee-momentum-burst-screener/scripts/_india_data.py`
- `skills/india-stockbee-momentum-burst-screener/scripts/screen_india_momentum_burst.py`
