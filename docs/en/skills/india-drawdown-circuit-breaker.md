---
layout: default
title: "India Drawdown Circuit Breaker"
grand_parent: English
parent: Skill Guides
nav_order: 39
lang_peer: /ja/skills/india-drawdown-circuit-breaker/
permalink: /en/skills/india-drawdown-circuit-breaker/
generated: true
---

# India Drawdown Circuit Breaker
{: .no_toc }

Decide whether new NSE (India) trade risk is allowed today from realized rupee P&L in india-paper-ledger / trader-memory-core state — daily, weekly and monthly drawdown halts and a losing-streak cooldown, with days in Indian Standard Time and halts released on the next NSE trading session. Use before planning or placing new Indian trades, after a stop-out or losing streak, or when the user asks whether they should keep trading, have hit their loss limit, or need a break.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-drawdown-circuit-breaker.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-drawdown-circuit-breaker){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Account-level risk gate for Indian swing trading. Read the trader-memory-core thesis
files that `india-paper-ledger` writes, sum realized rupee P&L (net of delivery
charges) for today, the week and the month in IST, and return `TRADING_ALLOWED`,
`COOLDOWN` or `HALTED`. Offline: no API key and no network.

This is the India port of `drawdown-circuit-breaker`. The state validation and rules
are the same code; the differences are the clock and calendar:

- Day, week (Monday 00:00 IST) and month boundaries use Indian Standard Time.
- Halts release on the next NSE trading session (XBOM calendar), so NSE holidays
  such as Diwali are skipped and US holidays are not.
- Defaults point at `state/india-paper/theses`; the account size can come from an
  `india-paper-ledger` report; reports show rupees.

---

## 2. When to Use

- Before running `india-breakout-trade-planner` or placing new paper or real NSE orders.
- After a stop-out, a partial trim at a loss, or two losing trades in a row.
- When the user asks whether they have hit a loss limit or should pause trading.

---

## 3. Prerequisites

- Python 3.10+ with `requirements.txt` (PyYAML, pandas-market-calendars); in this
  repository run commands with `uv run`.
- trader-memory-core thesis YAML files, normally `state/india-paper/theses/`.
- Account size in rupees: `--account-size`, or `--ledger-report` with an
  `india_paper_ledger_*.json` report (its equity is used).

---

## 4. Quick Start

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py process
```

---

## 5. Workflow

### Step 1: Refresh the paper ledger

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py process
```

### Step 2: Evaluate the circuit breaker

```bash
uv run python skills/india-drawdown-circuit-breaker/scripts/check_india_circuit_breaker.py \
  --ledger-report reports/india_paper_ledger_2026-11-20.json \
  --output-dir reports/
```

Use `--account-size 1000000` instead of `--ledger-report` for a fixed capital base
(recommended for month-long limits: equity shrinks as losses accrue). Use `--as-of`
for a deterministic run: a date covers the whole IST day, a timestamp with offset is
an exact cutoff (`2026-11-20T20:00:00+05:30`).

### Step 3: Override thresholds when needed

```bash
uv run python skills/india-drawdown-circuit-breaker/scripts/check_india_circuit_breaker.py \
  --account-size 1000000 --max-daily-loss-pct 1.5 --losing-streak-n 3 \
  --cooldown-hours 48 --weekly-drawdown-pct 4 --monthly-drawdown-pct 6
```

Or pass `--config` with a JSON object of the same keys (`max_daily_loss_pct`,
`losing_streak_n`, `cooldown_hours`, `weekly_drawdown_pct`, `monthly_drawdown_pct`).
CLI flags override the file.

### Step 4: Interpret and present the decision

| Recommendation | Meaning |
|----------------|---------|
| TRADING_ALLOWED | No rule is active; new trade risk may proceed through the rest of the workflow |
| COOLDOWN | No new positions; manage existing ones and review the recent losses |
| HALTED | No new entries: a drawdown limit is active or the state data is incomplete |

State each triggered rule with its rupee amount, threshold and `active_until` (IST).
For `incomplete_state_data`, list the data warnings and say the halt lasts until the
state is repaired and the check is rerun. The decision never closes positions.

---

## 6. Resources

**References:**

- `skills/india-drawdown-circuit-breaker/references/india_circuit_breaker_framework.md`

**Scripts:**

- `skills/india-drawdown-circuit-breaker/scripts/_india_data.py`
- `skills/india-drawdown-circuit-breaker/scripts/check_india_circuit_breaker.py`
