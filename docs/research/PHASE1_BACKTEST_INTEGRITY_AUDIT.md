# Phase 1 — Local Backtester Integrity Audit

**Scope:** `india-swing-backtester` and the code it replays (`india-vcp-screener`,
`india-stockbee-momentum-burst-screener`, `india-breakout-trade-planner`,
`india-paper-ledger`, vendored `_india_data.py`).
**Date:** 2026-11-24 · **Branch:** `india-layer` at `d47679fe` (clean working tree, no stashes,
in sync with `private/india-layer`).
**Mode:** audit only. No code, parameters, caches, reports or ledger state were changed; nothing
was committed or pushed. The only file created is this report.

---

## 1. Executive summary

- **Tests:** all six relevant suites were executed and pass. **597 tests, 597 passed,
  0 failed, 0 skipped, 0 errors.** Passing tests do not cover several of the concerns
  below (see §7).
- **No look-ahead was found in the screens.** Each session is screened only on bars
  dated on or before it, and VCP pivots come from causal swing detection. Back-adjusted
  prices still leak some hindsight (F7b).
- **Four confirmed defects:**
  1. **F2 (High): momentum-burst entry-day stops are skipped although they certainly
     happened.** A buy limit can only fill at the open or on the way down through the
     limit, so when the entry day's low is under the stop, the stop was hit after the
     fill. 193 of 2,195 NIFTY 500 momentum trades (8.8%) are like this. A first-order
     estimate says the reported result is about **₹68k (6.8% of starting capital) too
     favourable**.
  2. **F4 (Medium–High): the market regime is recorded but never used as an entry
     gate.** The documented live workflow says to honour the regime check. The
     momentum market gate is fixed at `neutral` for every session, and neither the
     method doc nor the report discloses the omission.
  3. **F7b (Medium): screening uses prices back-adjusted for later splits and bonuses.**
     The fixed ₹ and share-count thresholds (₹50 minimum price, ₹25 rupee breakout,
     1 lakh / 50 lakh share volume, NSE tick bands) are therefore applied to prices and
     volumes that were never quoted. 65 of ~500 universe stocks had a split or bonus in
     the momentum window, some by a factor of 10. The method doc describes this bias
     with the wrong direction.
  4. **F9 (Low): relative strength pairs stock and index closes by row position, not
     date.** The Yahoo index backfill is missing 2024-02-19, and Muhurat one-hour
     sessions are simulated as full sessions.
- **One confirmed minor defect:** F3. Open positions are valued without their selling
  charges, which is about 0.06% of capital at the end of the momentum run.
- **Known and documented:** F1, survivorship from using today's index members (the
  biggest unquantified bias), and the daily-bar ambiguity for stop-limit entries (F5).
- **Bias direction:** every defect whose direction could be measured (F1, F2, F3, A1)
  makes reported results look **better** than reality. F4 and F7b have unknown
  direction. This report makes no claim that any strategy is or is not profitable.

---

## 2. Environment and commands

| Item | Value |
|---|---|
| OS | macOS (Darwin 27.3.0) |
| Python | 3.13.14 (`.venv/bin/python`, repository virtualenv) |
| pytest | 9.2.0 |
| Dependencies | Verified with `ci_test_matrix.py install --check <id>`, which only checks and does not install; all satisfied. Nothing was installed or upgraded. |

The documented runner `scripts/run_all_tests.sh` wraps every call in
`uv run --extra dev --extra ci`, which syncs the virtualenv and may install packages.
To respect the "no install" rule, the same CI matrix script was called directly with the
existing interpreter. Its `run` subcommand executes exactly the CI pytest command
(`scripts/ci_test_matrix.py:624-625`):

```bash
.venv/bin/python scripts/ci_test_matrix.py install --check <skill-id>   # verify only
.venv/bin/python scripts/ci_test_matrix.py run <skill-id>
#   = python -m pytest skills/<skill-id>/scripts/tests --tb=short -q -rfEs
.venv/bin/python -m pytest scripts/tests/test_india_data.py -q --tb=short -rfEs
```

Evidence checks (§5) were read-only Python scripts over `.cache/india-data/` and the
existing reports in `reports/`. No backtest was re-run.

