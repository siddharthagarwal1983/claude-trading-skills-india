# India Pre-Trade Checks

The India gate runs the US `pre-trade-discipline-gate` engine first, then raises each
actionable candidate's decision with the checks below. A decision only ever moves up
(`GO` < `NO_ACTIONABLE_ORDERS` < `REVIEW_REQUIRED` < `NO_GO`).

## Checklist answers built from a breakout plan

For each `actionable_orders[]` entry of an `india-breakout-trade-planner` report:

| Answer | Value |
|--------|-------|
| `order_intent` | `ACTIONABLE` |
| `entry_in_written_plan` | true (the plan is the written plan) |
| `stop_predefined` | the order has a numeric stop above zero and below the trigger |
| `size_within_plan` | order quantity equals `trade_plan.shares` |
| `planned_risk_dollars` | `trade_plan.risk_inr` |
| `actual_risk_dollars` | recomputed: qty × (cap − stop) + buy charges at the cap + sell charges at the stop, with the plan's brokerage and DP charge |

The recompute uses the planner's exact formula, so an untouched plan gives identical
numbers, while a plan edited by hand (more shares, a lower stop) shows a larger order
risk and fails as NO_GO.

## Plan freshness

A plan is for one NSE session. The next session an order could fill in is today if
the gate runs before 09:15 IST on a trading day, otherwise the next trading day. If
that is later than the plan's `plan_valid_for_session`, the setup was planned on old
prices: NO_GO, re-run the screener and planner.

## Paper-ledger duplicates

`positions.json` and `orders.json` in the ledger state directory are read (read-only).
A symbol with an open paper position or an `OPEN` order is NO_GO, so a plan cannot
double up on a name.

## NSE results-date risk

Indian companies announce quarterly results during the session or after the close,
and the next day's open can gap through a stop. The gate reads the
`india-earnings-calendar` archive and flags a symbol whose next results date falls
within the next `--results-buffer-sessions` NSE sessions (default 5): REVIEW_REQUIRED.
The user may accept the risk explicitly with `--accept-results-risk SYMBOL`; the
acceptance is recorded in the candidate reasons and `india_checks`. A missing archive
is a warning only (the check is skipped).

## Upstream reports

The US engine reads the `recommendation` field of each report. The India gate also
requires:

| Report | Must come from | Fresh when |
|--------|----------------|------------|
| market regime | `india-exposure-coach` (`skill` field) | `as_of` is the latest completed NSE session or later |
| circuit breaker | `india-drawdown-circuit-breaker` | `as_of_date` is today (IST) or later |

A US report or a stale one makes actionable candidates REVIEW_REQUIRED: rerun the
India skill. Missing or unreadable reports are already REVIEW_REQUIRED in the engine.

## Revenge window

The engine scans trader-memory-core theses (`--state-dir`, default
`state/india-paper/theses`) for a losing exit or trim within `--revenge-window-hours`
(default 24) of the as-of time; any such loss is NO_GO for new entries. Timestamps are
absolute, so the window is the same in IST and US time.
