# Phase 1.1 — Workstream A: Backtester Integrity Corrections

**Branch:** `india-layer` (base `d47679fe`) · **Date:** 2026-11-24
**Input:** `docs/research/PHASE1_BACKTEST_INTEGRITY_AUDIT.md`
**Status:** implemented and tested in the working tree. **Not committed or pushed.**
Paper-ledger code was not touched (Workstream B has not started).

---

## 1. Summary

| # | Item | Result |
|---|---|---|
| A1 | Baseline preservation | Both baselines copied with SHA-256 checksums; originals untouched; `--legacy` reproduces both **trade for trade** with the new code |
| A2 | F2 entry-session stop (LIMIT entries) | **Fixed**, default on. Momentum NIFTY 500: 210 trades now stop on the entry session; return −45.96% → −55.34% on its own |
| A3 | F3 net liquidation valuation | **Fixed**, default net. About 0.06% of capital at the end; reports now show gross value, estimated costs and net value |
| A4 | F4 regime gate | **Plumbing added; the gate stays off by default.** The live policy is documented, but the exposure coach cannot be replayed faithfully for the whole window. `sma200_proxy` is offered as a clearly labelled sensitivity run. **Policy decision needed** (§9) |
| A5 | F7b point-in-time screening | **Fixed**, default on. Validated on all 64 real pre-action sessions (0 mismatches against raw NSE bars) |
| A6 | F9 date-aligned relative strength | **Fixed** in the VCP screener (live and backtest), default on. Calendar audit and Muhurat policy (`include`) documented |
| A7 | F1 historical universe | **Not fixed.** Point-in-time membership **is** available from DalalOS; 80 of 400 checked 2024-06-04 members (20%) have since left NIFTY 500. Recommendation in §8 |
| A8 | A1 limit-touch fills | Optional `--limit-fill trade_through`. **0 fills affected** in both strategies |

- **Tests:** all six suites pass: **654 tests, 654 passed, 0 failed, 0 skipped**. That is the original 597 plus 57 new; no existing test was modified.
- **Corrected headline:**
  - Momentum Burst on NIFTY 500 goes from **−45.96% to −53.20%**, and average R from −0.159 to −0.170.
  - VCP on NIFTY 500 is **unchanged at −0.76%** (2 trades); no correction touched its 7 plans.
- These are research results on a survivorship-biased universe (§8), not evidence that either strategy works or fails in live trading.

---

## 2. Baseline provenance (A1)

Neither original report recorded its command line. The commands below are rebuilt from the
parameters, window, screen cache and sweep axes stored in each report JSON. Re-running the base
case with the unchanged code reproduced every trade and equity row and every summary value; the
only exception is one event counter (`rejected_risk`) that newer code added to the VCP report.
The sweeps were not re-run.

