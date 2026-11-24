---
layout: default
title: "India Swing Backtester"
grand_parent: English
parent: Skill Guides
nav_order: 52
lang_peer: /ja/skills/india-swing-backtester/
permalink: /en/skills/india-swing-backtester/
generated: true
---

# India Swing Backtester
{: .no_toc }

Backtest India swing workflows on past NSE sessions by replaying the live skills' own code with Indian delivery charges — VCP breakouts (india-vcp-screener funnel, india-breakout-trade-planner orders) or Stockbee momentum bursts (india-stockbee-momentum-burst-screener signals, next-session limit entry, trigger-day-low stop, 3-5 session time exit), with india-paper-ledger fill rules. Reports return, drawdown, R-multiples and stability against NIFTY 500 and sweeps planner, exit and research-only rule settings. Free NSE data. Use before paper trading a strategy, or to test changed settings.
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[Download Skill Package (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/india-swing-backtester.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/india-swing-backtester){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

Replay an India swing workflow over past NSE sessions and measure how it would
have done. The sibling skills' code is imported, not copied, so the simulated
trades are the ones the live workflow would place. Pick the strategy with
`--strategy`:

| | `vcp` (default) | `momentum-burst` |
|---|---|---|
| Screen | `india-vcp-screener` funnel (`prepare_history` + `run_funnel`), 430-day window | `india-stockbee-momentum-burst-screener` `analyze_symbol`, 80-bar window |
| Signal | `india-breakout-trade-planner` `generate_plans` on the top 20 results | state ACTIONABLE_DAY1 (setup score 80+); possible upper-circuit closes skipped |
| Entry | buy stop-limit just above the pivot, valid 1 session | buy limit at the signal close + 2%, next session |
| Stop / exit | under the last contraction low; 2R target | trigger-day low; close of the 5th session held (no target) |
| Sizing | planner (0.5% risk × rating band, caps) | planner's `size_position` (0.5% risk, same caps) |

Every session D: exits for positions entered before D (paper-ledger stop-before-
target rule, stop slippage, time stop; dividends on ex-dates), then fills of the
orders planned the evening before (paper-ledger stop-limit / limit rules;
one-price circuit sessions do not fill), then mark-to-market, then the screen of
D (bars dated on or before D only) becomes orders for D+1. Parity tests check
that a backtest screen equals the live screener's report for the same day.

Metrics: equity curve, CAGR, max drawdown, Sharpe, exposure, trades, win rate, R,
charges, results by year, by rating, by NIFTY 500 regime (above/below its 200-day
average at the signal) and first vs second half, against NIFTY 500.

---

## 2. When to Use

- Before paper trading a strategy, to see whether it has an edge after costs
- After changing screener, planner or exit settings, to compare with the defaults
- To test robustness with sweeps (targets, stops, hold time, order validity,
  score and risk gates)

---

## 3. Prerequisites

- Python 3.9+ with `pandas-market-calendars`; `yfinance` optional (`requirements.txt`)
- Sibling skills next to this one (or their scripts directories in the env vars):
  `india-vcp-screener` (`INDIA_VCP_SCREENER_DIR`), `india-breakout-trade-planner`
  (`INDIA_BREAKOUT_PLANNER_DIR`), `india-paper-ledger` (`INDIA_PAPER_LEDGER_DIR`)
  and, for momentum bursts, `india-stockbee-momentum-burst-screener`
  (`INDIA_MOMENTUM_BURST_DIR`)
- No API key. Stock bars come from the shared NSE cache (`.cache/india-data`,
  fetched free from the NSE bhavcopy server when missing). NSE's index history
  starts in January 2025; earlier NIFTY 500 bars come from Yahoo Finance
  (`^CRSLDX`) and are used only if they match NSE's closes on the overlap (within
  0.05%). Without yfinance the test window starts once enough NSE index sessions
  exist.

---

## 4. Quick Start

```bash
python3 skills/india-swing-backtester/scripts/india_swing_backtest.py --strategy vcp --output-dir reports/
python3 skills/india-swing-backtester/scripts/india_swing_backtest.py --strategy momentum-burst \
  --universe nifty100 --output-dir reports/
```

---

## 5. Workflow

### Step 1: Run a strategy with its live rules

```bash
python3 skills/india-swing-backtester/scripts/india_swing_backtest.py --strategy vcp --output-dir reports/
python3 skills/india-swing-backtester/scripts/india_swing_backtest.py --strategy momentum-burst \
  --universe nifty100 --output-dir reports/
```

Defaults: NIFTY 500 stocks (`--universe nifty50|nifty100|...` or `--symbols`),
bars from 2024-01-01, test window from the first session after a warm-up (252
sessions for VCP, 100 for momentum) to the latest completed session, ₹10 lakh,
0.5% risk per trade, 6% heat, 10% position, 1% of turnover, 10 bps slippage on
stop entries and stop exits. The first run screens every session (under a minute
for NIFTY 500 with a warm cache); later runs reuse the cached screens.

### Step 2: Sweep settings

```bash
python3 skills/india-swing-backtester/scripts/india_swing_backtest.py --strategy momentum-burst \
  --sweep max_hold_sessions=3,4,5 min_score=70,80 target_r_multiple=0,2
```

Any `SimParams` field except `capital` and `strategy` can be swept. Screener
settings change the screens instead: `--screener-args "--trend-min-score 80"` (VCP)
or `--screener-args "--market-gate allowed"` (momentum; the live default is
`neutral`).

### Step 3: VCP research-only planner rules

The live planner plans only valid VCPs scoring 70+ whose worst entry is within 8%
of the stop, and sizes scores below 70 at zero. `--max-risk-pct`, `--min-score`
and `--developing-multiplier` (size for 60-69 scores, default 0.5) change those
gates by patching the planner module only while each simulated plan is made.
Reports label such runs **research variant**; adopting one in the live planner is
a deliberate, separate change.

### Step 4: Choose the simulation modes

Defaults are the Phase 1.1 corrected behaviour (`references/india-swing-backtest-method.md`):
buy limits stop out on the entry session when its low reaches the stop, equity is
net of estimated selling charges, screens see prices as quoted that day, and VCP
relative strength joins stock and benchmark on dates. Two modes stay off by default
and are for sensitivity runs only:

- `--regime-mode sma200_proxy` — a labelled one-input proxy, **not** the
  india-exposure-coach gate; `--regime-mode exposure_file --regime-file <json>`
  takes per-session coach actions. Report the mode and its limitation with results.
- `--limit-fill trade_through` — buy limits fill only when traded a tick through.

`--legacy` restores every earlier mode, which reproduces pre-Phase-1.1 reports.
Universes are today's index members (survivorship-biased); say so with results.

### Step 5: Read the report

`reports/india_swing_backtest_<strategy>_<universe>_<start>_<end>.md` / `.json`,
plus `_trades.csv` and `_equity.csv`. Lead with strategy vs NIFTY 500, closed
trades and average R, then the stability table. Treat fewer than about 30 trades
as anecdotal and a sweep's best cell as optimistic: compare neighbours, prefer
plateaus to peaks, and check average R before charges in the trade list.

---

## 6. Resources

**References:**

- `skills/india-swing-backtester/references/india-swing-backtest-method.md`

**Scripts:**

- `skills/india-swing-backtester/scripts/_india_data.py`
- `skills/india-swing-backtester/scripts/india_swing_backtest.py`
- `skills/india-swing-backtester/scripts/swing_backtest_engine.py`
- `skills/india-swing-backtester/scripts/swing_backtest_metrics.py`
- `skills/india-swing-backtester/scripts/swing_backtest_siblings.py`
