"""Phase 1.1 Workstream B: same-session stop for LIMIT entries (parity with the backtester).

A buy limit fills at the open (open <= limit) or as the price falls through the limit.
The stop sits below the limit, so a session low at or under the stop came after the
fill: the position is stopped out that session. Buy-stops (STOP, STOP_LIMIT) fill on
the way up, and a daily bar cannot show whether the low came first; they keep the
documented next-session policy, as do CLOSE orders (filled at the close itself).

See docs/research/PHASE1_1B_PAPER_LEDGER_FIX_REPORT.md.
"""

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import paper_engine as eng
import paper_ledger as pl
import pytest
from india_paper_fakes import FakeBars, bar

IST = timezone(timedelta(hours=5, minutes=30))
CAPITAL = 1_000_000
QTY = 100
FIXTURE = Path(__file__).parent / "fixtures" / "legacy_v1_state"


def placed(day="2026-11-20", hh=20, mm=0):
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def make(tmp_path, bars, slippage_bps=10, **acct):
    led = pl.PaperLedger(tmp_path / "paper", bars_fn=FakeBars({"ABC": {"bars": bars}}))
    led.init_account(CAPITAL, slippage_bps=slippage_bps, created_on=date(2026, 11, 20), **acct)
    return led


def limit_order(led, limit=102.0, stop=95.0, target=None, **kw):
    return led.place_entry(
        "ABC",
        QTY,
        order_type="LIMIT",
        limit=limit,
        stop=stop,
        target=target,
        placed_at=placed(),
        **kw,
    )


def sells(led):
    return [e for e in led.journal() if e["event"] == "FILL" and e["side"] == "SELL"]


def thesis(led, tid):
    return led.thesis_store.get(led.theses_dir, tid)


LATER = [
    bar("2026-11-24", 96, 97, 95.5, 96),
    bar("2026-11-25", 96, 97, 95.5, 96),
]


# --------------------------------------------------------------------------- 1-4 execution


def test_open_above_stop_then_low_breaches_exits_at_stop(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)] + LATER)
    order = limit_order(led)
    result = led.process(date(2026, 11, 23))
    (sell,) = sells(led)
    assert led.orders()[0]["fill"]["price"] == 101 and led.orders()[0]["fill"]["basis"] == "open"
    assert sell["session"] == "2026-11-23" and sell["exit_timing"] == "entry_session"
    assert sell["price"] == pytest.approx(94.91)  # stop 95.00 less 10 bps
    assert sell["basis"] == "stop" and sell["exit_reason"] == "stop_hit"
    assert led.positions() == {}
    th = thesis(led, order["thesis_id"])
    assert th["status"] == "CLOSED" and th["exit"]["exit_reason"] == "stop_hit"
    assert any("same session" in w for w in result["warnings"])
    assert not any("entry day" in w for w in result["warnings"])


def test_open_at_or_below_stop_exits_at_the_open(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 94, 96, 93, 95)] + LATER)
    limit_order(led)
    result = led.process(date(2026, 11, 23))
    (sell,) = sells(led)
    assert led.orders()[0]["fill"]["price"] == 94  # LIMIT 102 filled at the open
    assert sell["price"] == pytest.approx(93.91)  # the open less 10 bps
    assert sell["exit_timing"] == "entry_session"
    assert not any("will trigger next session" in w for w in result["warnings"])


def test_fill_at_limit_on_the_way_down_then_breach(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 103, 104, 94, 96)] + LATER)
    limit_order(led)
    led.process(date(2026, 11, 23))
    (sell,) = sells(led)
    assert led.orders()[0]["fill"]["price"] == 102.0
    assert led.orders()[0]["fill"]["basis"] == "limit"
    assert sell["price"] == pytest.approx(94.91)


def test_fill_without_breach_keeps_the_position(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 103, 100.5, 102)] + LATER)
    order = limit_order(led)
    result = led.process(date(2026, 11, 23))
    assert sells(led) == []
    pos = led.positions()[order["thesis_id"]]
    assert pos["qty"] == QTY and pos["last_evaluated"] == "2026-11-23"
    assert thesis(led, order["thesis_id"])["status"] == "ACTIVE"
    assert not any("same session" in w for w in result["warnings"])


