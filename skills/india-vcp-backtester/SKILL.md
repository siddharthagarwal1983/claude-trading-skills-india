---
name: india-vcp-backtester
description: Backtest the India VCP breakout workflow on past NSE sessions — india-vcp-screener's own funnel screens each day using only bars up to that day, india-breakout-trade-planner turns the result into stop-limit orders for a simulated rupee account, and india-paper-ledger's fill rules fill entries and stop/target exits with Indian delivery charges. Reports return, drawdown, R-multiples and stability against NIFTY 500, and sweeps planner, exit and research-only rule settings. Free NSE data. Use before paper trading the VCP strategy, or to test changed settings.
---

# India VCP Backtester

## Overview

Replay the India VCP breakout workflow over past NSE sessions and measure how it
would have done. It reuses the sibling skills' code rather than copying it, so the
simulated trades are the ones the live workflow would place:

1. **Screen** — each session D, `india-vcp-screener`'s funnel (`prepare_history` +
   `run_funnel`) runs on adjusted bars dated on or before D, over the same
   430-day window as a live run. A parity test checks the result equals the live
   screener's report for the same day. Screens are cached per settings, so
   re-runs and sweeps skip this step.
2. **Plan** — the top results (default 20, as in the screener's JSON report) go to
   `india-breakout-trade-planner`'s `generate_plans` with the simulated account as
   the paper-ledger state (equity, cash, open risk, held symbols).
3. **Fill** — next session, stop-limit orders fill with `india-paper-ledger`'s rules
   (gap past the limit: no fill; one-price circuit session: no fill; slippage
   capped at the limit). Exits use its stop-before-target rule, never on the entry
   day. Delivery charges on both legs; dividends credited on the ex-date.
4. **Measure** — equity curve, CAGR, max drawdown, Sharpe, trades, win rate, R,
   charges, exposure, results by year, by rating band, by NIFTY 500 regime
   (above/below its 200-day average at the signal), and first vs second half;
   compared with NIFTY 500 over the same sessions.

## When to Use

- Before paper trading the VCP breakout strategy, to see whether it has an edge
- After changing screener or planner settings, to compare against the defaults
- To test robustness: sweep the target, stop buffer, chase, order validity,
  time stop and the research-only planner rules

## When Not to Use

- For live trade plans — use `india-vcp-screener` + `india-breakout-trade-planner`
- For momentum-burst or other screens — this replays the VCP workflow only
- As proof of future returns: see the caveats below

## Prerequisites

- Python 3.9+ with `pandas-market-calendars`; `yfinance` optional (`requirements.txt`)
- The sibling skills `india-vcp-screener`, `india-breakout-trade-planner` and
  `india-paper-ledger` installed next to this one (or their scripts directories in
  `INDIA_VCP_SCREENER_DIR`, `INDIA_BREAKOUT_PLANNER_DIR`, `INDIA_PAPER_LEDGER_DIR`)
- No API key. Stock bars come from the shared NSE cache (`.cache/india-data`,
  fetched free from the NSE bhavcopy server when missing). NSE's index history
  starts in January 2025; earlier NIFTY 500 bars come from Yahoo Finance
  (`^CRSLDX`) and are used only if they match NSE's closes on the overlap
  (within 0.05%). Without yfinance the test window starts once 252 NSE index
  sessions exist.

## Workflow

### Step 1: Run the backtest with the live rules

```bash
python3 skills/india-vcp-backtester/scripts/india_vcp_backtest.py --output-dir reports/
```

Defaults: NIFTY 500, bars from 2024-01-01, test window from the first session
with a 252-session warm-up to the latest completed session, ₹10 lakh, planner
defaults (0.5% risk scaled by rating band, 6% heat, 10% position, 2R target, 1%
stop buffer, 2% chase, orders valid 1 session), 10 bps slippage on stop entries
and stop exits, no time stop. The first run screens every session (about 30
seconds for NIFTY 500 with a warm cache); later runs reuse the screens.

### Step 2: Sweep settings

```bash
python3 skills/india-vcp-backtester/scripts/india_vcp_backtest.py \
  --sweep target_r_multiple=1.5,2,3 stop_buffer_pct=0.5,1,2 valid_sessions=1,3,5
```

Any `SimParams` field except `capital` can be swept (`risk_pct`,
`max_portfolio_heat_pct`, `max_chase_pct`, `max_hold_sessions`, ...). Screener
settings change the screens instead: `--screener-args "--trend-min-score 80"`.

### Step 3: Research-only planner rules

The live planner only plans valid VCPs scoring 70+ whose worst entry is within
8% of the stop, and sizes scores below 70 at zero. To study those gates:

```bash
python3 skills/india-vcp-backtester/scripts/india_vcp_backtest.py \
  --sweep max_risk_pct=8,10,12 min_score=60,65,70
```

`--min-score` below 70 plans 60-69 scores as the "developing" band at
`--developing-multiplier` (default 0.5; "good" is 0.75). The planner module is
patched only while each simulated plan is made and restored afterwards. Reports
label such runs **research variant**; a variant that looks better must be adopted
in the live planner deliberately, not by this skill.

### Step 4: Read the report

`reports/india_vcp_backtest_<start>_<end>.md` / `.json`, plus `_trades.csv` and
`_equity.csv`. Lead with strategy vs NIFTY 500, the number of closed trades and
average R, then the stability table. Treat fewer than about 30 trades as
anecdotal, and a sweep's best cell as optimistic: compare its neighbours, prefer
broad plateaus over peaks.

## Output Format

```json
{
  "skill": "india-vcp-backtester",
  "window": {"start": "2025-01-13", "end": "2026-11-20", "sessions": 458},
  "base": {
    "params": {"capital": 1000000.0, "target_r_multiple": 2.0, "max_risk_pct": 8.0, "min_score": 70.0},
    "summary": {
      "strategy": {"total_return_pct": -0.76, "cagr_pct": -0.41, "max_drawdown_pct": -1.09, "sharpe": -0.43},
      "benchmark": {"name": "NIFTY 500", "total_return_pct": 13.39},
      "trade_stats": {"trades": 2, "win_rate": 0.0, "avg_r": -0.788, "profit_factor": 0.0},
      "by_year": {}, "by_rating_band": {}, "by_regime_at_entry": {}, "halves": {},
      "events": {"actionable_plans": 7, "orders_filled": 2, "orders_expired": 5}
    },
    "open_positions": []
  },
  "sweep": [{"overrides": {"max_risk_pct": 12.0}, "summary": {}}]
}
```

## Resources

- `scripts/india_vcp_backtest.py` — CLI, data loading, index backfill, reports
- `scripts/vcp_backtest_engine.py` — screening replay, screen cache, simulator, research rules
- `scripts/vcp_backtest_metrics.py` — return, drawdown and trade statistics
- `scripts/vcp_backtest_siblings.py` — loads the screener, planner and paper-fill code
- `scripts/_india_data.py` — vendored NSE data module
- `references/india-vcp-backtest-method.md` — method, assumptions and how to judge results

## Key Principles

1. **Replay, don't reimplement** — screener, planner and fills are the live code.
2. **No look-ahead** — a session sees only bars dated on or before it; orders fill
   from the next session.
3. **Costs in** — statutory delivery charges, DP charge and stop slippage.
4. **Sample size first** — few trades or a peaked sweep means no conclusion.
