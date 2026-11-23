---
name: india-weekly-performance-digest
description: Generate a weekly performance digest of NSE paper trades closed in india-paper-ledger state — win rate, expectancy, profit factor, R-multiple and pattern buckets computed as weekly-performance-digest does, plus rupee charges and gross-vs-net P&L, dividends, NIFTY 50 comparison per trade and for the week, MAE/MFE from official NSE closes, and the screener behind each trade. IST weeks and NSE sessions; free NSE data only. Use for an end-of-week review of Indian paper trading.
---

# India Weekly Performance Digest

## Overview

Summarise the trades closed during one NSE week from the `india-paper-ledger`
state (`state/india-paper/`). Headline metrics — win rate, expectancy, profit
factor, R-multiple, MAE/MFE averages, pattern buckets, top winners/losers — use
functions copied unchanged from `weekly-performance-digest`
(`scripts/digest_core.py`, guarded by a parity test). The India layer adds:

- **Rupees and IST.** Amounts use Indian digit grouping (₹10,17,329.06); a trade
  counts in the IST week of its exit date; weeks run Monday to Sunday and list
  their NSE sessions (holidays excluded).
- **Costs.** Raw fill prices and the charges on both legs come from the ledger
  journal, giving gross P&L, charges and net P&L per trade and a charges-drag
  line for the week.
- **Dividends.** The ledger credits dividends on the ex-date; thesis P&L leaves
  them out, so each trade shows them separately (`net_pnl_incl_dividends`).
- **Benchmark.** NIFTY 50 (or `--benchmark`) change for the week and over each
  trade's holding sessions, with the trade's excess return.
- **MAE/MFE.** trader-memory-core only computes them from FMP, so paper theses
  have none; the digest computes them from official NSE closes (entry to exit,
  against the charge-inclusive entry price), in the report only.
- **Signal source.** Paper theses always say `origin.skill: india-paper-ledger`.
  The digest follows `origin.output_file` (a breakout plan) to the screener report
  it was built from, so `by_source_skill` reads e.g. `india-vcp-screener`; orders
  placed by hand show as `manual`.
- **Realized equity.** Starting capital plus realized P&L and dividends before
  and after the week (open positions are not marked to market; use
  `india-paper-ledger status` for equity).

Thesis files are only read. The digest is descriptive, not investment advice.

## When to Use

- At the end of an NSE week to review closed paper trades
- To see whether trades beat simply holding NIFTY 50 over the same sessions
- To see how much of the gross profit went to STT and other charges
- To feed a month-end review or a signal postmortem

## When Not to Use

- For one trade in depth — use trader-memory-core's `postmortem`
- For screener hit rates, including signals that were never traded — use
  `india-signal-postmortem`
- For US trades — use `weekly-performance-digest`

## Prerequisites

- Python 3.9+ with `PyYAML` and `pandas-market-calendars` (`requirements.txt`)
- An `india-paper-ledger` state directory (or any trader-memory-core theses
  directory; without the ledger journal, charges and dividends are omitted)
- No API key. NSE end-of-day bars come free through the shared NSE bar cache
  (`.cache/india-data`); `--no-fetch` reads only the cache

## Workflow

### Step 1: Bring the ledger up to date

```bash
python3 skills/india-paper-ledger/scripts/india_paper_ledger.py process
```

### Step 2: Run the digest

```bash
# The week of the latest NSE session whose bhavcopy is final (after 19:00 IST)
python3 skills/india-weekly-performance-digest/scripts/india_weekly_digest.py \
  --state-dir state/india-paper --output-dir reports/

# A given week (any date inside it) or an explicit IST date range
python3 skills/india-weekly-performance-digest/scripts/india_weekly_digest.py --week-ending 2026-11-20
python3 skills/india-weekly-performance-digest/scripts/india_weekly_digest.py \
  --from-date 2026-11-01 --to-date 2026-11-30

# Offline: cached NSE bars only; a missing symbol leaves MAE/MFE blank
python3 skills/india-weekly-performance-digest/scripts/india_weekly_digest.py --no-fetch
```

The run writes `reports/india_weekly_digest_<to-date>.json` and `.md`. A week
that is still in progress is labelled with its last completed session.

### Step 3: Read the report

Lead with the executive summary and the closed-trades table (source, sessions
held, raw entry/exit, net P&L, R, NIFTY 50 over the same sessions, excess,
MAE/MFE, exit reason). Check **Costs and Income** for the charges drag and
**Process Improvements** for flagged patterns. Relay the **Data Notes** if any
bars were missing.

## Output Format

JSON keeps the US digest keys (`summary`, `metrics`, `pattern_analysis`,
`partial_trims`, `lessons`; `*_dollars` fields are rupees) and adds:

```json
{
  "report_type": "india_weekly_performance_digest",
  "exchange": "NSE", "currency": "INR", "timezone": "Asia/Kolkata",
  "period": {"from": "2026-11-16", "to": "2026-11-22", "session_count": 5,
             "last_session": "2026-11-20", "week_complete": true},
  "india": {
    "trades": [{"symbol": "RELIANCE", "signal_source": "manual", "holding_sessions": 10,
                "entry_price": 1300.0, "exit_price": 1365.0, "charges": 222.89,
                "gross_pnl": 4550.0, "net_pnl": 4327.11, "dividends": 0.0,
                "benchmark_pct": 8.3, "excess_pct": -3.55, "mae_pct": -0.12, "mfe_pct": 5.03}],
    "week_activity": {"buy_fills": 0, "sell_fills": 5, "charges_paid": 516.63,
                      "realized_pnl": 8972.36, "dividends": 0.0},
    "account": {"realized_equity_start": 1008356.7, "realized_equity_end": 1017329.06,
                "realized_return_pct": 0.89},
    "benchmark": {"index": "NIFTY 50", "change_pct": 2.61},
    "charges_drag": {"gross_pnl": 5067.4, "charges": 919.39, "net_pnl": 4148.01},
    "data_notes": []
  }
}
```

## Resources

- `scripts/india_weekly_digest.py` — CLI, India enrichment and Markdown report
- `scripts/digest_core.py` — metrics copied verbatim from weekly-performance-digest
- `scripts/_india_data.py` — vendored NSE data module (bar cache, sessions, IST)
- `references/india-weekly-digest-metrics.md` — metric definitions and caveats

## Key Principles

1. **Same math as the US digest** — only the inputs and presentation are Indian.
2. **No double counting** — headline totals use CLOSED theses; trims of open
   positions are informational, as in the US digest.
3. **Net of charges, dividends shown apart** — the thesis P&L is the paper
   ledger's charge-inclusive P&L; dividends are listed beside it.
4. **Read-only and free** — no thesis is changed; only free NSE data is used.
