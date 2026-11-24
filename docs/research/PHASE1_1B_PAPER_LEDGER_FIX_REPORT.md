# Phase 1.1 — Workstream B: Paper Ledger Integrity Audit and Fixes

**Branch:** `india-layer` (HEAD `d47679fe`) · **Date:** 2026-11-24
**Inputs:** `PHASE1_BACKTEST_INTEGRITY_AUDIT.md`, `PHASE1_1A_BACKTESTER_FIX_REPORT.md`
**Status:** implemented and tested in the working tree. **Not committed or pushed.** No
historical ledger data was modified, no migration was performed, and no broker or live order
was placed.

---

## 1. Executive summary

- **What was fixed:** the paper ledger now applies the approved F2 rule. A **LIMIT** buy whose
  own session low reaches its intraday stop is stopped out **that session**:
  - at the open, if the open was already at or below the stop;
  - otherwise at the stop;
  - less the account's stop slippage, with normal sell charges.
- **Parity with the backtester:** the ledger produces the same exit prices on identical bars.
  A real case replayed through the CLI matches it to the paisa in cash: CHAMBLFERT on
  2024-07-09, entered at ₹542.00 and exited at ₹518.48.
- **What is unchanged:**
  - STOP and STOP_LIMIT entries keep the documented next-session policy and warning, because a
    daily bar cannot show whether their low came before the trigger.
  - CLOSE orders and close-basis stops are also unchanged.
- **Change size:** one source file (`paper_ledger.py`, +87/−26 lines), documentation, 20 new
  tests, and a legacy-state fixture captured from the unchanged code.
- **Compatibility:** no schema change. The only new field (`exit_timing`) is additive and
  appears only on same-session exits.
- **Tests:**
  - The six audit suites pass: **674 tests** (654 after Workstream A plus 20 new), 0 failed,
    0 skipped.
  - The five suites that read ledger state pass as well: 1,057 tests.
- **Found but not changed:** four issues and one decision item (§12), including:
  - The ledger reports **gross** equity.
  - The ledger **skips** Muhurat sessions, unlike the backtester.

---

## 2. Initial state

- **Commit:** `d47679fe`, branch `india-layer`, in sync with `private/india-layer`.
- **Working tree at start:** Workstream A's uncommitted changes (9 modified, 4 new files).
  - I fingerprinted these with SHA-256 before starting.
  - At the end, all 13 match: **Workstream A files were not modified.**
- **Paper-ledger code:** unchanged from HEAD at the start.
- **Real ledger state:** none. `state/india-paper/` does not exist; `state/` holds only
  `journal/.gitkeep` and `theses/.gitkeep`. So there were no historical records to migrate or
  protect on this machine. Backward compatibility was tested against a fixture instead (§10).

---

## 3. Defects found

| # | Finding | Evidence | Action |
|---|---|---|---|
| **D1** | **F2: LIMIT entry-session stop ignored.** After a LIMIT buy fills, the position is created with `last_evaluated` set to the fill session, so `run_exits` skips it that session. A stop touch on that bar only produces a warning ("daily bars cannot show the order of events, so exits start next session"). A LIMIT filled at an open below its stop is labelled a gap fill whose "stop will trigger next session". | `paper_ledger.py` `_buy` (pre-change lines 1105-1110, 1143-1157, 1169-1177); `run_exits` skip at 858 | **Fixed** (§6) |
| **D2** | **Documentation** says exits always start the session after entry. | `references/fill_rules.md` lines 30-37; SKILL.md fill-rules bullets | **Fixed** |
| S1 | **Reported equity is gross.** `snapshot()` computes equity = cash + gross market value; only `unrealized_net` deducts estimated selling charges. `return_pct` and `exposure_pct` use the gross figure. The backtester now values net by default. | `paper_ledger.py:772` | **Not changed** (accounting change, outside F2) |
| S2 | **Muhurat sessions skipped.** `process()` drops 2024-11-01 and 2025-10-21 bars, so no fills or exits happen on them. The backtester simulates them (approved policy `include`). | `paper_ledger.py:693` | **Not changed** (policy decision) |
| S3 | **Thesis update before journal write.** Every exit (`_sell`) and entry (`_buy`) updates the trader-memory-core thesis *before* appending the journal line. A crash between the two would make a re-run try to close or open the thesis again and fail with a lifecycle error. This is pre-existing in all paths; the new code reuses them and adds no new window. | `_sell` 943-981, `_buy` 1134-1170 | **Not changed** (documented) |
| S4 | **P&L rounding.** `realized_pnl` comes from 4-decimal effective per-share prices × quantity, so it can differ from the cash movement by a paisa or two. CHAMBLFERT: −₹4,783.08 in the journal vs −₹4,783.07 in cash. | `_sell` 933 | **Not changed** (pre-existing, cosmetic) |
| S5 | **Close-basis LIMIT entries.** The approved F2 semantics describe intraday stops (open/low). For a close-basis stop the entry-day close also comes after a LIMIT fill, but applying it would be a new rule. | — | **Decision needed** (§12); kept on the next-session policy |

