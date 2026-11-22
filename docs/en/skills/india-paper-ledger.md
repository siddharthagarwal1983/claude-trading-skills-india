---
layout: default
title: "India Paper Ledger"
grand_parent: English
parent: Skill Guides
nav_order: 46
lang_peer: /ja/skills/india-paper-ledger/
permalink: /en/skills/india-paper-ledger/
generated: true
---

# India Paper Ledger
{: .no_toc }

Paper-trade NSE (India) delivery swing trades in rupees. Place paper entry orders (close, limit, stop, stop-limit) with a stop and target, fill them from official NSE end-of-day bars with Indian delivery charges (STT, stamp duty, exchange, SEBI, GST, DP), run stop/target exits, credit dividends, and keep every position as a trader-memory-core thesis. Use when the user wants to paper trade Indian stocks, record a paper buy or sell of an NSE stock, check their paper portfolio or P&L in rupees, or turn an india-* screener candidate into a tracked paper position.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-paper-ledger.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-paper-ledger){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

End-of-day paper trading for NSE cash-market delivery (CNC) longs. Orders are placed in
the evening or before the open; after each session's bhavcopy is final (19:00 IST) the
`process` step fills them from the official NSE bar, applies exits, and writes a rupee
report. No broker, no API key: prices come from the free NSE bhavcopy MCP over HTTP
through the shared India data cache.

Every entry order is a trader-memory-core thesis (IDEA → ENTRY_READY on placement,
ACTIVE on fill, PARTIALLY_CLOSED / CLOSED on exits, INVALIDATED if the order expires,
is cancelled or is rejected). Fill prices given to the thesis store include charges, so
its realized P&L is net in rupees and downstream journal skills read true results.

---

## 2. When to Use

- The user wants to paper trade an Indian stock or practise a swing setup without money.
- A candidate from `india-vcp-screener`, `india-stockbee-momentum-burst-screener` or
  `india-stock-analysis` needs a paper entry with a stop.
- An `india-position-sizer` report should become a paper order.
- The user asks for paper positions, cash, open risk or P&L in rupees.

---

## 3. Prerequisites

- Python 3.10+ with `skills/india-paper-ledger/requirements.txt` (jsonschema, PyYAML,
  pandas-market-calendars). In this repository run commands with `uv run`.
- The `trader-memory-core` skill installed next to this skill (it is imported, not
  copied). For another location set `INDIA_PAPER_TRADER_MEMORY_DIR` to its `scripts/`.
- Network access to the free NSE bhavcopy MCP for prices (bars are cached in
  `.cache/india-data/`). No metered DalalOS or Drishti calls are made.

---

## 4. Quick Start

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py init \
  --capital 1000000 --brokerage-per-order 0 --slippage-bps 5
```

---

## 5. Workflow

Run every command from the repository root. `--state-dir` defaults to
`state/india-paper` (gitignored); reports go to `reports/`.

### 1. Create the paper account (once)

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py init \
  --capital 1000000 --brokerage-per-order 0 --slippage-bps 5
```

`--dp-charge` defaults to ₹15.93 per sell (typical discount broker). Ask the user for
their broker's brokerage and DP charge if they want contract-note accuracy.

### 2. Place entry orders

Every entry needs a protective `--stop`. Choose the order type from the setup:

```bash
# Next eligible closing price (default type). Placed after 15:00 IST -> next session.
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py buy \
  --symbol RELIANCE --qty 40 --stop 1250 --target 1500 --thesis "Base breakout above 1300"

# Breakout buy-stop from a VCP pivot, good for 10 sessions
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py buy \
  --symbol "M&M" --type stop --trigger 2950 --stop 2800 --valid-sessions 10

# From an india-position-sizer report (qty, entry and stop taken from the report)
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py buy \
  --symbol TCS --type limit --sizer-report reports/india_position_sizer_2026-11-22_201500.json
```

From an `india-breakout-trade-planner` plan, place every actionable order at once
(or `--symbols` to choose). Orders whose planned session has already opened are
refused, and symbols with an open paper order or position are skipped:

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py place-plan \
  --plan reports/india_breakout_trade_plan_2026-11-20_200000.json
```

- `--type`: `close`, `limit`, `stop`, `stop-limit` (`--limit` is the cap). Default
  validity is 1 session for `close` and 10 for the others (`--valid-sessions`).
- `--stop-basis close` exits only on a close below the stop (intraday wicks ignored).
- `--thesis-id` attaches the order to an existing IDEA/ENTRY_READY thesis (for
  example one registered from a screener via trader-memory-core ingest).
- `--placed-at 2026-11-20T20:00+05:30` backdates placement to replay a past plan.

Quote NSE symbols with `&` (`"M&M"`). The thesis ticker is an alphanumeric label
(`MANDM`, `BAJAJAUTO`) because trader-memory-core ids reject `&` and `-`; the real
symbol is kept in the order and in the thesis provenance.

### 3. Process after the close (daily, after 19:00 IST)

```bash
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py process
```

Evaluates every session since the last run up to the latest completed NSE session
(`--as-of` to pin one). Each session runs exits first, then manual sells, then buys
(sale proceeds fund same-day buys). Re-running is safe: processed sessions are skipped.
Writes `reports/india_paper_ledger_<as_of>.md` and `.json` (`--no-report` to skip).

### 4. Manage positions

```bash
# Raise the stop / set a target (thesis exit levels are updated too)
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py set-exit \
  --thesis-id th_reliance_pvt_20261102_af64 --stop 1320 --target 1520
# Sell all, or --qty to trim, at the next eligible close
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py sell \
  --thesis-id th_reliance_pvt_20261102_af64 --qty 20
# Cancel an open order; list orders
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py cancel --order-id po_...
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py orders --status OPEN
# Report without processing
uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py status
```

### 5. Present the result

Summarise equity, cash, return, open risk and each position's distance to its stop in
rupees with Indian digit grouping. Surface every warning (gap fills, circuit-locked
sessions, corporate-action blocks, entry-day stop touches) and say what the user must do.
State that results are paper fills from end-of-day data, not broker executions.

---

## 6. Resources

**References:**

- `skills/india-paper-ledger/references/fill_rules.md`

**Scripts:**

- `skills/india-paper-ledger/scripts/_india_data.py`
- `skills/india-paper-ledger/scripts/india_paper_ledger.py`
- `skills/india-paper-ledger/scripts/paper_engine.py`
- `skills/india-paper-ledger/scripts/paper_ledger.py`