---

## 3. Test results

| Suite (test path) | Collected | Passed | Failed | Skipped | Errors | Time |
|---|---:|---:|---:|---:|---:|---:|
| `skills/india-swing-backtester/scripts/tests` | 130 | 130 | 0 | 0 | 0 | 2.29 s |
| `skills/india-vcp-screener/scripts/tests` | 82 | 82 | 0 | 0 | 0 | 0.24 s |
| `skills/india-stockbee-momentum-burst-screener/scripts/tests` | 71 | 71 | 0 | 0 | 0 | 0.24 s |
| `skills/india-paper-ledger/scripts/tests` | 160 | 160 | 0 | 0 | 0 | 0.71 s |
| `skills/india-breakout-trade-planner/scripts/tests` (dependency) | 99 | 99 | 0 | 0 | 0 | 0.24 s |
| `scripts/tests/test_india_data.py` (shared data module) | 55 | 55 | 0 | 0 | 0 | 0.23 s |
| **Total** | **597** | **597** | **0** | **0** | **0** | |

There are no failure tracebacks because nothing failed. Coverage was not measured.

---

## 4. Findings

Severity is about the **reliability of reported historical performance**: High = can
move the headline result by several percent of capital or change conclusions;
Medium = plausibly material, unquantified; Low = small or cosmetic.

### Summary table

| # | Concern | Classification | Severity |
|---|---|---|---|
| F1 | Current NIFTY universe reused historically | Known limitation / documented | High (unquantified) |
| F2 | Entry-day stop/target/time exits skipped | **Confirmed defect** for LIMIT entries (momentum); known limitation for stop entries | **High** |
| F3 | Open positions valued without liquidation charges | **Confirmed defect** | Low |
| F4 | Regime recorded, not enforced as entry gate | **Confirmed defect** (undisclosed gap to the documented live workflow) | Medium–High |
| F5 | Daily OHLC ambiguity: entry and stop both touched | Known limitation / documented (stop entries, exit bars); limit-entry case escalated to F2 | Low (after F2) |
| F6 | Pending orders around missing bars, suspensions, expiry | Not an issue (current settings); test gap | Low |
| F7a | Split/bonus/dividend P&L for open positions | Not an issue (verified arithmetic); test gap | Low |
| F7b | Screening thresholds on back-adjusted prices/volumes | **Confirmed defect** (hindsight; doc wrong) | Medium |
| F8 | VCP pivot confirmation / look-ahead | Not an issue (no look-ahead found) | — |
| F9 | Stock/benchmark date alignment | **Confirmed defect** | Low |
| A1 | Limit orders fill on a touch (extra finding) | Potential issue requiring further evidence | Low–Medium |

### F1 — Current universe reused throughout the backtest

- **Classification:** Known limitation / documented. **Severity:** High (unquantified).
- **Evidence:**
  - The universe is loaded once from today's NSE index CSV
    (`skills/india-swing-backtester/scripts/india_swing_backtest.py:523`).
  - Every session iterates the same `data.rows`
    (`swing_backtest_engine.py:135`, `:191`).
  - Sector labels for the 30% sector cap also come from today's file
    (`swing_backtest_engine.py:675`).
  - The VCP relative-strength percentile ranks a stock only against today's members
    (`screen_india_vcp.py`, `rank_relative_strength_universe` in `run_funnel`).
  - It is documented in the report caveats (`india_swing_backtest.py:395-396`) and the
    method doc (`references/india-swing-backtest-method.md:36`, `:42`).
- **Data:** in the cache, 69 of 500 universe stocks first trade after 2024-06-04. Those
  enter correctly when they list. Stocks that **left** the index during the window are
  absent entirely.
- **Impact:** flatters results. Stocks that fell out of NIFTY 500 are disproportionately
  losers, and today's winners are over-represented. The size is unknown without
  point-in-time constituent lists.

### F2 — Entry-day exits are skipped

- **Classification:**
  - **Confirmed defect** for LIMIT entries (momentum burst).
  - Known limitation for STOP_LIMIT entries (VCP).
