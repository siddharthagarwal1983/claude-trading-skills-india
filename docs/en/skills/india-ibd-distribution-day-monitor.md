---
layout: default
title: "India Ibd Distribution Day Monitor"
grand_parent: English
parent: Skill Guides
nav_order: 42
lang_peer: /ja/skills/india-ibd-distribution-day-monitor/
permalink: /en/skills/india-ibd-distribution-day-monitor/
generated: true
---

# India Ibd Distribution Day Monitor
{: .no_toc }

Detect IBD-style Distribution Days on NSE indexes (NIFTY 50 and NIFTY 500 by default) from free official NSE data with real index volume, track 25-session expiry and 5% invalidation, count d5/d15/d25 clusters, classify market risk (NORMAL/CAUTION/HIGH/SEVERE), and recommend equity exposure for an NSE swing book or a NIFTY index ETF. Use after the Indian market close, before adding NSE swing positions, or as input to india-exposure-coach. No API key required.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-ibd-distribution-day-monitor.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-ibd-distribution-day-monitor){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Count institutional selling days on NSE indexes: a close down at least 0.2% on higher
volume than the prior session. Distribution days expire after 25 sessions or once the
index rallies 5% above the day's close. Clusters within 5, 15 and 25 sessions set a risk
level, which maps to an exposure target. Method identical to the US
`ibd-distribution-day-monitor`; data, sessions and exposure policy are Indian (see
`references/india_market_notes.md`).

---

## 2. When to Use

- After 19:00 IST, to check whether institutions are distributing NSE stocks.
- Before adding new swing positions in Indian stocks.
- As the distribution input to `india-exposure-coach`.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-ibd-distribution-day-monitor/requirements.txt`.
- Network access to NSE (`mcp.nseindia.in`). No API key.

---

## 4. Quick Start

```bash
# Default: NIFTY 50 + NIFTY 500, exposure policy for an NSE stock swing book
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py --output-dir reports/

# 1x index ETF holder at 80% exposure
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py \
  --instrument NIFTY_ETF --current-exposure 80 --output-dir reports/

# Other indexes, or a past session (holidays snap to the previous session)
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py \
  --symbols "NIFTY 50,NIFTY MIDCAP 150" --as-of 2026-10-23 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Run the monitor

```bash
# Default: NIFTY 50 + NIFTY 500, exposure policy for an NSE stock swing book
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py --output-dir reports/

# 1x index ETF holder at 80% exposure
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py \
  --instrument NIFTY_ETF --current-exposure 80 --output-dir reports/

# Other indexes, or a past session (holidays snap to the previous session)
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py \
  --symbols "NIFTY 50,NIFTY MIDCAP 150" --as-of 2026-10-23 --output-dir reports/
```

### Step 2: Interpret

Load `references/ibd_distribution_methodology.md` for the rule details and
`references/india_market_notes.md` for NSE specifics (Muhurat removal, expiry and
rebalancing days, peer risk combination, exposure table). Report per-index d5/d15/d25,
the overall level, the portfolio action and any audit flags.

### Step 3: Feed the exposure decision

Pass the JSON to `india-exposure-coach --distribution`. A SEVERE level forces
CASH_PRIORITY there.

---

## 6. Resources

**References:**

- `skills/india-ibd-distribution-day-monitor/references/ibd_distribution_methodology.md`
- `skills/india-ibd-distribution-day-monitor/references/india_market_notes.md`

**Scripts:**

- `skills/india-ibd-distribution-day-monitor/scripts/_india_data.py`
- `skills/india-ibd-distribution-day-monitor/scripts/data_loader.py`
- `skills/india-ibd-distribution-day-monitor/scripts/distribution_day_tracker.py`
- `skills/india-ibd-distribution-day-monitor/scripts/exposure_policy.py`
- `skills/india-ibd-distribution-day-monitor/scripts/history_utils.py`
- `skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py`
- `skills/india-ibd-distribution-day-monitor/scripts/math_utils.py`
- `skills/india-ibd-distribution-day-monitor/scripts/models.py`
- `skills/india-ibd-distribution-day-monitor/scripts/report_generator.py`
- `skills/india-ibd-distribution-day-monitor/scripts/risk_classifier.py`