The remaining items on the checklist were audited and found consistent:
- Entry fill logic (`paper_engine.entry_fill`)
- Pending-order validity and expiry
- Cash checks at fill
- Buy and sell charges booked once each (event IDs `fill:<order>` / `exit:<thesis>:<session>`)
- DP charge on every sell leg only
- Stop slippage on stop exits only; the target exits at its price with no slippage
- Position deletion on full exit
- Idempotent re-runs
- Corporate-action blocking
- Order-placement paths (§9)

---

## 4. Files changed

| File | Change |
|---|---|
| `skills/india-paper-ledger/scripts/paper_ledger.py` | New `_SessionContext._entry_session_stop(order, bar)` (line 1089); `_buy` computes it (1115), suppresses the now-wrong "stop will trigger next session" warning for these fills (1129), and after the fill executes the stop through the existing `exit_fill` + `apply_slippage` + `_sell` path (1192-1213); `_sell` gains an optional `exit_timing` keyword (928) that adds `"exit_timing": "entry_session"` to the journal line and the run event only when set (978-990). No other behaviour changed |
| `skills/india-paper-ledger/references/fill_rules.md` | Gap-fill note and an "Exit Fills" exception section for LIMIT entries; buy-stop, close-order and close-basis policies stated explicitly |
| `skills/india-paper-ledger/SKILL.md` | Fill-rules bullet updated; "same-session limit stops" added to the warnings to surface |
| `docs/en/skills/india-paper-ledger.md` | Regenerated (`generate_skill_docs.py --skill india-paper-ledger --overwrite`). The generator's known bogus catalog row was reverted; `--check` shows 0 drift |
| `skills/india-paper-ledger/scripts/tests/test_entry_session_stop.py` | **New**, 20 tests |
| `skills/india-paper-ledger/scripts/tests/fixtures/legacy_v1_state/` | **New**: a ledger state directory written by the **unchanged** code (account, orders, positions, journal, three trader-memory-core theses). The only edit is an inline `# pragma: allowlist secret` comment on the three YAML `origin_fingerprint` hash lines (detect-secrets false positives); the data is unchanged |
| `docs/research/PHASE1_1B_PAPER_LEDGER_FIX_REPORT.md` | This report |

**Not changed:**
- `paper_engine.py`, `india_paper_ledger.py` (CLI), the vendored `_india_data.py`
- trader-memory-core
- every Workstream A file (verified by checksum)
- strategy, sizing and risk rules
- `requirements.txt`

---

## 5. Before / after execution semantics

Order: LIMIT buy at ₹102.00, stop ₹95.00, 100 shares, account slippage 10 bps,
DP ₹15.93, brokerage ₹0. Fill session bar = open/high/low/close.