- **Severity:** High.
- **Evidence:**
  - Exits run before entries in each session, and positions entered on day D are
    skipped on D (`swing_backtest_engine.py:482`, `if bar is None or
    pos["entry_session"] >= day: continue`; loop order at `:753-755`).
  - This is intended and tested (`tests/test_swing_backtest_engine.py:121`
    `test_no_exit_on_entry_day`).
  - It is documented in the method doc (line 7, "Positions entered before D") and in the
    report ("Exits start the session after entry", `india_swing_backtest.py:387-388`).
  - The live paper ledger behaves the same but warns
    (`skills/india-paper-ledger/scripts/paper_ledger.py:1169-1177`: "daily bars cannot
    show the order of events, so exits start next session").
- **Why the ambiguity argument fails for LIMIT buys:** a buy limit fills at the open
  (when open ≤ limit) or at the limit as the price falls through it
  (`paper_engine.py:65-70`). The stop is below the limit. So if the session's low is at
  or below the stop, the price traded through the stop **after** the fill. A real
  stop-loss order would have been triggered. The order of events is known, not
  ambiguous.
- **Measured on the existing momentum NIFTY 500 run** (2024-06-04 → 2026-11-20, 2,195
  closed trades, bars from the shared cache, back-adjusted as in the backtest):
  - 193 trades (8.8%) had entry-day low ≤ stop.
  - Simulated: 173 later exited by stop and 20 by time stop. Net ₹-199,125, average R
    -0.469.
  - Counterfactual stop on the entry day (at the stop, or the open if lower, less
    10 bps slippage, same charges): net ₹-267,585, average R -0.572.
  - **Difference: ₹-68,459 ≈ -6.8% of the ₹10 lakh starting capital.** The reported
    -45.96% is too favourable. This first-order estimate ignores compounding, freed
    cash, heat and re-entries, so treat it as a direction and rough magnitude only.
- **VCP (live rules, NIFTY 500):** 0 of 2 trades affected.
- **Time exit on entry day:** not reachable, because `max_hold_sessions >= 1` is
  enforced (`swing_backtest_engine.py:359-360`).
- **Constraint note:** the paper ledger has the same behaviour. Changing the ledger is
  out of scope without approval; a backtester-only fix would make the two diverge.

### F3 — Open-position valuation excludes liquidation charges

- **Classification:** Confirmed defect. **Severity:** Low.
- **Evidence:**
  - Mark-to-market is `cash + qty × close` with no selling charges
    (`swing_backtest_engine.py:456-460`).
  - The final equity, and therefore total return, CAGR and drawdown, uses this value
    for any position still open at the end.
  - `open_positions[].unrealized` is `qty × (close − entry_price)`, which ignores buy
    and sell charges (`swing_backtest_engine.py:779-781`).
- **Measured:**
  - Momentum NIFTY 500 run: 14 open positions worth ₹3.57 lakh at the end. Estimated
    selling charges (0.1% STT, about 0.01% fees, ₹15.93 DP each) come to about ₹616,
    or **0.06% of capital**.
  - VCP NIFTY 500 run: no open positions.
- **Impact:** slightly flatters final equity and intra-run drawdowns. It matters more
  for short windows or long holds.

### F4 — Regime is recorded but not enforced as an entry gate

- **Classification:** Confirmed defect (a fidelity gap to the documented live workflow,
  not disclosed). **Severity:** Medium–High.
- **Evidence:**
  - `_regime()` (`swing_backtest_engine.py:441-446`, NIFTY 500 vs its 200-day average)
    is used only to label positions (`:598`) and to group results
    (`swing_backtest_metrics.py:164`). No code path blocks or resizes entries by
    regime.
  - Momentum: the screener's `--market-gate` defaults to `neutral`
    (`screen_india_momentum_burst.py:831-833`), and the backtest passes the same value
    for every historical session. The live skill tells the user to pass the day's
    regime decision (`india-stockbee-momentum-burst-screener/SKILL.md:33-36`). With
    `restrictive`, strong setups become `MANUAL_REVIEW_ONLY` and would not be traded.
  - VCP: the live workflow says to honour the regime check
    (`india-vcp-screener/SKILL.md:39`) and to run `india-exposure-coach` first, turning
    plans into a watchlist under `REDUCE_ONLY` / `CASH_PRIORITY`
    (`india-breakout-trade-planner/SKILL.md:47`). The backtest does neither.
  - Neither the report caveats nor the method doc's "Known biases" table mentions the
    omission.
- **Descriptive data from the existing momentum NIFTY 500 report** (not a profitability
  claim):

  | Regime at entry | Trades | Average R |
  |---|---:|---:|
  | Above the 200-day average | 1,141 | -0.200 |
  | Below the 200-day average | 686 | -0.125 |
  | Unknown (first ~200 sessions, too little index history) | 368 | — |

  A simple 200-day filter alone would not have turned average R positive in this
  sample. The live exposure coach uses more inputs (breadth, distribution days, FTD,
  VIX, valuation) and was not replayed, so its effect is unknown.

### F5 — Daily OHLC ambiguity when entry and stop are both touched

- **Classification:** Known limitation / documented. The limit-entry case is not
  ambiguous and is covered by F2. **Severity:** Low once F2 is handled.
- **Evidence:**
  - **STOP_LIMIT entries (VCP):** a fill at the trigger on the way up can come before
    or after the session low. A daily bar cannot tell which, so this is a genuine
    ambiguity. The ledger's policy is to warn and start exits next session
    (`paper_ledger.py:1172-1177`). 0 VCP live-rules trades were affected.
  - **Exit bar touching both stop and target:** the stop is assumed first
    (`paper_engine.py:93-104`). This is documented as a pessimistic bias (method doc
    line 44) and tested (`test_bar_touching_stop_and_target_counts_as_stop`).
  - **STOP_LIMIT gap above the cap:** if the open is above the limit, there is no fill,
    even though the price could later fall back under the limit
    (`paper_engine.py:71-75`). This is a conservative choice and is tested
    (`test_gap_past_limit_does_not_fill`).

### F6 — Pending orders around missing bars, suspensions and expiry

- **Classification:** Not an issue for current settings. Test gap. **Severity:** Low.
- **Evidence:**
  - **Orders:** a session with no bar for the stock uses up one session of validity,
    then the order expires (`swing_backtest_engine.py:543-550`).
  - **Locked sessions:** one-price circuit sessions don't fill (`:552-553`; tested).
  - **Gaps:** gaps past the limit are counted and expire (`:558-559`; tested).
  - **Cash:** an order is rejected if cash is short at fill time (`:575-577`).
  - **Validity:** `valid_sessions` defaults to 1, so an order never waits across a
    long gap.
  - **Held positions:** with no bar on a day, nothing happens: no exit, and the
    time-stop counter doesn't advance (`:482-484`). The position is marked at the last
    available close (`:437-439`). A long-suspended or delisted holding would stay open
    at a stale price. This is unlikely under F1, because delisted names are not in the
    universe.
  - **Missing test:** no test exercises the missing-bar (`orders_suspended`) path or
    `valid_sessions > 1`.
- **Data note:** `TATACAP` has pre-IPO bars from 2024-01-09 to 2024-05-03, then nothing
  until 2025-10-13. Both screeners use calendar-day windows (VCP 430 days, momentum
  about 158 days), so the stale bars never entered a screen. Its first momentum
  appearance is 2025-11-19, and it never appears in a VCP screen. No contamination was
  found, but nothing guards against it explicitly.

### F7a — Split, bonus and dividend handling for open positions (P&L)

- **Classification:** Not an issue (arithmetic verified). Test gap. **Severity:** Low.
- **Evidence:**
  - Whole series are back-adjusted once with every action up to the cache's coverage
    end: price × factor, volume ÷ factor (`india_swing_backtest.py:85-88`;
    `_india_data.py:561-579`).
  - Share counts and prices therefore share one unit system for the whole run. A split
    during a hold causes no artificial jump, and rupee P&L, turnover and charges are
    unchanged.
  - Dividends are credited as `dps × adj_factor × qty` on the ex-date
    (`swing_backtest_engine.py:466-478`). Since `qty` is in today's units and
    `adj_factor` is the price multiplier, `qty × adj_factor` equals the shares actually
    held then, which is correct.
- **Missing test:** the dividend test only covers `factor = 1.0`
  (`tests/test_swing_backtest_engine.py:160`).
- **Note:** for real held positions, the live ledger **blocks** on splits and bonuses
  rather than adjusting them. This is a deferred user decision and outside the
  backtester.

### F7b — Screening thresholds applied to back-adjusted data

- **Classification:** Confirmed defect: hindsight from future corporate actions leaks
  into past signal selection, and the documentation is wrong. **Severity:** Medium.
- **Evidence:**
  - Before a later split or bonus, back-adjusted prices are lower and volumes higher
    than what was actually quoted.
  - Thresholds stated in absolute ₹ or shares therefore misfire on those dates:
    - ₹50 minimum price (`screen_india_vcp.py:369`, `:604`;
      `screen_india_momentum_burst.py:803`)
    - ₹25 rupee-breakout trigger (`:812`, `:595-599`)
    - 1 lakh minimum shares (`:806`)
    - 50 lakh "high volume" tag (`:819`)
    - NSE tick bands used to round entry, stop and limit prices (planner,
      `swing_backtest_engine.py:694-695`)
  - Rupee-turnover gates are unaffected.
- **Measured:**
  - 65 of 500 cached universe stocks had a split or bonus between 2024-06-04 and
    2026-11-20.
  - Cumulative price factors go as low as 0.1 (PGEL, TATAINVEST, ANGELONE, BAJFINANCE).
  - Example: MSUMI closed at ₹64.47 as quoted just before its action, but ₹42.98
    adjusted. The ₹50 gate excludes it with hindsight.
- **Documentation error:** the method doc says "₹50 minimum price can admit a stock that
  was then above it" (`references/india-swing-backtest-method.md:43`). For splits and
  bonuses the effect is the opposite (stocks are wrongly **excluded**), and the
  volume and rupee-trigger effects are not mentioned.
- **Impact:** signal selection differs from what a trader would have seen on about 13%
  of names before their actions. The direction for performance is unknown.

### F8 — VCP pivot confirmation and possible look-ahead

- **Classification:** Not an issue (no look-ahead found).
- **Evidence:**
  - The screen inputs are cut off at D. Stock bars are
    `series.window(start, day)` (`swing_backtest_engine.py:141`), and unexplained-jump
    warnings are filtered to dates up to D (`:145`).
  - The index window also ends at D (`:151`).
  - Swing highs and lows are confirmed only by later bars inside that window. The
    ATR-ZigZag is causal (`vcp_pattern_calculator.py:264-339`).
  - Pivot = high of the last contraction (`:634-644`).
  - Plans are made after the session's mark-to-market and fill from D+1 at the
    earliest (`swing_backtest_engine.py:753-768`).
  - Tests: `test_screen_day_never_sees_later_bars` (`test_swing_backtest_engine.py:221`)
    and the live-parity tests `test_screen_day_matches_live_screener`
    (`test_india_swing_backtest.py:83`) and its momentum twin
    (`test_swing_backtest_momentum.py:233`).
- **Caveats:**
  - Hindsight still enters through F1 (the RS percentile is computed among survivors)
    and F7b (adjusted prices).
  - "Pivot confirmation" is by price only: no breakout-volume check before the order.
    This matches the live planner, which is end-of-day only, and is documented in the
    planner's SKILL.md overview.
  - The no-look-ahead test covers `screen_day`, not a whole simulation run (see T18).

### F9 — Stock/benchmark date alignment

- **Classification:** Confirmed defect. **Severity:** Low.
- **Evidence:**
  - Relative strength computes period returns by row offset
    (`closes[0]` vs `closes[63/126/189/252]`) separately for the stock and the index
    (`relative_strength_calculator.py:65-78`, `:136-140`), with no date join. Any
    calendar difference shifts the comparison.
  - The simulated session calendar is the benchmark's dates
    (`swing_backtest_engine.py:87-89`).
- **Measured** (benchmark = Yahoo backfill 2024-01-02 → 2024-12-31 plus NSE from
  2025-01-01, 710 sessions):
  - One session that the majority of stocks traded is missing from the benchmark:
    **2024-02-19** (Yahoo backfill). It is outside both test windows, so it is not
    simulated, but VCP screens whose 252-bar look-back spans February 2024 (screens up
    to about February 2025) compare 12-month returns one session apart.
  - Stocks with their own gaps (suspensions, TATACAP) misalign further.
  - The backfill check passed: 32 overlap sessions, maximum close difference 0.0.
- **Muhurat sessions:** the one-hour Diwali sessions on 2024-11-01 and 2025-10-21 are
  in both stock and index calendars. They are simulated as normal sessions: fills and
  exits use the one-hour bar, and they count toward the 5-session momentum time stop.
  The shared module has `drop_muhurat_sessions` (`_india_data.py:647-657`), but the
  backtester doesn't use it.

### A1 — Limit orders fill on a touch (additional finding)

- **Classification:** Potential issue requiring further evidence.
  **Severity:** Low–Medium.
- **Evidence:** `entry_fill` fills a LIMIT buy at the limit whenever
  `low <= limit` (`paper_engine.py:68-69`). That assumes the order was filled the moment
  the price touched the limit, ignoring queue position. This is optimistic for every
  momentum entry that opened above the limit and filled on a dip. The trade CSV doesn't
  record the entry fill basis, so the share of such fills could not be measured. The
  method doc does not mention it.

---

## 5. Potential impact on historical performance

| Finding | Direction on reported results | Estimated size |
|---|---|---|
| F1 Survivorship | Flatters | Unknown, likely the largest |
| F2 Entry-day stops (momentum) | Flatters | About +6.8% of capital (first-order, NIFTY 500 run) |
| F3 Unliquidated open positions | Flatters | About +0.06% of capital (momentum NIFTY 500) |
| F4 No regime gate | Unknown | Unknown; a simple 200-DMA split shows negative average R in both buckets |
| F7b Back-adjusted thresholds | Unknown | Affects about 13% of universe names before their actions |
| F9 Date alignment / Muhurat | Negligible to small | 1 benchmark session; 2 Muhurat sessions |
| A1 Touch fills | Flatters | Unknown |
| No STCG tax (documented) | Flatters | Not modelled |
| Stop-first on two-sided bars (documented) | Pessimistic | Small for momentum (no target) |

Every bias with a known direction except the documented stop-first rule makes the
reported results **more favourable** than reality. Correcting them is not expected to
improve the reported momentum and VCP results. This is a statement about the direction
of measured biases, not a profitability verdict, which needs corrected re-runs.

---

## 6. Recommended fixes (priority order; none implemented)

1. **F2 — Same-session stop for LIMIT entries.** In the backtester, after a LIMIT fill,
   evaluate the stop on the same bar: exit at the stop, or at the open when the fill was
   at the open and the open is under the stop. Keep the current policy for STOP_LIMIT
   entries and report how many fills were ambiguous. Whether the paper ledger should
   change the same way is a separate decision that needs approval, because it changes
   live paper behaviour.
2. **F4 — Replay the regime gate.** Add a per-session regime input for the momentum
   `--market-gate` and the VCP planner gate. Options: replay `india-exposure-coach` on
   historical NSE data, or use a documented proxy such as NIFTY 500 vs its 200-day
   average. Keep a "no gate" mode for comparison, and disclose the mode in every report.
3. **F1 — Point-in-time universe.** Build historical NIFTY 50/100/500 membership from
   NSE index reconstitution notices and screen each session only on that day's members.
   Until then, report a sensitivity run (for example the universe as of the window
   start, where obtainable) and keep the survivorship caveat prominent.
4. **F7b — Point-in-time adjustment for screening.** When screening session D, adjust
   bars only for actions with ex-date ≤ D, so ₹ and share thresholds see prices as
   quoted. Simulation P&L can keep using the fully adjusted series. Correct the method
   doc (line 43).
5. **F9 — Date-aligned relative strength and calendar checks.** Join stock and index by
   date for RS. Detect benchmark sessions missing from the stock calendar and fill them
   from another source (2024-02-19). Decide on and test a Muhurat policy (drop them, as
   the breadth skills do, or treat them as no-fill sessions).
6. **F3 — Net valuation.** Deduct estimated selling charges in mark-to-market and final
   equity, and report unrealized P&L net of buy and sell charges.
7. **A1 — Fill realism.** Add a conservative option for touch fills (require
   `low < limit` by one tick, or a fill haircut) as a sensitivity run.
8. **F6/F7a — Test gaps.** Add the regression tests below. No behaviour change is
   expected.

---

## 7. Regression tests required before strategy optimisation

None of these exist today unless noted.

| ID | Test (proposed name) | Guards |
|---|---|---|
| T1 | `test_limit_entry_exits_same_session_when_low_breaches_stop` | F2 |
| T2 | `test_limit_entry_filled_at_open_below_stop_exits_at_open` | F2 |
| T3 | `test_stop_limit_entry_same_session_breach_follows_documented_policy` (flagged and counted) | F2/F5 |
| T4 | `test_bar_touching_stop_and_target_counts_as_stop` (exists, keep) | F5 |
| T5 | `test_final_equity_deducts_estimated_liquidation_charges` | F3 |
| T6 | `test_unrealized_pnl_is_net_of_buy_and_sell_charges` | F3 |
| T7 | `test_regime_gate_blocks_new_entries_but_not_exits` (both strategies) | F4 |
| T8 | `test_momentum_market_gate_is_per_session_not_constant` | F4 |
| T9 | `test_report_discloses_regime_mode` | F4 |
| T10 | `test_universe_is_point_in_time` (member added after D not screened on D; removed member screened before removal) | F1 |
| T11 | `test_screen_thresholds_use_prices_as_quoted_before_later_split` (₹50 gate, ₹25 rupee trigger, share-volume gates) | F7b |
| T12 | `test_tick_rounding_uses_point_in_time_price_band` | F7b |
| T13 | `test_dividend_before_later_split_credits_shares_held_then` (`adj_factor` ≠ 1) | F7a |
| T14 | `test_position_across_split_ex_date_pnl_matches_unadjusted_calculation` | F7a |
| T15 | `test_missing_bar_consumes_order_validity` (`valid_sessions` = 1 and 2) | F6 |
| T16 | `test_held_position_without_bars_is_not_time_stopped_and_marked_at_last_close` | F6 |
| T17 | `test_stale_pre_listing_bars_never_enter_a_screen` (TATACAP-shaped gap) | F6 |
| T18 | `test_full_run_is_invariant_to_bars_after_each_session` (perturb all future bars; orders and fills up to D unchanged) | F8 |
| T19 | `test_relative_strength_aligns_by_date_when_benchmark_misses_a_session` | F9 |
| T20 | `test_benchmark_calendar_gap_is_detected` (for example 2024-02-19) | F9 |
| T21 | `test_muhurat_session_policy` | F9 |
| T22 | `test_limit_touch_fill_policy` (touch vs trade-through option) | A1 |
| T23 | `test_screen_day_matches_live_screener` / momentum twin (exist, keep green after every fix) | parity |

---

## 8. Limitations of this audit

- **Method:** static code review plus targeted, read-only data checks. No fixes were
  made and no backtest was re-run, so corrected performance figures do not exist yet.
- **Estimates:** the F2 counterfactual is first-order. It recomputes P&L only for the
  affected trades and ignores compounding, cash and heat interactions, and the trades
  that freed cash would have enabled. Treat it as a direction and rough size.
- **Report vintage:** the evidence uses reports and screen caches produced on
  2026-11-23 by the current code (commit `bd2e4ed1`, content-identical to the
  rewritten `78041048`). They were not regenerated.
- **Not audited in depth:**
  - metric formulas (CAGR, Sharpe, drawdown)
  - the screeners' scoring logic beyond the look-ahead and threshold questions
  - `india-exposure-coach`
  - charge rates
  - the accuracy of NSE bhavcopy data beyond internal consistency
  - the correctness of Yahoo data beyond the stored overlap check
- **VCP sample:** the VCP NIFTY 500 live-rules run has 2 trades, so no performance
  conclusion can be drawn from it either way.
- **Unmeasured:** F1 (no historical constituent data was available locally), F4's
  effect under the real exposure-coach rules, and A1 (the fill basis isn't stored in
  the trade CSV).
- **Test results:** these describe the six suites listed. Other repository suites were
  not run.
