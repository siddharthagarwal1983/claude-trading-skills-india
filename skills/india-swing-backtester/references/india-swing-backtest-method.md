# India Swing Backtest Method

## Timeline of one session D

| Step | What happens | Code reused |
|---|---|---|
| Exits | Positions entered before D: dividend credit if D is an ex-date; exit if D's bar hits the stop or target (stop first when both); stop fills less slippage; optional time stop at D's close | `paper_engine.exit_fill`, `apply_slippage`, `dividend_per_share` |
| Entries | Orders planned on an earlier evening: buy-stop at the trigger, capped at the limit; open above the limit = no fill; one-price session = no fill; slippage on the fill capped at the limit; cash checked | `paper_engine.entry_fill`, `leg_charges`, `effective_price` |
| Mark | Equity = cash + shares × D's close | — |
| Screen (vcp) | Funnel on bars dated in [D − 430 days, D] | `screen_india_vcp.prepare_history`, `run_funnel` |
| Plan (vcp) | Top 20 results → plans sized on equity, with open risk, cash, sector exposure and held symbols from the simulated account | `plan_india_breakout_trades.generate_plans` |
| Screen (momentum) | `analyze_symbol` on the last 80 bars dated on or before D, results in the screener's order | `screen_india_momentum_burst.normalize_bars`, `analyze_symbol`, `sort_results` |
| Plan (momentum) | Signals of D (ACTIONABLE_DAY1 / MANUAL_REVIEW with setup score ≥ `min_score`, no possible upper circuit) → buy limit at close + `max_chase_pct`, stop at the trigger-day low, skipped above `max_risk_pct` limit-to-stop; heat, position, sector, turnover and cash caps | `india_breakout_risk.size_position` |

An order lives `valid_sessions` sessions (default 1, as the planner emits); a
session with no bar for the stock (suspension) uses one up. Stop-limit fills get
the stop slippage capped at the limit; limit fills do not (as in the paper
ledger). Late-breakout VCP "revalidation" plans and momentum MANUAL_REVIEW names
below the score threshold need a human chart check and are not traded.

The momentum-burst order rules are this skill's reading of the screener's
`references/entry_exit_rules.md` (stop at the trigger-day low, review after 3-5
sessions): an end-of-day trader buys the next morning, so entry is a next-session
limit order a little above the signal close, and the exit is the close of the
`max_hold_sessions`-th session unless the stop (or an optional target) is hit first.

## Data

- **Stocks:** official NSE bhavcopy bars, adjusted for splits and bonuses with
  NSE's corporate actions (as of the download). Unexplained jumps above 20% inside
  the 260-session window exclude the stock that day, as in the live screener.
- **Index:** NSE's index history starts on 2025-01-01. Earlier NIFTY 500 bars come
  from Yahoo Finance `^CRSLDX` (NIFTY 50: `^NSEI`), accepted only when its closes
  match NSE's within 0.05% on at least 10 shared sessions; the check is stored with
  the cached backfill in `.cache/india-data/backtest/`.
- **Universe:** today's index membership.

## Known biases

| Bias | Direction | Note |
|---|---|---|
| Survivorship | Flatters | Stocks that dropped out of NIFTY 500 are missing |
| Split adjustment | Neutral for P&L | Prices and ticks before a later split differ from what was quoted; ₹50 minimum price can admit a stock that was then above it |
| Daily bars | Pessimistic | Stop counted first when one bar touches both levels |
| No taxes | Flatters | STCG on gains is not deducted |
| Short history | Unknown | About two years of mostly rising market |
| No chart review | Unknown | Live use filters signals by eye; the backtest takes every qualifying one |

## Judging a result

- **Trades:** below ~30 closed trades any statistic is anecdotal; a 2R system at
  a 30% win rate has a breakeven near 28-30%, so a few trades swing the verdict.
- **Expectancy:** average R above ~0.2 after charges is the minimum worth paper
  trading; check the median R and the exit-reason mix.
- **Benchmark:** compare with NIFTY 500 for the same sessions, and with average
  exposure: a strategy invested 10% of the time cannot match a fully invested index
  in a rally, so judge return per unit of exposure and drawdown too.
- **Stability:** first vs second half, each year, regime above/below the 200-day
  average. An edge confined to one half or one regime is fragile.
- **Sweeps:** each extra cell tried makes the best cell more likely to be luck.
  Prefer settings whose neighbours are similar (a plateau) and keep the live
  defaults unless a change is clearly and broadly better.
