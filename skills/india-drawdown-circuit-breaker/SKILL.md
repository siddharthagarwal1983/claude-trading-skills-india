---
name: india-drawdown-circuit-breaker
description: Decide whether new NSE (India) trade risk is allowed today from realized rupee P&L in india-paper-ledger / trader-memory-core state — daily, weekly and monthly drawdown halts and a losing-streak cooldown, with days in Indian Standard Time and halts released on the next NSE trading session. Use before planning or placing new Indian trades, after a stop-out or losing streak, or when the user asks whether they should keep trading, have hit their loss limit, or need a break.
---

# India Drawdown Circuit Breaker

## Overview

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

## When to Use

- Before running `india-breakout-trade-planner` or placing new paper or real NSE orders.
- After a stop-out, a partial trim at a loss, or two losing trades in a row.
- When the user asks whether they have hit a loss limit or should pause trading.

## Prerequisites

- Python 3.10+ with `requirements.txt` (PyYAML, pandas-market-calendars); in this
  repository run commands with `uv run`.
- trader-memory-core thesis YAML files, normally `state/india-paper/theses/`.
- Account size in rupees: `--account-size`, or `--ledger-report` with an
  `india_paper_ledger_*.json` report (its equity is used).

## Workflow

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

## Default Rules

| Rule | Default | State | Release |
|------|---------|-------|---------|
| Max daily loss | 2.0% of account | HALTED | 00:00 IST, next NSE session |
| Losing streak | 2 consecutive losing closes | COOLDOWN | 24 hours after the last loss exit |
| Weekly drawdown | 5.0% of account | HALTED | First NSE session of next week |
| Monthly drawdown | 8.0% of account | HALTED | First NSE session of next month |

Break-even closes count as wins. Empty or missing state returns `TRADING_ALLOWED`
with `data_quality: EMPTY_STATE`; malformed, duplicated or conflicting thesis data
fails closed with `PARTIAL` + `HALTED`. See
`references/india_circuit_breaker_framework.md` for every validation rule.

## Output Format

`reports/india_circuit_breaker_decision_YYYY-MM-DD_HHMMSS.json` (IST stamp) and a
matching `.md` unless `--json-only`. JSON keys: `recommendation`, `triggered_rules[]`
(`rule`, `threshold`, `observed`, `active_until`, `detail`), `metrics`
(`realized_pnl_today`, `realized_pnl_wtd`, `realized_pnl_mtd`, `consecutive_losses`,
`last_loss_exit_at`, `theses_scanned`), `account_size`, `config`, `data_quality`,
`warnings`, `rationale`, plus `exchange: NSE`, `currency: INR`, `timezone: Asia/Kolkata`.

## Key Principles

1. Realized damage only — unrealized P&L never triggers a rule.
2. Survival first — stop escalating risk after losses.
3. Advisory — the output gates the workflow; it places or cancels no orders.
4. Fail closed on incomplete state.

## Resources

- `scripts/check_india_circuit_breaker.py` — CLI and rule engine.
- `scripts/_india_data.py` — vendored NSE module (IST, NSE session calendar).
- `references/india_circuit_breaker_framework.md` — rules, calendar, data-quality contract.