| | Momentum Burst NIFTY 500 | VCP NIFTY 500 |
|---|---|---|
| Original files | `reports/india-swing-backtest/india_swing_backtest_momentum-burst_nifty500_2024-06-04_2026-11-20{.json,.md,_trades.csv,_equity.csv}` | `reports/india_vcp_backtest_2025-01-13_2026-11-20{.json,.md,_trades.csv,_equity.csv}` |
| Preserved copies | `reports/phase1_1/baseline/momentum-burst_nifty500/` | `reports/phase1_1/baseline/vcp_nifty500/` |
| Checksums | `reports/phase1_1/baseline/SHA256SUMS` (verified again at the end: all OK; originals unchanged) | same |
| Generated | 2026-11-23 17:26:47 IST | 2026-11-23 16:31:41 IST |
| Skill / commit | `india-swing-backtester` at `bd2e4ed1` (`78041048` after the email rewrite) | `india-vcp-backtester`, the skill's earlier name, at `542c12f8` (`050e218e` after the rewrite) |
| Command (rebuilt) | `india_swing_backtest.py --strategy momentum-burst --universe nifty500 --sweep max_hold_sessions=3,4,5 min_score=70,80 target_r_multiple=0,2` | `india_vcp_backtest.py --universe nifty500 --sweep max_risk_pct=8,10,12 min_score=60,65,70 target_r_multiple=2,3` |
| Window | 2024-06-04 → 2026-11-20, 610 sessions, data from 2024-01-01 | 2025-01-13 → 2026-11-20, 458 sessions, data from 2024-01-01 |
| Universe | NIFTY 500 as of the 2026-11-22 download (501 rows; DUMMYHEG has no data) | same |
| Parameters | ₹10 lakh; risk 0.5%; heat 6%; position 10%; sector 30%; ADV 1%; chase 2%; LIMIT valid 1 session; stop at the trigger-day low; no target; 5-session time stop; max risk 10%; score ≥ 80; slippage 10 bps; DP ₹15.93 | ₹10 lakh; risk 0.5% × rating band; heat 6%; target 2R; stop buffer 1%; max chase 2%; pivot buffer 0.1%; valid 1 session; max risk 8%; score ≥ 70; slippage 10 bps |
| Screen cache | `momentum_screens_ef7dbee28c58c1b3.jsonl` | `vcp_screens_58d1690cb37fbff5.jsonl` |
| Data vintage | Stock bars: all 500 cached files end 2026-11-20 and none changed after the baseline. NIFTY 500 index from NSE (2025-01-01 →) plus Yahoo `^CRSLDX` backfill for 2024 (32-session overlap check, maximum difference 0.0). Only the NIFTY 50/100 index files changed afterwards, and these runs don't use them | same |

Reproduction runs are kept in `reports/phase1_1/reproduce_pre_change/` (code before any edit)
and `reports/phase1_1/runs/00_legacy/` (new code with `--legacy`). Comparing baseline columns
row by row: momentum trades 2,195/2,195 and equity 610/610 identical; VCP trades 2/2 and equity
458/458 identical.

**Baseline headline metrics**

| Metric | Momentum Burst | VCP |
|---|---:|---:|
| Total return | −45.96% | −0.76% |
| Net P&L, closed trades | ₹−4,83,210 | ₹−7,566 |
| End equity | ₹5,40,398 | ₹9,92,434 |
| CAGR | −22.12% | −0.41% |
| Max drawdown | −50.09% | −1.09% |
| Closed trades | 2,195 | 2 |
| Win rate | 34.1% | 0.0% |
| Profit factor | 0.76 | 0.00 |
| Average R | −0.159 | −0.788 |
| Open positions at end | 14 | 0 |
| Charges | ₹2,44,093 | ₹291 |
| NIFTY 500 over the window | +18.52% | +13.39% |

---

## 3. Files changed

| File | Change |
|---|---|
| `skills/india-swing-backtester/scripts/swing_backtest_engine.py` | Point-in-time helpers (`future_factor`, `point_in_time_bars`, `to_sim_units`, lines 127-165); regime helpers (`sma200_action`, `regime_actions`, `load_regime_file`, `market_gate_for`, 168-215); `screen_day` / `screen_day_momentum` take `pit` and a per-session `gate` (252, 325); `screen_key` takes `extra` (381); `screen_range` passes them on (445); new `SimParams` modes and validation (498-563); `_valuation` (676); `_touch_only` and `_entry_session_stop` (849, 857); regime gate in `_plan` (885); plan unit conversion (920, 962, 986); `final_valuation` and `_open_position` (1085, 1097) |
| `skills/india-swing-backtester/scripts/india_swing_backtest.py` | `LEGACY_MODES` / `DEFAULT_MODES` (48); `regime_summary`, `muhurat_trades`, `calendar_audit` (75-125); "Simulation settings" Markdown section (502); flag group (639); mode resolution (699); regime map and gates (777); `corrections` / `regime` / `calendar` in the report JSON (840) |
| `skills/india-swing-backtester/scripts/swing_backtest_metrics.py` | Passes `final_valuation` into the summary |
| `skills/india-vcp-screener/scripts/screen_india_vcp.py` | `align_for_rs`, `relative_strength` (68-118); `analyze_stock(..., rs_alignment=)` (135, 181); `run_funnel` (564); `--rs-alignment` flag (691). The copied US relative-strength calculator is unchanged |
| `skills/india-swing-backtester/scripts/tests/test_phase1_1_corrections.py` | **New**, 49 tests |
| `skills/india-vcp-screener/scripts/tests/test_rs_alignment.py` | **New**, 8 tests |
| `skills/india-swing-backtester/references/india-swing-backtest-method.md` | Entry-session stops, point-in-time screening, regime modes, limit fills, calendar; the F7b bias row corrected |
| `skills/india-swing-backtester/SKILL.md`, `skills/india-vcp-screener/SKILL.md` | New Step 4 (simulation modes); RS alignment note |
| `docs/en/skills/india-swing-backtester.md`, `docs/en/skills/india-vcp-screener.md` | Regenerated with `generate_skill_docs.py --skill <id> --overwrite` (generator-owned pages). `generate_skill_docs.py --check` reports 0 drift. The generator also re-added a known bogus catalog row ("Stockbee 20pct Study"); that was reverted |
| `docs/research/PHASE1_1A_BACKTESTER_FIX_REPORT.md` | This report |

