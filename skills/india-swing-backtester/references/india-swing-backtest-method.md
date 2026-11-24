# India Swing Backtest Method

## Timeline of one session D

| Step | What happens | Code reused |
|---|---|---|
| Exits | Positions entered before D: dividend credit if D is an ex-date; exit if D's bar hits the stop or target (stop first when both); stop fills less slippage; optional time stop at D's close | `paper_engine.exit_fill`, `apply_slippage`, `dividend_per_share` |
| Entries | Orders planned on an earlier evening: buy-stop at the trigger, capped at the limit; open above the limit = no fill; one-price session = no fill; slippage on the fill capped at the limit; cash checked. A buy **limit** whose session low then reaches the stop exits the same session (see below) | `paper_engine.entry_fill`, `leg_charges`, `effective_price` |
| Mark | Equity = cash + shares × D's close − estimated selling charges on the open positions (`--liquidation-valuation gross` drops the deduction) | `leg_charges` |
| Screen (vcp) | Funnel on bars dated in [D − 430 days, D] | `screen_india_vcp.prepare_history`, `run_funnel` |
| Plan (vcp) | Top 20 results → plans sized on equity, with open risk, cash, sector exposure and held symbols from the simulated account | `plan_india_breakout_trades.generate_plans` |
| Screen (momentum) | `analyze_symbol` on the last 80 bars dated on or before D, results in the screener's order | `screen_india_momentum_burst.normalize_bars`, `analyze_symbol`, `sort_results` |
| Plan (momentum) | Signals of D (ACTIONABLE_DAY1 / MANUAL_REVIEW with setup score ≥ `min_score`, no possible upper circuit) → buy limit at close + `max_chase_pct`, stop at the trigger-day low, skipped above `max_risk_pct` limit-to-stop; heat, position, sector, turnover and cash caps | `india_breakout_risk.size_position` |

An order lives `valid_sessions` sessions (default 1, as the planner emits); a
session with no bar for the stock (suspension) uses one up. Stop-limit fills get
the stop slippage capped at the limit; limit fills do not (as in the paper
ledger). Late-breakout VCP "revalidation" plans and momentum MANUAL_REVIEW names
below the score threshold need a human chart check and are not traded.

### Entry-session stops (Phase 1.1, F2)

A buy limit fills at the open (when the open is at or below the limit) or as the
price falls through the limit. The stop sits below the limit, so a session low at
or under the stop came **after** the fill: the position exits that session at the
stop (or at the open when it opened there), less stop slippage, and the trade is
recorded with exit reason `stop_hit_entry_session`. The target and the time stop
still start the session after entry. A buy-stop (VCP) fills on the way up, and a
daily bar cannot show whether the low came before or after the trigger, so its
stop is checked from the next session, as in india-paper-ledger; the trade is
flagged `entry_day_stop_touched` and counted in `entry_session_stop_touch_kept`.
`--entry-session-stop off` restores the earlier next-session rule for every order.

### Point-in-time screening (Phase 1.1, F7b)

The simulation runs on bars back-adjusted for every split and bonus in the data,
so share counts are in today's units. A screen of session D instead sees prices
and volumes **as quoted on D**: bars are divided by the product of the factors of
actions with ex-date after D (`future_factor`), so the ₹50 minimum price, the ₹25
rupee breakout, the share-volume floors and NSE tick bands apply to what a trader
saw then. Plans are priced and tick-rounded in those units and converted back
(prices × factor, shares ÷ factor, rounded down) for the simulation. Rupee
turnover is unaffected. `--screen-prices back_adjusted` restores the old screens.

### Regime gate (Phase 1.1, F4)

The live workflow (india-exposure-coach SKILL.md, step 4) maps the coach's action
to `--market-gate` (NEW_ENTRY_ALLOWED → `allowed`, REDUCE_ONLY → `restrictive`,
CASH_PRIORITY → skip new entries) and acts on VCP candidates only under
NEW_ENTRY_ALLOWED. `--regime-mode`:

| Mode | Entries | Momentum `--market-gate` | Faithful to live? |
|---|---|---|---|
| `no_gate` (default) | every session | `neutral` every session (screener default) | No — the earlier behaviour |
| `sma200_proxy` | only when NIFTY 500 closes at/above its 200-session average; unknown (< 200 closes) blocks | per session | No — a labelled one-input proxy |
| `exposure_file` | only on NEW_ENTRY_ALLOWED sessions in `--regime-file`; missing sessions block | per session | Only as faithful as the file |

The posture of D (known after D's close) governs orders planned on D. Exits and
orders already placed are never blocked. A faithful replay of the coach needs its
inputs as of each session: NSE index history with volume and P/E starts on
2025-01-01 and the cached INDIA VIX on 2025-08-29, so distribution days, FTD,
trend and volatility cannot all be rebuilt for 2024.

### Limit fills (Phase 1.1, A1)

By default a buy limit fills whenever the session low reaches the limit (a
"touch"). `--limit-fill trade_through` is a conservative sensitivity run that
fills at the limit only when the low traded at least one NSE tick below it;
`limit_touch_only` counts the fills that rule would drop.

### Calendar

The simulation steps through the benchmark's sessions. A session most stocks
traded that the benchmark lacks is listed in the report (`calendar`) and is
neither simulated nor used for relative strength, which joins stock and benchmark
bars on dates (`--rs-alignment date`; `offset` is the earlier position-by-position
comparison). Muhurat (one-hour Diwali) sessions are simulated like any other
session (policy `include`); the report lists them and the entries and exits on them.

`--legacy` restores every pre-Phase-1.1 mode at once (explicit flags still win);
with it the 2026-11-23 NIFTY 500 baselines reproduce trade for trade.

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
- **Universe:** today's index membership (survivorship-biased; see below).

## Known biases

| Bias | Direction | Note |
|---|---|---|
| Survivorship | Flatters | Stocks that dropped out of NIFTY 500 are missing. Of 400 NIFTY 500 members on 2024-06-04 checked against the DalalOS membership record, 80 (20%) are no longer members |
| Split adjustment | Neutral for P&L | Fixed for screens by point-in-time prices (default). With `--screen-prices back_adjusted`, prices before a later split or bonus are lower and volumes higher than quoted, so the ₹50 minimum wrongly **excludes** stocks, the ₹25 rupee breakout rarely fires and share-volume floors pass too easily |
| Daily bars | Pessimistic | Stop counted first when one bar touches both levels |
| Limit touch fills | Flatters | A limit order fills when the low only touches the limit; see `--limit-fill trade_through` |
| Regime gate | Unknown | Not applied by default; the live workflow gates entries with india-exposure-coach |
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