| Fill-session bar (O/H/L/C) | Before | After |
|---|---|---|
| 101 / 102 / 94 / 96 (fills at the open, low breaches) | Bought ₹101.00; warning "stop … also touched on the entry day … exits start next session"; held overnight | Bought ₹101.00 and **sold ₹94.91 the same session** (stop less 10 bps); thesis CLOSED `stop_hit`; journal `exit_timing: entry_session` |
| 94 / 96 / 93 / 95 (opens below the stop) | Bought ₹94.00; warning "filled below its stop; the stop will trigger next session" | Bought ₹94.00 and **sold ₹93.91 the same session** (the open less 10 bps) |
| 103 / 104 / 94 / 96 (falls through the limit, then breaches) | Bought ₹102.00 at the limit; held overnight | Bought ₹102.00 and **sold ₹94.91 the same session** |
| 101 / 103 / 100.5 / 102 (no breach) | Bought ₹101.00, held | **Unchanged**: bought ₹101.00, held |
| 101 / 125 / 100.5 / 120 with a ₹110 target | Bought; target touch reported; target filled next session | **Unchanged**: no target or other exit on the entry session |
| STOP_LIMIT trigger ₹100 / cap ₹102, bar 99 / 101 / 94 / 96 | Bought ₹100.10; warning; stopped next session | **Unchanged** |

**Event sequence for a same-session stop** (journal lines, in order):
1. `FILL BUY` (`fill:<order_id>`, cash −cost)
2. `FILL SELL` (`exit:<thesis_id>:<session>`, `basis: stop`, `exit_reason: stop_hit`, `exit_timing: entry_session`, cash +proceeds)

trader-memory-core then records ENTRY_READY → ACTIVE (`open_position`) → CLOSED (`close`), both
at the session close timestamp. Its history check allows equal timestamps, and its exit-reason
list (`stop_hit`, `target_hit`, `time_stop`, `invalidated`, `manual`) is respected. The run
prints a warning naming the fill, the low, the stop and the exit price.

**Real-data example, run through the CLI.** This is one of the backtester's 205 same-session
stops, replayed with real cached NSE bars into a scratch state directory, not
`state/india-paper`:

```text
buy --symbol CHAMBLFERT --qty 193 --type limit --limit 544.95 --stop 519 --valid-sessions 1 --placed-at 2024-07-08T20:00+05:30
process --as-of 2024-07-09
  WARNING: 2024-07-09 CHAMBLFERT: LIMIT filled at ₹542.00 and the session low ₹507.15 reached
  the stop ₹519.00; a buy limit fills before a lower low, so the stop exited the same session at ₹518.48
  BUY  2024-07-09 542.00 open   charges ₹124.09  cash −1,04,730.09
  SELL 2024-07-09 518.48 stop   charges ₹119.62  cash +99,947.02  stop_hit / entry_session
process --as-of 2024-07-12 → 0 sessions, 0 events (nothing replayed)
```

| | Backtester (Phase 1.1A corrected run) | Paper ledger |
|---|---|---|
| Entry | ₹542.00 (open) | ₹542.00 (open) |
| Exit | ₹518.48, same session | ₹518.48, same session |
| Net cash result | −₹4,783.07 | −₹4,783.07 (₹10,00,000 → ₹9,95,216.93); journal `realized_pnl` −₹4,783.08 (S4) |

Downstream, on that state:
- `india-weekly-performance-digest` reports 1 closed trade, 0W/1L, realized −₹4,783.08.
- `india-drawdown-circuit-breaker` reads the thesis without errors (TRADING_ALLOWED).

---

## 6. Same-session LIMIT stop: implementation

```python
@staticmethod
def _entry_session_stop(order, bar) -> bool:
    return (order["order_type"] == "LIMIT"
            and order["stop_basis"] == "intraday"
            and float(bar["low"]) <= order["stop"])
```

In `_buy`, after the existing fill, cash check, thesis `open_position` and journal BUY:

- `exit_fill(bar, stop=order["stop"], target=None, stop_basis="intraday")` gives the existing
  exit rule: the open if it was at or below the stop, else the stop. The target is deliberately
  `None`.
- `apply_slippage(price, account slippage_bps, "SELL")`: the same stop slippage `run_exits`
  uses.
- The existing `_sell(...)` then:
  - computes sell charges (including DP);
  - computes the effective price and realized P&L;
  - closes the thesis as `stop_hit`;
  - journals with an idempotent event ID;
  - deletes the position.
- There is no time exit (the ledger has none), and the target is not evaluated on the entry
  session.

This is the same rule the backtester applies (`_entry_session_stop` in
`swing_backtest_engine.py`). The only naming difference: the backtester records the trade's
`exit_reason` as `stop_hit_entry_session`, while the ledger keeps trader-memory-core's
`stop_hit` and adds `exit_timing`, because trader-memory-core rejects other reasons and
downstream readers count `exit_reason`.

---

## 7. STOP_LIMIT behaviour and remaining limitations

- **STOP and STOP_LIMIT buys** fill on the way up (at the trigger, or at the open on a gap up).
  The daily bar does not show whether the session low came before or after the trigger, so the
  ledger still:
  - holds the position;
  - warns ("…also touched on the entry day; daily bars cannot show the order of events, so
    exits start next session");
  - evaluates the stop from the next session.

  Tested for both STOP and STOP_LIMIT.
- **CLOSE orders** fill at the close, so any earlier low came before the fill. The existing gap
  and entry-day warnings are unchanged (tested).
- **Close-basis stops** (`--stop-basis close`) are unchanged: evaluated from the next session
  (tested; decision item S5).
- **Limitations:**
  - Fills are end-of-day: there is no queue position or partial fill.
  - A LIMIT order "touching" its limit is treated as filled. A1 measured no such touch-only
    fills in the momentum backtest.

---

## 8. Accounting verification

All of the following are asserted by tests and exercised in the real-data run.

| Check | Result |
|---|---|
| Buy charges recorded once | `fill:<order_id>` event, cash −(turnover + charges); exactly one BUY journal line |
| Sell charges recorded once | `exit:<thesis>:<session>` event; exactly one SELL line; later sessions add none |
| DP charge | ₹0 on the buy leg, ₹15.93 on the sell leg (account setting), as on every other sale |
| Stop slippage | 10 bps deducted from the stop or open exit price (₹95.00 → ₹94.91; ₹94.00 → ₹93.91); 0 bps account → exactly ₹95.00 |
| Cash after the round trip | capital − buy cost + sell proceeds, to the paisa |
| Realized P&L | journal `realized_pnl` = proceeds − cost (±₹0.05); thesis `outcome.pnl_dollars` (rupees) equals it; snapshot `realized_pnl` and `charges_paid` (both legs) match |
| No double counting | snapshot cash = equity with nothing held; no open position remains |
| Quantities | position removed (never negative); thesis CLOSED with no shares remaining |
| Unrelated state | another open position, another pending LIMIT order (its validity counter advanced normally) and its ENTRY_READY thesis are unaffected |

No charge schedule, slippage rule or accounting convention was changed. S1 (gross equity) and
S4 (paisa rounding) are pre-existing and listed for decision.

---

## 9. Manual approval and safety verification

- **How approval works today:** the ledger has no approval *flag*. Its control is structural:
  an order exists only when a person runs `buy`, `place-plan --plan <file>` or `sell` (or the
  matching `place_entry` / `place_plan` / `place_exit` call).
- **No new way to create orders:** `process()` never creates a BUY or SELL order. It only fills
  orders that already exist and runs the protective stop/target exits set on them. The same
  -session stop is one more such protective exit.
- **Verified by test:**
  - After a same-session stop, the order list is unchanged: no re-entry, no new order.
  - The journal holds only that order's BUY and SELL.
  - An empty ledger processes to nothing.
- **Unchanged:** no broker code exists in or was added to the skill (no network calls besides
  free NSE bars). No autonomous trading, no CLI command, no flag; the `place-plan` refusal of
  stale plans and duplicate symbols is unchanged.
- **No broker orders or live trades were placed.** The only ledger state written was in pytest
  temporary directories, the fixture, and a scratch directory under the session scratchpad.

---

## 10. Persistence and idempotency verification

| Scenario | Test | Result |
|---|---|---|
| Repeated processing of the same session | `test_repeated_processing_is_idempotent` | second run: 0 sessions, 0 events; cash, journal, orders and positions identical |
| Processing later sessions after the closed trade | same test | no further exits; cash unchanged |
| Save and reload (new `PaperLedger` on the same directory, then process again) | `test_reload_from_disk_does_not_replay_the_exit` | journal and cash identical; no position recreated |
| Process restart (separate CLI processes) | real-data CLI run | second `process` → 0 sessions, 0 events |
| Interrupted save: journal written, `orders.json` / `positions.json` reverted to pre-run state | `test_interrupted_save_is_recovered_without_duplicates` | re-run rebuilds and re-closes the position without journaling or charging again; order FILLED; thesis CLOSED; cash unchanged |
| Duplicate execution attempt on a closed position | `test_no_duplicate_exit_or_negative_position` | lows below the stop on 3 later sessions → still exactly one SELL |
| Existing (pre-change) ledger records | `test_pre_phase_1_1_ledger_state_remains_readable` | fixture written by the unchanged code loads; snapshot reads its legacy closed trade, open position and pending order; processing a new session appends new lines and leaves every existing journal line byte-identical; the pending legacy LIMIT then follows the new rule (filled ₹207.00, stopped ₹199.80 the same session) |

**No historical ledger migration was performed.** There is no schema change and
`schema_version` stays 1. The new journal field is additive and optional, and existing records
are never rewritten; the journal is append-only.

One effect to be aware of: a LIMIT order placed **before** this change but filled **after** it
gets the new same-session rule. This is the approved behaviour, shown by the fixture test. No
such order exists on this machine.

---

## 11. Regression test results

Commands: CI's pytest command via `.venv/bin/python scripts/ci_test_matrix.py run <skill>`
(dependencies verified first with `install --check`; nothing installed), plus
`.venv/bin/python -m pytest scripts/tests/test_india_data.py -q --tb=short -rfEs`.

| Suite | After A | Now | Passed | Failed | Skipped |
|---|---:|---:|---:|---:|---:|
| india-swing-backtester | 179 | 179 | 179 | 0 | 0 |
| india-vcp-screener | 90 | 90 | 90 | 0 | 0 |
| india-stockbee-momentum-burst-screener | 71 | 71 | 71 | 0 | 0 |
| **india-paper-ledger** | 160 | **180** | 180 | 0 | 0 |
| india-breakout-trade-planner | 99 | 99 | 99 | 0 | 0 |
| shared `test_india_data.py` | 55 | 55 | 55 | 0 | 0 |
| **Six-suite total** | **654** | **674** | **674** | **0** | **0** |
| *Dependent:* india-drawdown-circuit-breaker | | 85 | 85 | 0 | 0 |
| *Dependent:* india-pre-trade-discipline-gate | | 83 | 83 | 0 | 0 |
| *Dependent:* india-signal-postmortem | | 99 | 99 | 0 | 0 |
| *Dependent:* india-weekly-performance-digest | | 108 | 108 | 0 | 0 |
| *Dependent:* trader-memory-core | | 682 | 682 | 0 | 0 |

- No existing test was modified or removed.
- The 12 new tests that exercise the new behaviour were run first against the unchanged code
  and failed. The other 8 are guards for behaviour that must not change, and passed both
  before and after.
- Coverage: `paper_ledger.py` 93%, skill total 94%.
- Local checks on all Workstream B files pass: ruff check and format, codespell, detect-secrets,
  no-absolute-paths, CLI error conventions, the dependency manifest, doc drift (0), final
  newline and trailing whitespace.

New tests, mapped to the 15 required scenarios:

| # | Requirement | Test(s) |
|---|---|---|
| 1 | Fills at open above stop, then breach | `test_open_above_stop_then_low_breaches_exits_at_stop` |
| 2 | Fills at open below stop | `test_open_at_or_below_stop_exits_at_the_open` |
| 3 | Fills intraday at the limit, then breach | `test_fill_at_limit_on_the_way_down_then_breach`, `test_low_exactly_at_the_stop_counts_as_a_breach` |
| 4 | Fills without breach | `test_fill_without_breach_keeps_the_position` |
| 5 | No same-session time (or target) exit | `test_only_the_stop_acts_on_the_entry_session` |
| 6-8 | Slippage, charges, cash, realized P&L | `test_charges_slippage_cash_and_realized_pnl_reconcile`, `test_zero_slippage_account_exits_exactly_at_the_stop` |
| 9-10 | No duplicate exit, no negative position; unrelated state intact | `test_no_duplicate_exit_or_negative_position`, `test_unrelated_positions_and_orders_are_untouched` |
| 11 | STOP_LIMIT (and STOP) ambiguity unchanged; CLOSE and close-basis unchanged | `test_buy_stop_entry_day_breach_keeps_next_session_policy` (×2), `test_close_order_entry_day_policy_unchanged`, `test_close_basis_limit_entry_keeps_next_session_policy` |
| 12 | Manual approval remains mandatory | `test_processing_never_creates_or_reopens_orders`, `test_an_empty_ledger_processes_to_nothing` |
| 13 | Repeated processing idempotent | `test_repeated_processing_is_idempotent` |
| 14 | Save/reload, restart, interrupted save | `test_reload_from_disk_does_not_replay_the_exit`, `test_interrupted_save_is_recovered_without_duplicates` |
| 15 | Existing records readable | `test_pre_phase_1_1_ledger_state_remains_readable` |

---

## 12. Outstanding issues and recommendations

Differences that remain between the ledger and the backtester:

| | Backtester | Paper ledger |
|---|---|---|
| LIMIT entry-session stop | yes | **yes (now)** |
| STOP / STOP_LIMIT entry day | next session, counted | next session, warned |
| Exit record | `exit_reason: stop_hit_entry_session` | `exit_reason: stop_hit` + `exit_timing: entry_session` |
| Equity valuation | net of estimated selling charges (default) | **gross** (S1) |
| Muhurat sessions | included (policy `include`) | **skipped** (S2) |
| Stop basis | intraday only | intraday or close; close basis on the next-session policy (S5) |
| Stop slippage | 10 bps default | account setting (`init --slippage-bps`, default 0) |

**Decisions needed:**
1. **S1:** report ledger equity and return net of estimated selling charges, as the backtester
   now does? This is an accounting and reporting change; I recommend yes, as a separate change.
2. **S2:** align Muhurat handling. Either the ledger includes them, or the backtester skips
   them. Today the two differ.
3. **S5:** apply a same-session close-basis stop to LIMIT entries (the close comes after the
   fill)? I recommend yes for consistency, but it needs your approval as a new rule.
4. **S3:** make the thesis-then-journal update crash-safe, for example by writing a pending
   journal marker first. This is pre-existing and low risk for a single-user end-of-day ledger.
   I recommend a small hardening change later.
5. **S4:** none needed; it is cosmetic. It could be documented in `fill_rules.md`.

Before any commit:
- Repackage the skills (`scripts/package_skills.py`).
- Run the full pre-commit suite; its local checks were run individually above.

---

## 13. Migration confirmation

No historical ledger record was modified, rewritten or migrated:
- No schema or database change was made.
- `schema_version` is unchanged.
- There was no existing ledger state on this machine.

## 14. Trading confirmation

No broker order was placed, no live trade was made, no autonomous trading was enabled, and
nothing was committed, pushed, merged or deployed.