**Not changed:**
- `skills/india-paper-ledger/**`
- the strategy screeners' selection rules, scores and thresholds
- the planner
- `skills-index.yaml`
- `requirements.txt` files
- the vendored `_india_data.py`

**Outside git:**
- `reports/phase1_1/**` (`reports/` is gitignored)
- new screen caches in `.cache/india-data/backtest/`, written alongside the old ones; no cache was deleted and `--rescreen` was never used
- `.cache/india-data/dalalos/list_index_constituents_NIFTY500_2024-06-04.json`, plus one ledger line in `.cache/india-data/credit_ledger.jsonl`

**Before any commit:**
- Repackage the two skills (`scripts/package_skills.py`); the packaged-skill drift hook will otherwise fail.
- Run the full pre-commit suite. I ran its local checks individually: ruff check and format, codespell, no-absolute-paths, CLI error conventions, skill dependency manifests and doc drift all pass. The dependency check's warning about the sibling loader's dynamic import predates this work.

---

## 4. Corrections implemented

### F2 — Entry-session stop for LIMIT entries (`--entry-session-stop on|off`, default on)

- **Rule:** after a LIMIT buy fills, `_entry_session_stop` checks the same bar.
  - If the low is at or under the stop, the position exits that session using the paper engine's own `exit_fill(bar, stop, target=None)`: at the open when the bar opened at or below the stop, otherwise at the stop.
  - The exit pays the existing 10 bps stop slippage and the normal sell charges.
  - The trade is recorded with exit reason **`stop_hit_entry_session`**, `sessions_held = 0`.
- **Why this is not ambiguous:** a buy limit fills at the open or on the way down through the limit, and the stop is below the limit. So a session low under the stop always comes after the fill.
- **What is unchanged:**
  - No target and no time exit on the entry session.
  - The stop-first rule for bars that touch both stop and target.
  - **STOP_LIMIT (VCP) entries keep the documented next-session policy**, because a buy-stop fills on the way up and the bar cannot show whether the low came first. These are flagged `entry_day_stop_touched` and counted (`entry_session_stop_touch_kept`).
- **New trade columns:**
  - `entry_basis`
  - `entry_day_stop_touched`
  - `regime_action_at_signal`

### F3 — Net liquidation valuation (`--liquidation-valuation net|gross`, default net)

- Each session's equity is now cash + market value − the estimated selling charges of every open position, using `leg_charges(buy=False)`: STT, exchange, SEBI and GST, plus the ₹15.93 DP charge **per position**.
- The equity CSV adds `market_value` and `est_liquidation_costs`.
- Open positions add:
  - `market_value`
  - `buy_charges`
  - `est_sell_charges`
  - `net_liquidation_value`
  - `unrealized_net`, which is net of both legs
