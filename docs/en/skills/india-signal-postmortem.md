---
layout: default
title: "India Signal Postmortem"
grand_parent: English
parent: Skill Guides
nav_order: 49
lang_peer: /ja/skills/india-signal-postmortem/
permalink: /en/skills/india-signal-postmortem/
generated: true
---

# India Signal Postmortem
{: .no_toc }

Score India screener signals (india-vcp-screener, india-stockbee-momentum-burst-screener, india-earnings-trade-analyzer, india-dividend-screener) against what NSE prices did 5 and 20 sessions later, whether or not they were traded. Classifies outcomes with the signal-postmortem rules, adds excess return over NIFTY 50, the india-exposure-coach regime, grade breakdowns, winners never taken by the paper ledger, and advisory weight feedback. Free official NSE end-of-day data only. Use for weekly or monthly screener quality audits.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-signal-postmortem.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-signal-postmortem){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Measure how good each India screener's calls were. `record` reads screener JSON
reports, takes each listed stock as a LONG signal on the report's session, and
measures the close-to-close return 5 and 20 **NSE sessions** later from official
bhavcopy closes (adjusted for splits and bonuses). It compares each return with
NIFTY 50 over the same sessions, tags the india-exposure-coach regime at the
signal and at the 5-session exit, marks whether the india-paper-ledger took the
trade, and writes one `pm_<signal_id>.json` record per signal. `summary`
aggregates the records per screener, grade and regime.

Outcome classes and feedback come from `signal-postmortem`, copied unchanged
into `scripts/postmortem_core.py` (a parity test guards the copy):
TRUE_POSITIVE, FALSE_POSITIVE, FALSE_POSITIVE_SEVERE (wrong way by more than
2%), REGIME_MISMATCH (lost more than 2% after the regime changed) and NEUTRAL
(within ±0.5%). India records add `excess_outcome_category`, the same rules
applied to the return over NIFTY 50: in a strong market most longs "work", so
the excess view shows whether the screener added anything over the index.

---

## 2. When to Use

- Weekly or monthly, to audit which India screener (and which grade) is worth acting on
- After a run of losing paper trades, to separate bad signals from bad execution
- Before trusting a screener in a new market regime
- To list good signals that were never traded

---

## 3. Prerequisites

- Python 3.9+ with `pandas-market-calendars` (`requirements.txt`); no API key
- Saved India screener JSON reports in `reports/` (they are written there by default)
- Optional: `india_exposure_posture_*.json` reports for regimes and a
  `state/india-paper/` ledger for the taken flag

---

## 4. Quick Start

```bash
python3 skills/india-signal-postmortem/scripts/india_signal_postmortem.py record \
  --reports-dir reports/ --postmortems-dir reports/india-postmortems --output-dir reports/
```

---

## 5. Workflow

### Step 1: Record outcomes

```bash
python3 skills/india-signal-postmortem/scripts/india_signal_postmortem.py record \
  --reports-dir reports/ --postmortems-dir reports/india-postmortems --output-dir reports/
```

- Scans `reports/` for the four screener report types (or pass `--reports FILE...`;
  a plain `{"signals": [...]}` file in signal-postmortem format also works).
- Ignores signals older than `--since` (default 90 days). The same stock, date
  and screener across several runs counts once.
- Signals with fewer than 5 later sessions are **pending** and are recorded on a
  later run; records missing the 20-session return are completed on re-runs.
  Complete records are never rewritten; `outcome_notes` added by hand are kept.
- `--no-fetch` uses only the shared NSE cache (`.cache/india-data`); otherwise
  missing bars are fetched free from the NSE bhavcopy server.
- Regimes come from `india_exposure_posture_*.json` in `--regime-dir` (default
  the reports dir); a report older than 7 days counts as UNKNOWN, and an UNKNOWN
  signal regime never produces REGIME_MISMATCH.

Momentum-burst candidates in state REJECTED are skipped. Every other listed
stock is a signal; use the grade breakdown to judge the lower grades separately.

### Step 2: Summarise

```bash
python3 skills/india-signal-postmortem/scripts/india_signal_postmortem.py summary \
  --postmortems-dir reports/india-postmortems --output-dir reports/
```

Writes `reports/india_signal_postmortem_summary_<date>.json` and `.md`:
accuracy, false-positive rate, average 5/20-session return and excess return
and the share of signals that beat NIFTY 50, per screener, per grade and per
regime; paper trades taken; the best winners that were not taken; advisory
weight changes (20+ signals per screener) and improvement-backlog items.

### Step 3: Act on it

Lead with the excess-return columns. A screener or grade that is accurate in
raw terms but negative in excess terms is mostly riding the market. Feed clear
findings back into screener thresholds or the pre-trade checklist by hand; the
weights are advisory and nothing is changed automatically.

---

## 6. Resources

**References:**

- `skills/india-signal-postmortem/references/india-signal-outcomes.md`

**Scripts:**

- `skills/india-signal-postmortem/scripts/_india_data.py`
- `skills/india-signal-postmortem/scripts/india_signal_postmortem.py`
- `skills/india-signal-postmortem/scripts/postmortem_core.py`
