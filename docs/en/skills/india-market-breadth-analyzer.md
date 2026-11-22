---
layout: default
title: "India Market Breadth Analyzer"
grand_parent: English
parent: Skill Guides
nav_order: 45
lang_peer: /ja/skills/india-market-breadth-analyzer/
permalink: /en/skills/india-market-breadth-analyzer/
generated: true
---

# India Market Breadth Analyzer
{: .no_toc }

Measure Indian market breadth from NIFTY 500 constituents on free official NSE data — percent of stocks above their 20/50/200-day averages, net new 52-week highs, advance/decline and a McClellan oscillator — and score it 0-100 (Strong / Healthy / Neutral / Weak / Critical) with deterioration and washout flags. Use when the user asks about Indian market breadth, participation, how many NSE stocks are in uptrends, or needs the breadth input for india-exposure-coach. No API key required.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-market-breadth-analyzer.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-market-breadth-analyzer){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Compute daily breadth for a Nifty universe (default NIFTY 500) from constituent prices
and score the latest session on participation, momentum, breadth trend, new highs versus
lows and the McClellan oscillator. This is an India-specific model, not a port of the US
TraderMonty-based analyzer (see `references/india_breadth_methodology.md` for why).

---

## 2. When to Use

- The user asks how broad the Indian market move is, or how many NSE stocks are healthy.
- Daily regime check before NSE swing entries.
- As the breadth input to `india-exposure-coach`.

---

## 3. Prerequisites

- Python 3.9+ and `pip install -r skills/india-market-breadth-analyzer/requirements.txt`.
- Network access to NSE. No API key.
- Warm the cache once (about 80 minutes for NIFTY 500):
  `python3 skills/india-market-breadth-analyzer/scripts/_india_data.py update --universe nifty500 --start 2024-01-01`.
  Later runs fetch one session per stock.

---

## 4. Quick Start

```bash
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py --output-dir reports/

# Large caps only, or a past session
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py \
  --universe nifty200 --as-of 2026-10-23 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Run the analyzer

```bash
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py --output-dir reports/

# Large caps only, or a past session
python3 skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py \
  --universe nifty200 --as-of 2026-10-23 --output-dir reports/
```

### Step 2: Interpret

Load `references/india_breadth_methodology.md` for definitions, component scoring and
zones. Report the composite and zone, the five component inputs, the deterioration and
washout flags, coverage, and the percentile of today's %>200DMA within the available
history. Mention survivorship bias and the short history (2024 onward) when drawing
historical comparisons.

### Step 3: Feed the exposure decision

Pass the JSON to `india-exposure-coach --breadth`.

---

## 6. Resources

**References:**

- `skills/india-market-breadth-analyzer/references/india_breadth_methodology.md`

**Scripts:**

- `skills/india-market-breadth-analyzer/scripts/_india_data.py`
- `skills/india-market-breadth-analyzer/scripts/breadth_calculator.py`
- `skills/india-market-breadth-analyzer/scripts/india_breadth_analyzer.py`