- `unrealized` keeps its old gross meaning.
- The summary adds `final_valuation` (mode, market value, estimated costs, net value, cash).
- Closed trades are unchanged: charges are booked once, at the actual sale, and the estimate is never posted. `gross` mode is bit-for-bit the old valuation, because `_valuation` keeps Python's compensated `sum()`.

### F4 — Historical regime replay (`--regime-mode no_gate|sma200_proxy|exposure_file`, default `no_gate`)

**Audit before coding.** The live policy *is* written down in
`india-exposure-coach/SKILL.md` lines 62-65:
- NEW_ENTRY_ALLOWED → momentum `--market-gate allowed`
- REDUCE_ONLY → `restrictive`
- CASH_PRIORITY → skip new entries
- VCP candidates are acted on only under NEW_ENTRY_ALLOWED
- The live mapping never uses `neutral`, yet the old backtest used `neutral` for every session.

Faithful historical replay is **not possible for the whole window**:
- The coach's critical inputs (breadth, distribution days, NIFTY 50 trend) and its volatility input need NSE index history with volume and P/E, which starts on 2025-01-01. The Yahoo backfill has no volume.
- The cached INDIA VIX starts on 2025-08-29.
- With two critical inputs missing, the coach's own rule forces REDUCE_ONLY, which would be an artifact of missing data rather than a market signal.

**What was built:**
- `regime_actions` gives a per-session action map.
- `market_gate_for` implements the SKILL.md mapping.
- Momentum screens take the gate **per session** (`screen_range(..., gates=)`), and the cache key includes it.
- `_plan` blocks new entries unless the signal session's action is NEW_ENTRY_ALLOWED. D's posture, known after D's close, governs orders planned on D.
- Exits and orders already placed are never blocked.
- Unknown sessions block, matching the coach's missing-input rule.

**Modes:**

| Mode | Status |
|---|---|
| `no_gate` | Default. Identical to the old behaviour (constant `neutral` momentum gate, no VCP gate); a regime map passed in is ignored |
| `sma200_proxy` | Labelled in the report as "PROXY, not the india-exposure-coach". NIFTY 500 close vs its 200-session average |
| `exposure_file` | Per-session coach actions from `--regime-file`; the path to a faithful replay once such a file exists |

Every report carries `regime.mode`, its description, its limitation, session counts per
action, and the momentum gate type.

### F7b — Point-in-time screening (`--screen-prices point_in_time|back_adjusted`, default point_in_time)

- **Screens:** for screening session D, `future_factor` is the product of split/bonus factors with ex-date **after** D. `point_in_time_bars` divides the back-adjusted prices by it and multiplies volumes, so the screeners see prices and volumes as quoted on D. Actions on or before D still apply.
- **Plans:**
  - Momentum limit and stop, and the VCP planner's trigger, limit, stop and target, are priced and **tick-rounded in quoted units**.
  - They are then converted to the simulation's units: prices × factor; shares ÷ factor, rounded down.
  - The simulation, P&L, dividends and charges keep the single fully adjusted series, so nothing is adjusted twice.
  - The factors are stored per screen (`pit_factors`).
- **Validation on real data:**
  - For all 64 split/bonus events in the momentum window, the point-in-time bar on the session before the ex-date was compared with the raw NSE bar: **0 mismatches** in close and volume.
  - Examples:

    | Stock | Back-adjusted close | Point-in-time close | Raw NSE close | Note |
    |---|---:|---:|---:|---|
    | ANGELONE | 248.99 | 2489.90 | 2489.90 | |
    | BAJFINANCE | 933.10 | 9331.00 | 9331.00 | |
    | PGEL | 399.99 | 3999.90 | 3999.90 | |
    | TATAINVEST | 992.20 | 9922.00 | 9922.00 | |
    | MSUMI | 42.98 | 64.4699 | 64.47 | the audit's ₹50-gate example, now admitted |

  - MSUMI's ₹0.0001 difference is the existing 4-decimal rounding inside `adjust_bars` for its 2-for-3 factor.