def test_low_exactly_at_the_stop_counts_as_a_breach(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 95.0, 96)] + LATER)
    limit_order(led)
    led.process(date(2026, 11, 23))
    assert len(sells(led)) == 1


# --------------------------------------------------------------------------- 5 no other exit


def test_only_the_stop_acts_on_the_entry_session(tmp_path):
    """No target (or any time-based) exit on the entry session; the target fills next session."""
    bars = [
        bar("2026-11-23", 101, 125, 100.5, 120),  # high above the 110 target, low above stop
        bar("2026-11-24", 121, 122, 119, 120),  # opens above the target
    ]
    led = make(tmp_path, bars)
    order = limit_order(led, target=110.0)
    result = led.process(date(2026, 11, 23))
    assert sells(led) == [] and order["thesis_id"] in led.positions()
    assert any("entry day" in w for w in result["warnings"])  # the target touch is reported
    led.process(date(2026, 11, 24))
    (sell,) = sells(led)
    assert sell["exit_reason"] == "target_hit" and sell["session"] == "2026-11-24"
    assert "exit_timing" not in sell


# --------------------------------------------------------------------------- 6-8 accounting


def test_charges_slippage_cash_and_realized_pnl_reconcile(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)] + LATER)
    order = limit_order(led)
    led.process(date(2026, 11, 23))
    buy = next(e for e in led.journal() if e["event"] == "FILL" and e["side"] == "BUY")
    (sell,) = sells(led)
    exp_buy = eng.leg_charges(101 * QTY, buy=True, dp_charge=15.93)
    exp_sell = eng.leg_charges(94.91 * QTY, buy=False, dp_charge=15.93)
    assert buy["charges"] == exp_buy and buy["charges"]["dp_charge"] == 0
    assert sell["charges"] == exp_sell and sell["charges"]["dp_charge"] == 15.93
    cost = exp_buy["turnover"] + exp_buy["total"]
    proceeds = exp_sell["turnover"] - exp_sell["total"]
    assert buy["cash_delta"] == pytest.approx(-cost, abs=0.01)
    assert sell["cash_delta"] == pytest.approx(proceeds, abs=0.01)
    assert led.cash() == pytest.approx(CAPITAL - cost + proceeds, abs=0.01)
    assert sell["realized_pnl"] == pytest.approx(proceeds - cost, abs=0.05)
    th = thesis(led, order["thesis_id"])
    assert th["outcome"]["pnl_dollars"] == pytest.approx(sell["realized_pnl"], abs=0.05)
    snap = led.snapshot(date(2026, 11, 23))
    assert snap["account"]["realized_pnl"] == pytest.approx(sell["realized_pnl"], abs=0.01)
    assert snap["account"]["charges_paid"] == pytest.approx(
        exp_buy["total"] + exp_sell["total"], abs=0.01
    )
    assert snap["account"]["cash"] == snap["account"]["equity"]  # nothing held
    assert snap["positions"] == []


def test_zero_slippage_account_exits_exactly_at_the_stop(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)], slippage_bps=0)
    limit_order(led)
    led.process(date(2026, 11, 23))
    assert sells(led)[0]["price"] == 95.0


# --------------------------------------------------------------------------- 9-10 integrity


def test_no_duplicate_exit_or_negative_position(tmp_path):
    later = [bar(d, 94, 95, 90, 92) for d in ("2026-11-24", "2026-11-25", "2026-11-26")]
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)] + later)
    order = limit_order(led)
    led.process(date(2026, 11, 26))
    assert len(sells(led)) == 1
    assert led.positions() == {}
    th = thesis(led, order["thesis_id"])
    assert th["status"] == "CLOSED"
    assert (th.get("position") or {}).get("shares_remaining", 0) in (0, None)
    assert led.orders()[0]["status"] == "FILLED"
    assert [o["status"] for o in led.orders()] == ["FILLED"]


