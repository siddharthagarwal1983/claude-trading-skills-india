---
name: india-pre-trade-discipline-gate
description: Run a pre-trade discipline checklist before placing NSE (India) orders — from an india-breakout-trade-planner plan or a checklist file — blocking planless, oversized, revenge, stale-plan, duplicate and regime- or circuit-breaker-blocked entries, and flagging NSE results due within a few sessions, with every decision journaled. Use right before placing paper or real orders for Indian stocks, after a recent loss, or when the user asks whether a planned Indian trade passes their rules.
---

# India Pre-Trade Discipline Gate

## Overview

Decide `GO` / `REVIEW_REQUIRED` / `NO_GO` / `NO_ACTIONABLE_ORDERS` for planned NSE
orders before they are placed. Offline: no broker call and no network.

The checklist rules, revenge-trade window, approved operating rules, journal and
thesis links come from the US `pre-trade-discipline-gate`, imported from the
sibling skill (not copied). This skill adds the India layer:

| Check | Decision when it fails |
|-------|------------------------|
| Plan built from `india-breakout-trade-planner`; order risk recomputed from qty, cap, stop and delivery charges must not exceed the planned rupee risk | NO_GO |
| Order quantity equals the planned shares; a stop exists below the trigger | NO_GO |
| The plan's NSE session has not already opened | NO_GO |
| Symbol not already open (position or order) in `india-paper-ledger` | NO_GO |
| No NSE results due within `--results-buffer-sessions` (default 5) | REVIEW_REQUIRED unless `--accept-results-risk SYMBOL` |
| Market-regime report is from `india-exposure-coach` and current | REVIEW_REQUIRED |
| Circuit-breaker report is from `india-drawdown-circuit-breaker` and from today (IST) | REVIEW_REQUIRED |

US engine rules that still apply: `REDUCE_ONLY` / `CASH_PRIORITY` posture → NO_GO;
`COOLDOWN` / `HALTED` circuit breaker → NO_GO; a losing exit or trim inside the
revenge window (default 24 h) → NO_GO; missing upstream reports → REVIEW_REQUIRED.

## When to Use

- Immediately before `india-paper-ledger place-plan` or entering an order at a broker.
- After a stop-out, to block a revenge trade.
- When the user asks whether a planned Indian trade passes their rules.

## Prerequisites

- The `pre-trade-discipline-gate` and `trader-memory-core` skills installed next to this
  skill (or `INDIA_PRE_TRADE_US_GATE_DIR` pointing at the US gate's `scripts/`).
- Python 3.10+ with `requirements.txt` (PyYAML, pandas-market-calendars); in this
  repository run commands with `uv run`.
- Today's `india_exposure_posture_*.json` and `india_circuit_breaker_decision_*.json`.
- Optional: the `india-earnings-calendar` archive (`.cache/india-data/results_calendar.json`).

## Workflow

### Step 1: Produce the upstream decisions

Run `india-exposure-coach` (after the breadth, distribution-day and FTD skills),
`india-drawdown-circuit-breaker`, and `india-breakout-trade-planner`. Refresh the
results archive with `india-earnings-calendar` at least weekly.

### Step 2: Run the gate

```bash
uv run python skills/india-pre-trade-discipline-gate/scripts/check_india_pre_trade.py \
  --plan reports/india_breakout_trade_plan_2026-11-22_210000.json \
  --exposure-decision reports/india_exposure_posture_2026-11-22_204500.json \
  --circuit-breaker-decision reports/india_circuit_breaker_decision_2026-11-22_205000.json \
  --output-dir reports/
```

Defaults: `--state-dir state/india-paper/theses` (revenge window, thesis links),
`--ledger-state-dir state/india-paper` (duplicates), `--journal-dir
state/journal/india-pre-trade-discipline`. `--symbols` limits the plan to some names.

For trades not from a plan, pass `--answers-file` in the US format (see the US skill);
`planned_risk_inr` / `actual_risk_inr` are accepted as names for the risk fields.

### Step 3: Act on the decision

| Decision | Action |
|----------|--------|
| GO | Place the orders (`india-paper-ledger place-plan --plan ...`, or at the broker) |
| REVIEW_REQUIRED | Do not place yet. Resolve each reason: rerun a stale or wrong-market report, or decide on results risk and rerun with `--accept-results-risk SYMBOL` |
| NO_GO | Do not place. Report every reason |
| NO_ACTIONABLE_ORDERS | Nothing to place |

Present each candidate's reasons and the India checks table (planned vs order risk in
rupees, plan session, results date, paper-ledger state). Accept results risk only when
the user says so explicitly. `--fail-on-non-go` makes the CLI exit 2 for any non-GO
decision (for scripts).

## Output Format

- `reports/india_pre_trade_decision_YYYY-MM-DD_HHMMSS.json` — the US decision shape
  (`overall_decision`, `candidate_results[]` with `checklist_answers` and `reasons`,
  `metrics`, `inputs`, `warnings`, `rationale`) plus `india_checks` per symbol and
  `skill`, `exchange`, `currency`, `timezone`.
- A matching `.md` (unless `--json-only`) and one JSONL row per run in the journal dir.

## Notes

- Risk fields keep the US names (`planned_risk_dollars`, `actual_risk_dollars`); the
  amounts are rupees.
- The optional approved-rules ledger (`--approved-rules-ledger`) uses the US skill's
  dates, which are New York calendar dates; near midnight IST that can be a day off.
- Not investment advice.

## Resources

- `scripts/check_india_pre_trade.py` — CLI, plan → checklist builder, India checks.
- `scripts/_india_data.py` — vendored NSE module (IST, sessions, delivery charges).
- `references/india_pre_trade_checks.md` — each India check, its inputs and its rationale.