- **Documentation:** the method doc's wrong bias statement is corrected.

### F9 — Date-aligned relative strength (VCP screener, `--rs-alignment date|offset`, default date)

- **Join:** `align_for_rs` inner-joins stock and benchmark bars on trading dates before the unchanged RS calculator runs, in both `run_funnel` (the trend filter) and `analyze_stock`.
- **Lookback:** after the join, `closes[63]` means 63 *common* sessions back on both sides.
- **No substitution:** a date never pairs with a different date.
- **Recorded per result in `relative_strength.alignment`:**
  - gaps inside the shared span, on each side
  - `latest_common_date`
  - `latest_date_mismatch`
- **Live impact:** this changes the **live** VCP screener's RS values slightly whenever calendars differ. `offset` restores the old behaviour.
- **Calendar audit:** every report now lists benchmark sessions missing from the stock-majority calendar. The only one found was 2024-02-19, missing from the Yahoo backfill. It lies outside both test windows and is neither simulated nor used for RS.
- **Muhurat policy decided as `include`.** This is the status quo, so existing report calendars are unchanged. The one-hour Diwali sessions are simulated like any session, listed in the report, and their entries and exits are counted. Momentum had 7 entries and 4 exits on them (2024-11-01, 2025-10-21); VCP had none.

### A1 — Limit-touch execution (`--limit-fill touch|trade_through`, default touch)

- `trade_through` fills a LIMIT order at the limit only when the low traded at least one NSE tick below it; the tick is measured in quoted units.
- `limit_touch_only` counts the fills that rule would drop, in every mode.
- **Measured:**
  - Momentum: 2,232 of 2,270 corrected entries filled at the open. The 38 limit fills all traded through, by at least ₹0.15.
  - **0 touch-only fills; no effect on results.**
  - VCP uses STOP_LIMIT orders, so this rule doesn't apply.
- The default is unchanged.

### `--legacy`

Restores every pre-Phase-1.1 mode:
- entry-session stop off
- gross valuation
- back-adjusted screens
- offset RS
- touch fills
- `no_gate`

Explicit flags still win. Reports record `corrections.legacy`.

---

## 5. Tests