def test_unrelated_positions_and_orders_are_untouched(tmp_path):
    fb = FakeBars(
        {
            "ABC": {"bars": [bar("2026-11-23", 101, 102, 94, 96)]},
            "XYZ": {"bars": [bar("2026-11-23", 500, 505, 495, 502)]},
            "LIM": {"bars": [bar("2026-11-23", 220, 222, 215, 221)]},
        }
    )
    led = pl.PaperLedger(tmp_path / "paper", bars_fn=fb)
    led.init_account(CAPITAL, slippage_bps=10)
    limit_order(led)
    xyz = led.place_entry("XYZ", 10, stop=480, placed_at=placed())
    lim = led.place_entry(
        "LIM", 10, order_type="LIMIT", limit=210, stop=200, valid_sessions=5, placed_at=placed()
    )
    led.process(date(2026, 11, 23))
    assert set(led.positions()) == {xyz["thesis_id"]}
    status = {o["symbol"]: o["status"] for o in led.orders()}
    assert status == {"ABC": "FILLED", "XYZ": "FILLED", "LIM": "OPEN"}
    assert next(o for o in led.orders() if o["symbol"] == "LIM")["sessions_evaluated"] == 1
    assert thesis(led, lim["thesis_id"])["status"] == "ENTRY_READY"


# --------------------------------------------------------------------------- 11 buy-stops unchanged


@pytest.mark.parametrize(
    "kwargs",
    [
        {"order_type": "STOP_LIMIT", "trigger": 100.0, "limit": 102.0},
        {"order_type": "STOP", "trigger": 100.0},
    ],
)
def test_buy_stop_entry_day_breach_keeps_next_session_policy(tmp_path, kwargs):
    bars = [
        bar("2026-11-23", 99, 101, 94, 96),  # triggers at 100 on the way up; low under 95
        bar("2026-11-24", 96, 97, 94.5, 95.5),  # stop hit the next session
    ]
    led = make(tmp_path, bars)
    order = led.place_entry("ABC", QTY, stop=95.0, placed_at=placed(), **kwargs)
    result = led.process(date(2026, 11, 23))
    assert sells(led) == [] and order["thesis_id"] in led.positions()
    assert any("entry day" in w and "next session" in w for w in result["warnings"])
    led.process(date(2026, 11, 24))
    (sell,) = sells(led)
    assert sell["session"] == "2026-11-24" and sell["exit_reason"] == "stop_hit"
    assert "exit_timing" not in sell


def test_close_order_entry_day_policy_unchanged(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 100, 101, 90, 100)])
    led.place_entry("ABC", 10, stop=95, placed_at=placed())  # CLOSE: fills at the close
    result = led.process(date(2026, 11, 23))
    assert sells(led) == [] and len(led.positions()) == 1
    assert any("entry day" in w for w in result["warnings"])


def test_close_basis_limit_entry_keeps_next_session_policy(tmp_path):
    """Close-basis stops are outside the approved F2 semantics (open/low); unchanged."""
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 93, 94)])  # low and close under 95
    order = limit_order(led, stop_basis="close")
    result = led.process(date(2026, 11, 23))
    assert sells(led) == [] and order["thesis_id"] in led.positions()
    assert any("entry day" in w for w in result["warnings"])


# --------------------------------------------------------------------------- 12 manual approval


def test_processing_never_creates_or_reopens_orders(tmp_path):
    later = [bar(d, 101, 103, 100.5, 102) for d in ("2026-11-24", "2026-11-25")]
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)] + later)
    limit_order(led)
    before = [o["order_id"] for o in led.orders()]
    led.process(date(2026, 11, 25))
    assert [o["order_id"] for o in led.orders()] == before  # no re-entry after the stop
    assert [e["side"] for e in led.journal() if e["event"] == "FILL"] == ["BUY", "SELL"]


def test_an_empty_ledger_processes_to_nothing(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)])
    result = led.process(date(2026, 11, 23))
    assert led.orders() == [] and led.journal() == [] and result["events"] == []


# --------------------------------------------------------------------------- 13-14 idempotency


