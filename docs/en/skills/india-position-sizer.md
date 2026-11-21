---
layout: default
title: "India Position Sizer"
grand_parent: English
parent: Skill Guides
nav_order: 37
lang_peer: /ja/skills/india-position-sizer/
permalink: /en/skills/india-position-sizer/
generated: true
---

# India Position Sizer
{: .no_toc }

Calculate risk-based position sizes for long NSE (India) delivery trades in rupees, with whole shares, SME lot sizes, tick-rounded ATR stops, and Indian delivery charges (STT, exchange fee, SEBI fee, stamp duty, GST, DP charge) counted inside the risk budget. Use when the user asks how many shares of an Indian stock to buy, wants to size an NSE trade, or hands off an india-* screener candidate with entry and stop.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-position-sizer.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-position-sizer){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Size a long NSE cash-market (delivery / CNC) trade so a stop-out loses no more than the
planned share of the account *after* charges. Rupees throughout, Indian digit grouping
(₹12,34,567.00), whole shares only. Pure calculation: no API key, no network.

Methods:

- **Fixed fractional** (`--stop`): risk a fixed percentage per trade (commonly 1%).
- **ATR-based** (`--atr`): stop = entry - ATR x multiplier, rounded down to the tick.
- **Kelly budget** (`--win-rate`, `--avg-win`, `--avg-loss`): half-Kelly risk budget only.

Constraints: max position % and max sector % (remaining room), plus `--lot-size` for SME
stocks that trade in fixed lots.

---

## 2. When to Use

- The user asks how many shares of an Indian stock to buy for a given entry and stop.
- A candidate from `india-vcp-screener` or `india-stockbee-momentum-burst-screener` needs
  sizing before manual order entry.
- The user wants to see how STT and other charges change a tight-stop trade.

---

## 3. Prerequisites

- Python 3.9+ (standard library only).
- Inputs in rupees: account size, entry, stop (or ATR), risk %.

---

## 4. Quick Start

```bash
# Fixed fractional with charges (default)
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 1000000 --entry 1000 --stop 950 --risk-pct 1.0 \
  --output-dir reports/

# ATR-based stop with portfolio limits
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 1000000 --entry 2450 --atr 48.5 --atr-multiplier 2 --risk-pct 1.0 \
  --max-position-pct 10 --max-sector-pct 30 --current-sector-exposure 18 \
  --sector "Information Technology" --output-dir reports/

# SME stock traded in lots of 1,200
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 2000000 --entry 112 --stop 104 --risk-pct 1.0 --lot-size 1200 \
  --output-dir reports/

# Kelly budget (no entry)
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 1000000 --win-rate 0.55 --avg-win 2.5 --avg-loss 1.0 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Gather trade parameters

Collect account size (₹), entry, stop or ATR, risk % per trade, and optional limits
(max position %, max sector %, current sector exposure %, lot size for SME stocks,
broker brokerage per order and DP charge).

### Step 2: Run the sizer

```bash
# Fixed fractional with charges (default)
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 1000000 --entry 1000 --stop 950 --risk-pct 1.0 \
  --output-dir reports/

# ATR-based stop with portfolio limits
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 1000000 --entry 2450 --atr 48.5 --atr-multiplier 2 --risk-pct 1.0 \
  --max-position-pct 10 --max-sector-pct 30 --current-sector-exposure 18 \
  --sector "Information Technology" --output-dir reports/

# SME stock traded in lots of 1,200
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 2000000 --entry 112 --stop 104 --risk-pct 1.0 --lot-size 1200 \
  --output-dir reports/

# Kelly budget (no entry)
python3 skills/india-position-sizer/scripts/india_position_sizer.py \
  --account-size 1000000 --win-rate 0.55 --avg-win 2.5 --avg-loss 1.0 --output-dir reports/
```

Add `--no-costs` only to compare against a charge-free calculation.

### Step 3: Explain the result

Load `references/india_costs_and_sizing.md` when the user asks why the share count is
below the charge-free figure, how charges are computed, or how SME lots, tick sizes and
circuits affect the plan. Report shares, position value, price risk, charges at the stop
and total risk as a percentage of the account. Name the binding constraint if one applies.

### Step 4: Flag Indian execution risks

- A stock locked at its lower circuit cannot be sold at the stop; mention gap risk for
  stocks with 5% or 10% bands.
- Delivery settles T+1; check cash for overlapping trades.
- Charges are estimates from statutory rates; the user's contract note is the authority.

---

## 6. Resources

**References:**

- `skills/india-position-sizer/references/india_costs_and_sizing.md`

**Scripts:**

- `skills/india-position-sizer/scripts/india_position_sizer.py`
