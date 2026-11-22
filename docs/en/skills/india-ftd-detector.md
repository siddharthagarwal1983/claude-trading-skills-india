---
layout: default
title: "India FTD Detector"
grand_parent: English
parent: Skill Guides
nav_order: 38
lang_peer: /ja/skills/india-ftd-detector/
permalink: /en/skills/india-ftd-detector/
generated: true
---

# India FTD Detector
{: .no_toc }

Detect Follow-Through Day (FTD) market-bottom confirmations on NSE indexes using William O'Neil's rally-attempt state machine, tracking NIFTY 50 with a second index (default NIFTY 500) on free official NSE data, with post-FTD health checks and a 0-100 quality score. Use when the user asks whether the Indian market has bottomed, about follow-through days or rally attempts on Nifty, or whether it is safe to increase NSE equity exposure after a correction. No API key required.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-ftd-detector.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-ftd-detector){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Track NIFTY 50 and a second NSE index through O'Neil's cycle: correction → swing low →
rally attempt (day 1) → follow-through day (day 4-7, strong gain on higher volume) →
post-FTD health (distribution, invalidation, power trend). Produces a combined state and
a quality score. Engine identical to the US `ftd-detector`; indexes and session handling
are Indian (see `references/india_market_notes.md`).

States: `NO_SIGNAL`, `CORRECTION`, `RALLY_ATTEMPT`, `FTD_WINDOW`, `FTD_CONFIRMED`,
`RALLY_FAILED`, `FTD_INVALIDATED`.

---

## 2. When to Use

- After a correction in Indian equities, to judge whether a bottom is confirmed.
- Before raising NSE exposure from defensive levels.
- As the bottom-confirmation input to `india-exposure-coach`.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-ftd-detector/requirements.txt`.
- Network access to NSE (`mcp.nseindia.in`). No API key.

---

## 4. Quick Start

```bash
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --output-dir reports/

# Mid caps as the second lens
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py \
  --secondary-index "NIFTY MIDCAP 150" --output-dir reports/

# A past session
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --as-of 2026-10-30 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Run the detector

```bash
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --output-dir reports/

# Mid caps as the second lens
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py \
  --secondary-index "NIFTY MIDCAP 150" --output-dir reports/

# A past session
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --as-of 2026-10-30 --output-dir reports/
```

### Step 2: Interpret

Load `references/ftd_methodology.md` for the state machine and scoring,
`references/post_ftd_guide.md` for health checks after a confirmation, and
`references/india_market_notes.md` for NSE specifics. Report the combined state, each
index's state, swing low, rally day count, FTD date and gain, and the quality score.
A `data_gap` means the state is unavailable, not bullish.

### Step 3: Feed the exposure decision

Pass the JSON to `india-exposure-coach --ftd`.

---

## 6. Resources

**References:**

- `skills/india-ftd-detector/references/ftd_methodology.md`
- `skills/india-ftd-detector/references/india_market_notes.md`
- `skills/india-ftd-detector/references/post_ftd_guide.md`

**Scripts:**

- `skills/india-ftd-detector/scripts/_india_data.py`
- `skills/india-ftd-detector/scripts/_price_bars.py`
- `skills/india-ftd-detector/scripts/ftd_lifecycle.py`
- `skills/india-ftd-detector/scripts/india_ftd_detector.py`
- `skills/india-ftd-detector/scripts/post_ftd_monitor.py`
- `skills/india-ftd-detector/scripts/rally_tracker.py`
- `skills/india-ftd-detector/scripts/report_generator.py`