def test_repeated_processing_is_idempotent(tmp_path):
    led = make(tmp_path, [bar("2026-11-23", 101, 102, 94, 96)] + LATER)
    limit_order(led)
    led.process(date(2026, 11, 23))
    state = (led.cash(), led.journal(), led.orders(), led.positions())
    again = led.process(date(2026, 11, 23))
    assert again["events"] == [] and again["sessions"] == []
    assert (led.cash(), led.journal(), led.orders(), led.positions()) == state
    led.process(date(2026, 11, 25))  # later sessions do not touch the closed trade
    assert len(sells(led)) == 1 and led.cash() == state[0]


def test_reload_from_disk_does_not_replay_the_exit(tmp_path):
    bars = [bar("2026-11-23", 101, 102, 94, 96)] + LATER
    led = make(tmp_path, bars)
    limit_order(led)
    led.process(date(2026, 11, 23))
    cash, journal = led.cash(), led.journal()
    fresh = pl.PaperLedger(tmp_path / "paper", bars_fn=FakeBars({"ABC": {"bars": bars}}))
    fresh.process(date(2026, 11, 23))
    fresh.process(date(2026, 11, 25))
    assert fresh.journal() == journal and fresh.cash() == cash
    assert fresh.positions() == {}


def test_interrupted_save_is_recovered_without_duplicates(tmp_path):
    """Journal written but orders/positions not saved (crash): a re-run must not double count."""
    bars = [bar("2026-11-23", 101, 102, 94, 96)] + LATER
    led = make(tmp_path, bars)
    order = limit_order(led)
    orders_before = (tmp_path / "paper" / "orders.json").read_text()
    positions_before = (tmp_path / "paper" / "positions.json").read_text()
    led.process(date(2026, 11, 23))
    cash, journal = led.cash(), led.journal()
    (tmp_path / "paper" / "orders.json").write_text(orders_before)
    (tmp_path / "paper" / "positions.json").write_text(positions_before)
    led.process(date(2026, 11, 23))
    assert led.journal() == journal and led.cash() == cash
    assert led.positions() == {}
    assert led.orders()[0]["status"] == "FILLED"
    assert thesis(led, order["thesis_id"])["status"] == "CLOSED"


# --------------------------------------------------------------------------- 15 legacy records


def test_pre_phase_1_1_ledger_state_remains_readable(tmp_path):
    """State written by the ledger before this change (fixture) loads and keeps processing."""
    state = tmp_path / "legacy"
    shutil.copytree(FIXTURE, state)
    journal_before = (state / "journal.jsonl").read_text().splitlines()
    fb = FakeBars(
        {
            "XYZ": {
                "bars": [
                    bar("2026-11-24", 502, 506, 498, 504),
                    bar("2026-11-25", 504, 510, 500, 508),
                ]
            },
            "LIM": {"bars": [bar("2026-11-25", 207, 209, 199, 201)]},  # LIMIT 208, stop 200
        }
    )
    led = pl.PaperLedger(state, bars_fn=fb, now_fn=lambda: placed("2026-11-25", 20, 30))
    old = led.snapshot(date(2026, 11, 24))
    assert old["account"]["realized_pnl"] < 0  # the legacy ABC stop exit
    assert [p["symbol"] for p in old["positions"]] == ["XYZ"]
    assert any(o["symbol"] == "LIM" for o in old["open_orders"])
    cash_before = led.cash()
    led.process(date(2026, 11, 25))
    lines = (state / "journal.jsonl").read_text().splitlines()
    assert lines[: len(journal_before)] == journal_before  # history is append-only, untouched
    new = [json.loads(x) for x in lines[len(journal_before) :]]
    assert [(e["symbol"], e["side"]) for e in new] == [("LIM", "BUY"), ("LIM", "SELL")]
    assert new[1]["exit_timing"] == "entry_session" and new[1]["price"] == pytest.approx(199.8)
    assert set(led.positions()) == set(
        json.loads((FIXTURE / "positions.json").read_text())["positions"]
    )
    assert led.cash() == pytest.approx(
        cash_before + new[0]["cash_delta"] + new[1]["cash_delta"], abs=0.01
    )
    assert (
        led.snapshot(date(2026, 11, 25))["account"]["realized_pnl"] < old["account"]["realized_pnl"]
    )
