---
name: india-breakout-trade-planner
description: Turn india-vcp-screener (NSE) output into Minervini-style breakout trade plans in rupees — buy-stop trigger above the pivot, capped chase price, stop under the last contraction low and R-multiple target, all on NSE tick sizes — with whole-share sizing that counts Indian delivery charges inside the risk budget, turnover, cash, sector and portfolio-heat caps, and ready india-paper-ledger orders. Use when the user has India VCP screener results and wants entry/stop/target levels, position sizes, or paper orders for Indian breakout candidates.
---

# India Breakout Trade Planner

## Overview

Apply the US `breakout-trade-planner` Minervini gate to NSE candidates from
`india-vcp-screener`, size each plan in rupees, and emit an order the
`india-paper-ledger` can place directly. Pure calculation on a local JSON report;
no API key and no network (the NSE session calendar comes from
`pandas-market-calendars`).

India changes from the US planner:

- Prices sit on NSE cash-market tick bands (₹0.01 below ₹250, ₹0.05 to ₹1,000,
  ₹0.10 to ₹5,000, then ₹0.50 / ₹1 / ₹5). The trigger rounds up; the chase cap
  and stop round down.
- The risk budget covers the loss at the stop **plus** round-trip delivery charges
  (STT both legs, stamp duty, exchange, SEBI, GST, DP).
- Size is also capped at `--max-adv-pct` (default 1%) of average daily rupee
  turnover, and by available paper cash when a ledger report is given.
- Output is a STOP_LIMIT paper order and a broker-style NSE SL order (CNC). The US
  5-minute confirmation mode is not offered: the India data is end-of-day only.

## When to Use

- After `india-vcp-screener` produced a report and the user wants trade plans.
- The user asks for entry, stop, target or share count for an NSE VCP setup.
- The user wants the plan placed as paper orders in `india-paper-ledger`.

## Prerequisites

- An `india_vcp_screener_*.json` report (schema 1.0, `metadata.currency: INR`).
  US `vcp-screener` reports are refused — use `breakout-trade-planner` for those.
- Python 3.10+ with `pandas-market-calendars` (`requirements.txt`); in this
  repository run commands with `uv run`.
- Optional: an `india_paper_ledger_*.json` report for equity, cash, open risk
  and held symbols.

## Workflow

### Step 1: Check the market posture

Run `india-exposure-coach` first. When it returns `REDUCE_ONLY` or
`CASH_PRIORITY`, present plans as a watchlist only and say why.

### Step 2: Generate plans

Prefer the paper ledger report so heat, cash and held names are current:

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py status
uv run python skills/india-breakout-trade-planner/scripts/plan_india_breakout_trades.py \
  --input reports/india_vcp_screener_2026-11-20_200000.json \
  --ledger-report reports/india_paper_ledger_2026-11-20.json \
  --output-dir reports/
```

Without a ledger, pass `--account-size <rupees>`. Key options: `--risk-pct` (0.5),
`--max-portfolio-heat-pct` (6), `--max-position-pct` (10), `--max-sector-pct` (30),
`--max-chase-pct` (2), `--stop-buffer-pct` (1), `--target-r-multiple` (2),
`--max-adv-pct` (1), `--valid-sessions` (1), `--brokerage-per-order`, `--dp-charge`,
`--as-of` (date = that evening 20:00 IST, or an ISO time with offset).

### Step 3: Review and present

Read the Markdown report. Present, in order:

1. **Warnings** — a `STALE_SCREENER` warning means the screener ran on an older
   session: re-run it before acting.
2. **Actionable orders** — trigger, cap, stop, target, shares, rupee risk with
   charges, binding cap, and any price-band warning.
3. **Revalidation** — stocks already above the pivot: no order; confirm the next
   session holds above the pivot and never pay above the cap.
4. **Watchlist** — developing patterns (score 60-69) with their alert level.
5. **Not planned** — skipped (held), deferred (heat), constrained, rejected, with reasons.

Explain that sizing uses the capped (worst) entry, so a fill at the trigger risks less.

### Step 4: Place paper orders (on request)

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py place-plan \
  --plan reports/india_breakout_trade_plan_2026-11-20_200000.json [--symbols BHEL]
```

`place-plan` refuses a plan whose session has already opened and skips symbols that
already have an open paper order or position. Each actionable order also carries a
one-line `paper_command` and a `broker_order` for manual entry with a broker.

## Minervini Gate

| Condition | Pre-breakout | Breakout |
|-----------|-------------|----------|
| valid_vcp | True | True |
| rating band (score) | good ≥70 / strong ≥80 / textbook ≥90 | same |
| risk from worst entry to stop | ≤ 8% | ≤ 8% |
| breakout volume | — | detected |
| distance above pivot | — | ≤ max chase |
| last close | — | ≤ worst entry |

Risk per trade = base risk × band multiplier (good 0.75, strong 1.0, textbook 1.75).
Candidates are planned in score order; a plan that would push total open risk past
the heat cap is deferred.

## Output Format

- `reports/india_breakout_trade_plan_YYYY-MM-DD_HHMMSS.json` — `actionable_orders[]`
  (`trade_plan`, `paper_order`, `paper_command`, `broker_order`, `warnings`),
  `revalidation`, `watchlist`, `rejected`, `deferred`, `constrained`, `skipped`,
  `summary`, `warnings`, `plan_valid_for_session`.
- `reports/india_breakout_trade_plan_YYYY-MM-DD_HHMMSS.md` — human-readable plan.

## Limitations

- NSE reviews tick bands monthly from past closes; a stock near a band boundary can
  carry the neighbouring tick. If a broker rejects a price, re-round it there.
- Daily price bands (2/5/10/20%) are not known from bhavcopy data; a trigger more
  than 5% above the last close is flagged.
- Plans are educational; not investment advice.

## Resources

- `scripts/plan_india_breakout_trades.py` — CLI, gate, ledger link, reports.
- `scripts/india_breakout_risk.py` — tick-rounded prices, risk, rupee sizing with charges.
- `scripts/_india_data.py` — vendored NSE module (sessions, tick sizes, delivery charges).
- `references/india_breakout_rules.md` — entry, stop, sizing and order rules with a worked example.
