---
name: india-signal-postmortem
description: Score India screener signals (india-vcp-screener, india-stockbee-momentum-burst-screener, india-earnings-trade-analyzer, india-dividend-screener) against what NSE prices did 5 and 20 sessions later, whether or not they were traded. Classifies outcomes with the signal-postmortem rules, adds excess return over NIFTY 50, the india-exposure-coach regime, grade breakdowns, winners never taken by the paper ledger, and advisory weight feedback. Free official NSE end-of-day data only. Use for weekly or monthly screener quality audits.
---

# India Signal Postmortem

## Overview

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

## When to Use

- Weekly or monthly, to audit which India screener (and which grade) is worth acting on
- After a run of losing paper trades, to separate bad signals from bad execution
- Before trusting a screener in a new market regime
- To list good signals that were never traded

## When Not to Use

- For realized P&L of paper trades — use `india-weekly-performance-digest`
- For US signals — use `signal-postmortem`
- As a backtest: the sample is whatever reports exist, with survivorship and
  look-ahead limits of each screener

## Prerequisites

- Python 3.9+ with `pandas-market-calendars` (`requirements.txt`); no API key
- Saved India screener JSON reports in `reports/` (they are written there by default)
- Optional: `india_exposure_posture_*.json` reports for regimes and a
  `state/india-paper/` ledger for the taken flag

## Workflow

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

## Output Format

Each record keeps the signal-postmortem fields (`signal_id`, `ticker`,
`signal_date`, `source_skill`, `predicted_direction`, `entry_price`,
`realized_returns` {"5d", "20d"}, `exit_price`, `exit_date`, `holding_days`,
`outcome_category`, `regime_at_signal`, `regime_at_exit`, `outcome_notes`,
`recorded_at`) and adds:

```json
{
  "horizon_unit": "nse_sessions", "exchange": "NSE", "currency": "INR",
  "entry_session": "2026-10-23", "report_price": 997.1,
  "grade": "B", "score": 71.0, "setup_state": "MANUAL_REVIEW",
  "exits": {"5d": {"date": "2026-10-30", "close": 985.0}},
  "benchmark": "NIFTY 50", "benchmark_returns": {"5d": 0.020049},
  "excess_returns": {"5d": -0.032184}, "excess_outcome_category": "FALSE_POSITIVE_SEVERE",
  "mae_5d_pct": -3.15, "mfe_5d_pct": 3.07,
  "taken": false, "taken_order_id": null, "complete": false
}
```

`5d`/`20d` count NSE sessions, not calendar days. `taken` is `null` without a
ledger. Each `record` run also writes `india_signal_postmortem_batch_<stamp>.json`
(recorded, pending, duplicates, errors).

## Resources

- `scripts/india_signal_postmortem.py` — `record` and `summary` CLI
- `scripts/postmortem_core.py` — classification and feedback copied from signal-postmortem
- `scripts/_india_data.py` — vendored NSE data module (bar cache, adjustment, IST)
- `references/india-signal-outcomes.md` — horizons, classes, excess view and caveats

## Key Principles

1. **Every signal counts** — traded or not, so screener quality is judged on its own.
2. **Sessions, not days** — horizons follow the NSE calendar, holidays excluded.
3. **Beat the index or say so** — excess over NIFTY 50 sits beside every raw return.
4. **Same rules as the US skill** — only data, calendar and currency are Indian.