Commands (CI's exact pytest command via the matrix script, existing `.venv`, nothing installed):

```bash
.venv/bin/python scripts/ci_test_matrix.py run <skill-id>   # = python -m pytest skills/<id>/scripts/tests --tb=short -q -rfEs
.venv/bin/python -m pytest scripts/tests/test_india_data.py -q --tb=short -rfEs
```

| Suite | Phase 1 | Now | Passed | Failed | Skipped |
|---|---:|---:|---:|---:|---:|
| india-swing-backtester | 130 | 179 | 179 | 0 | 0 |
| india-vcp-screener | 82 | 90 | 90 | 0 | 0 |
| india-stockbee-momentum-burst-screener | 71 | 71 | 71 | 0 | 0 |
| india-paper-ledger | 160 | 160 | 160 | 0 | 0 |
| india-breakout-trade-planner | 99 | 99 | 99 | 0 | 0 |
| shared `test_india_data.py` | 55 | 55 | 55 | 0 | 0 |
| **Total** | **597** | **654** | **654** | **0** | **0** |

No existing test was modified or replaced. Coverage (pytest-cov, already installed):

| File | Coverage |
|---|---:|
| `swing_backtest_engine.py` | 94% |
| `india_swing_backtest.py` | 94% |
| `swing_backtest_metrics.py` | 100% |
| `screen_india_vcp.py` | 85% |

New tests, all executed and passing:

| Area | Tests |
|---|---|
| F2 | LIMIT fill at open below stop → exit at open; fill at open above stop then breach → exit at stop; fill on the way down then breach; no breach keeps the position; charges, slippage and cash for a same-session round trip (DP ₹15.93 on the sell leg); no duplicate exit or negative position; `off` restores the old rule; STOP_LIMIT keeps the next-session policy |
| F3 | Final equity deducts selling charges (and `gross` does not); net unrealized includes both legs; two open positions each pay a DP charge; closed-trade P&L identical in net and gross |
| F4 | Gate blocks entries; gate never blocks exits; the signal session's posture applies (planned under allowed, filled on a blocked day); unknown regime blocks; a gated mode requires a map; `no_gate` ignores any map; 200-session proxy actions; regime-file loader and validation; SKILL.md gate mapping; momentum gate is set per session and restored; cache key tracks gates and price mode; report identifies the mode; file required for `exposure_file`; momentum gate labelled per session vs constant |
| F7b | Screens see quoted prices and volumes before a later split; actions on or before D still apply; ₹50 minimum (live screener); share-volume floor; ₹25 rupee breakout; screen output invariant when a future split is added to the data; momentum tick rounding in the quoted band (339.95 → 33.995, not 33.99) and after a split; VCP plan prices and shares converted (stop 95.04, not 95.03) |
| F9 | Benchmark missing a date; stock missing a date; different histories; latest-session mismatch flagged; lookback counts common sessions; `analyze_stock` honours the mode; CLI default `date` |
| Calendar | Missing benchmark session listed; Muhurat sessions in the data and window, policy `include`; audit function |
| A1 | Touch-only fill counted and skipped under `trade_through`; a one-tick trade-through fills; open fills are never touch fills |
| CLI / params | Defaults recorded; `--legacy` restores all modes; an explicit flag beats `--legacy`; invalid modes rejected; mode fields cannot be swept |
| Parity (existing) | `test_screen_day_matches_live_screener` (×2) and the momentum twin still pass |

---

## 6. Before / after performance

NIFTY 500, `--no-fetch --end 2026-11-20`. "Only" runs add one correction to `--legacy`;
run 05 has all corrections (the new defaults). Outputs are in `reports/phase1_1/runs/<run>/`.

### Momentum Burst (2024-06-04 → 2026-11-20; NIFTY 500 +18.52%)

| Run | Total return | Net P&L closed (₹) | End equity (₹) | CAGR | Max DD | Trades | Win rate | PF | Avg R | Open | Charges (₹) | Est. liq. costs (₹) | Entry-session stops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (original file) | −45.96% | −4,83,210 | 5,40,398 | −22.12% | −50.09% | 2,195 | 34.1% | 0.76 | −0.159 | 14 | 2,44,093 | — | — |
| 00 `--legacy` | −45.96% | −4,83,210 | 5,40,398 | −22.12% | −50.09% | 2,195 | 34.1% | 0.76 | −0.159 | 14 | 2,44,093 | 0 | 0 |
| 01 F2 only | −55.34% | −5,75,528 | 4,46,601 | −27.93% | −59.63% | 2,250 | 33.3% | 0.70 | −0.163 | 14 | 2,37,533 | 0 | 210 |
| 02 F3 only | −46.20% | −4,84,986 | 5,37,997 | −22.26% | −50.24% | 2,239 | 33.4% | 0.75 | −0.169 | 15 | 2,44,614 | 609 | 0 |
| 03 F7b only | −41.58% | −4,40,598 | 5,84,204 | −19.62% | −46.08% | 2,243 | 33.9% | 0.79 | −0.161 | 15 | 2,56,909 | 0 | 0 |
| **05 Corrected (defaults)** | **−53.20%** | **−5,54,241** | **4,67,950** | **−26.55%** | **−57.06%** | **2,270** | **33.3%** | **0.72** | **−0.170** | **13** | **2,47,386** | **551** | **205** |
| 06 Corrected + A1 `trade_through` | −53.20% | identical to 05 | | | | | | | | | | | |
| 07 Corrected + F4 `sma200_proxy` (sensitivity) | −33.95% | −3,55,985 | 6,60,503 | −15.51% | −35.31% | 1,269 | 31.1% | 0.69 | −0.153 | 21 | 1,46,398 | 971 | 104 |

F9 doesn't apply to momentum, which has no relative-strength input.

### VCP (2025-01-13 → 2026-11-20; NIFTY 500 +13.39%)

Every run (baseline, 00-07, including F9-only run 04) gives the same result: −0.76% total return,
−₹7,566 net, end equity ₹9,92,434, CAGR −0.41%, maximum drawdown −1.09%, **2 trades**
(PAYTM, CRAFTSMAN; both stopped out), win rate 0%, average R −0.788, 0 open positions, ₹291 charges.

### Separate impact of each correction

| Correction | Momentum effect | VCP effect | Notes |
|---|---|---|---|
| F2 | −9.38 pp return; 210 same-session stops (205 in the corrected run) | none | Larger than the audit's first-order −6.8% because of compounding and the cash it frees. Neither VCP trade breached on its entry day |
| F3 | −0.24 pp; ₹551-609 of estimated liquidation costs at the end | none (no open positions) | The trade count moved by 44 through sizing on slightly lower equity (path effect, see below) |
| F7b | +4.38 pp return; average R −0.161 vs −0.159 | none | Changed 130 momentum signals across 33 stocks (95 gained, 35 lost) and 86 VCP top-20 rows; no VCP planner-eligible setup changed |
| F9 | n/a | none | Changed 8 of 9,160 VCP top-20 rows; no planner-eligible setup changed |
| A1 | 0 (no touch-only fills) | n/a (STOP_LIMIT) | 98% of momentum entries fill at the open |
| F4 proxy (sensitivity, not adopted) | −53.20% → −33.95%; trades 2,270 → 1,269; average R −0.170 → −0.153 | none (both trades in allowed sessions; 198 sessions blocked) | The loss shrinks mainly because there is less exposure. Average R stays negative under the proxy. Not equivalent to the live gate |

**Reading these numbers:**
- **Use average R, not return, to judge a single correction.** Momentum results are path-dependent:
  - 4,435 of 6,741 planned orders in the corrected run were sized at zero shares because the
    evening's cash ran out.
  - **553 of 2,270 trades (24%) carry under ₹500 of planned risk** against the ₹5,000 target.
    These are leftover-cash positions.
  - A tiny change in equity, such as F3's ~₹600, therefore reshuffles which signals get funded.
  - This is why F3 moves the trade count by 44 and why F7b's +4.4 pp return comes with an
    essentially unchanged average R.
- Average R is negative in every configuration tested. This report does not draw a profitability
  conclusion from that. The universe is survivorship-biased (§8), which flatters results, and the
  regime gate is unresolved (§9).

---

## 7. Remaining limitations

- **F1 survivorship:** universes are still today's index members (§8).
- **F4:** no faithful exposure-coach replay. `sma200_proxy` is a labelled proxy only (§9).
- **STOP_LIMIT entry-day ambiguity (VCP):** this remains a daily-bar limitation under the
  documented next-session policy. Every case is counted; there were 0 in the VCP run.
- **Paper ledger diverges for LIMIT entries.** `india-paper-ledger` still starts exits the
  session after entry, as the audit described, while the backtester now applies the
  entry-session stop. Aligning them is Workstream B.
- **Point-in-time precision:** restated prices carry the 4-decimal rounding of `adjust_bars`
  (≤ ₹0.0001 in the checks). Converted share counts are rounded down, so a few positions after
  odd bonus ratios hold up to one share less than the planner computed.
- **Muhurat sessions:** treated as full sessions (policy `include`). A one-hour bar may fill or
  exit orders that a normal session would treat differently.
- **Path-dependent sizing:** leftover-cash positions dominate trade counts (see §6). This is a
  planner sizing behaviour; it was not changed, and is flagged for a decision.
- **Taxes:** STCG is not modelled.
- **Human chart review:** not simulated.
- **Sweeps:** the baselines' sweeps were not re-run with the corrections.

---

## 8. F1 — Historical universe status (A7)

**Status: not fixed.** The current-universe mode remains and is labelled survivorship-biased in
the method doc, SKILL.md and every report.

**Data availability, checked:**
- **Locally:** only today's NSE index CSVs (`.cache/india-data/universe/`, downloaded 2026-11-22). No historical snapshots.
- **DalalOS** (1 credit, cached at `.cache/india-data/dalalos/list_index_constituents_NIFTY500_2024-06-04.json` and logged in the credit ledger):
  - `list_index_constituents` with `as_of` returns point-in-time membership "from the index membership record, which is complete from 2021-07-01".
  - For NIFTY 500 on 2024-06-04 it reports **500 members**, but the reply was **cut at 400 rows** with no paging parameter.
  - Symbols are given under today's names (for example ETERNAL for Zomato). That makes joining easy but hides renames.
  - `list_index_events` (adds, deletes and segment changes by date, up to 200 per call) is the complementary source.
- **Evidence of bias:** 80 of the 400 returned 2024-06-04 members (20%) are not in today's NIFTY 500. Examples: 3MINDIA, ABFRL, BLUEDART, CAMPUS, EQUITASBNK, JUSTDIAL, RAYMOND, SANOFI.

**Assessment:** usable in principle, but not yet proven complete. Before relying on it:
1. Extract complete membership with small queries, or rebuild it from today's list plus `list_index_events`.
2. Reconcile against NSE's semi-annual reconstitution press releases (niftyindices.com).
3. Fetch free NSE bars for the departed stocks; they are not in the cache.

**Recommendation:**
- Build a dated membership table for NIFTY 500 (and NIFTY 50/100) from about 2023-12 onward.
- Estimated cost: 2-4 DalalOS calls for events plus a handful of `as_of` checks, then about 100-150 free NSE history downloads.
- Then screen each session on that day's members. Keep the current-universe mode for comparison.
- This is a separate, approval-gated piece of work.

---

## 9. Unresolved policy decisions (need your approval)

1. **F4 regime gate.** Which mode should research reports use by default?
   - Options:
     - (a) keep `no_gate`, as now
     - (b) build a faithful `exposure_file` by replaying `india-exposure-coach --as-of` per session; possible only from about 2025-10 onward with NSE data, and longer only with more index and VIX history
     - (c) adopt `sma200_proxy` as a declared research rule. That would be a new trading assumption, not the live policy.
   - I recommend (a) as the default, plus (b) limited to the window where the coach's inputs exist.
2. **Paper-ledger alignment (Workstream B).** Apply the same LIMIT entry-session stop to `india-paper-ledger`, which the Workstream B plan proposes. Until then, backtest and ledger behave differently for momentum entries.
3. **Leftover-cash positions.** Should the planner or backtest keep funding positions far below the risk target (24% of momentum trades carry under ₹500 of risk)? This is a sizing-rule change and was not made.
4. **Live VCP screener RS alignment.** The fix changes live screener output whenever stock and benchmark calendars differ (8 of 9,160 backtest rows changed). Approve `date` as the default, or keep `offset` for live runs.
5. **Muhurat policy.** `include` was chosen (status quo). The alternative is to treat Muhurat sessions as no-trade sessions.
6. **F1 data work.** Approve the DalalOS credits and the implementation for point-in-time universes (§8).

---

## 10. Workstream A gate

- [x] Baselines preserved; checksums recorded and re-verified; originals untouched
- [x] Baselines reproduce exactly under `--legacy`
- [x] F2, F3, F7b, F9 fixed with regression tests; F4 plumbing and modes added; A1 sensitivity mode added
- [x] No strategy selection rule, score, threshold or parameter changed
- [x] Paper-ledger code untouched; no broker orders; no installs
- [x] All 654 tests pass (original 597 unchanged)
- [ ] **Your review and approval of Workstream A** (and decisions in §9)
- [ ] Commit (after approval; needs repackaging and the full pre-commit run)
- [ ] Workstream B (not started)
