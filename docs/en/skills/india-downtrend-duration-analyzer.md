---
layout: default
title: "India Downtrend Duration Analyzer"
grand_parent: English
parent: Skill Guides
nav_order: 37
lang_peer: /ja/skills/india-downtrend-duration-analyzer/
permalink: /en/skills/india-downtrend-duration-analyzer/
generated: true
---

# India Downtrend Duration Analyzer
{: .no_toc }

Analyze how long peak-to-trough declines in Indian NSE stocks typically last, grouped by NSE industry and by size tier (Nifty 100 / Midcap 150 / Smallcap 250 / Microcap 250), using free official NSE end-of-day data adjusted for bonuses and splits, and render an interactive HTML histogram. Use when the user asks how long corrections last in Indian stocks, typical downtrend duration by sector or market cap on NSE, or wants time-stop baselines for Nifty stocks. No API key required.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-downtrend-duration-analyzer.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-downtrend-duration-analyzer){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Detect local peaks and troughs in daily NSE closes, measure each decline of at least
`--min-depth` percent, and summarise durations (median, mean, P25/P75/P90) overall, by NSE
industry and by size tier. Method identical to the US `downtrend-duration-analyzer`; data
and grouping are Indian (see `references/india_market_notes.md`).

---

## 2. When to Use

- The user asks how long corrections typically last for Indian stocks or a Nifty sector.
- Setting time-based stops or patience thresholds for NSE swing trades.
- Comparing large-cap versus small-cap recovery behaviour on NSE.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-downtrend-duration-analyzer/requirements.txt`
  (numpy, pandas, pandas-market-calendars).
- Network access to NSE, no API key. The first NIFTY 500 run downloads history since
  2024 for every stock (allow about an hour); reruns use the cache in `./.cache/india-data/`.

---

## 4. Quick Start

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

---

## 5. Workflow

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

---

## 6. Resources

**References:**

- `skills/india-downtrend-duration-analyzer/references/downtrend_methodology.md`
- `skills/india-downtrend-duration-analyzer/references/india_market_notes.md`

**Scripts:**

- `skills/india-downtrend-duration-analyzer/scripts/_india_data.py`
- `skills/india-downtrend-duration-analyzer/scripts/analyze_india_downtrends.py`
- `skills/india-downtrend-duration-analyzer/scripts/generate_india_histogram_html.py`
