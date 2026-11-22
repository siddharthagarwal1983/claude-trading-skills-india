---
layout: default
title: "India Exposure Coach"
grand_parent: English
parent: Skill Guides
nav_order: 41
lang_peer: /ja/skills/india-exposure-coach/
permalink: /en/skills/india-exposure-coach/
generated: true
---

# India Exposure Coach
{: .no_toc }

Produce a one-page Indian market posture — equity exposure ceiling and NEW_ENTRY_ALLOWED / REDUCE_ONLY / CASH_PRIORITY — by combining india-market-breadth-analyzer, india-ibd-distribution-day-monitor and india-ftd-detector reports with NIFTY 50 trend, INDIA VIX, NIFTY P/E percentile and optional FII/DII flows. Use when the user asks how much to have invested in Indian stocks, whether new NSE swing entries are allowed today, or for a daily India market regime decision. No API key required.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-exposure-coach.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-exposure-coach){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Synthesize the India regime skills and free NSE market context into one decision for an
NSE swing book: an exposure ceiling (0-100%), an action, a participation read and a
confidence level, with every component and missing or stale input shown. Weights and
mappings are in `references/india_exposure_framework.md`.

---

## 2. When to Use

- Daily, after the Indian close, to decide whether new swing entries are allowed.
- When the user asks how much of the account should be in NSE equities right now.
- Before running India screeners, as the market gate.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-exposure-coach/requirements.txt`.
- Network access to NSE for NIFTY 50 and INDIA VIX context (or `--no-fetch`).
- Same-day reports from the three India regime skills for full confidence.

---

## 4. Quick Start

```bash
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py --output-dir reports/
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py --output-dir reports/
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --output-dir reports/
```

---

## 5. Workflow

### Step 1: Produce the inputs

```bash
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py --output-dir reports/
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py --output-dir reports/
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --output-dir reports/
```

Optional: save FII/DII daily flows to a JSON file (format in the framework reference).

### Step 2: Run the coach

```bash
python3 skills/india-exposure-coach/scripts/calculate_india_exposure.py \
  --breadth "reports/india_market_breadth_*.json" \
  --distribution "reports/india_ibd_distribution_day_monitor_*.json" \
  --ftd "reports/india_ftd_detector_*.json" \
  --flows reports/fii_dii_flows.json \
  --output-dir reports/
```

Globs pick the newest matching file. Inputs older than `--max-age-sessions` (default 2)
are ignored and reported as stale.

### Step 3: Report the posture

Load `references/india_exposure_framework.md` when explaining scores. State the action,
exposure ceiling, participation and confidence, then the components. Name stale or missing
inputs explicitly; with two critical inputs missing the action cannot be better than
REDUCE_ONLY.

### Step 4: Apply it downstream

- Map the action to `india-stockbee-momentum-burst-screener --market-gate`:
  NEW_ENTRY_ALLOWED → `allowed`, REDUCE_ONLY → `restrictive`; under CASH_PRIORITY skip new
  entries entirely.
- Act on `india-vcp-screener` candidates only when the action is NEW_ENTRY_ALLOWED.
- Treat the ceiling as the cap on total exposure when sizing with `india-position-sizer`.

---

## 6. Resources

**References:**

- `skills/india-exposure-coach/references/india_exposure_framework.md`

**Scripts:**

- `skills/india-exposure-coach/scripts/_india_data.py`
- `skills/india-exposure-coach/scripts/calculate_india_exposure.py`
