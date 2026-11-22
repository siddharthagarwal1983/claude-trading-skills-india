"""Ledger tests: orders, EOD processing, trader-memory-core bridge, reports.

Uses the real trader-memory-core thesis store (sibling skill) against fake
NSE bars, so every lifecycle call is validated by its schema.
"""

import json
from datetime import date, datetime, timedelta, timezone

import paper_ledger as pl
import pytest
from india_paper_fakes import FakeBars, bar

IST = timezone(timedelta(hours=5, minutes=30))


def placed(day="2026-11-20", hh=20, mm=0):
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=IST)


@pytest.fixture
def bars():
    return FakeBars(
        {
            "RELIANCE": {
                "bars": [
                    bar("2026-11-20", 1000, 1010, 990, 1000),
                    bar("2026-11-23", 1000, 1020, 995, 1010),
                    bar("2026-11-24", 1012, 1030, 1005, 1025),
                    bar("2026-11-25", 1020, 1022, 940, 950),
                    bar("2026-11-26", 950, 960, 945, 955),
                ]
            },
            "M&M": {
                "bars": [
                    bar("2026-11-23", 3000, 3050, 2990, 3040),
                    bar("2026-11-24", 3040, 3100, 3030, 3090),
                    bar("2026-11-25", 3090, 3300, 3080, 3250),
                ]
            },
        }
    )


@pytest.fixture
def ledger(tmp_path, bars):
    led = pl.PaperLedger(tmp_path / "paper", bars_fn=bars)
    led.init_account(1_000_000, created_on=date(2026, 11, 20))
    return led


def thesis(led, tid):
    return led.thesis_store.get(led.theses_dir, tid)


# --------------------------------------------------------------------------- account


def test_init_account_writes_state(tmp_path, bars):
    led = pl.PaperLedger(tmp_path / "p", bars_fn=bars)
    acct = led.init_account(500_000, slippage_bps=5, created_on=date(2026, 11, 1))
    assert acct["currency"] == "INR"
    assert acct["starting_capital"] == 500_000
    assert led.cash() == 500_000
    with pytest.raises(pl.LedgerError):
        led.init_account(1)


def test_init_validates_inputs(tmp_path, bars):
    led = pl.PaperLedger(tmp_path / "p", bars_fn=bars)
    with pytest.raises(ValueError):
        led.init_account(0)
    with pytest.raises(ValueError):
        led.init_account(1000, slippage_bps=-1)


def test_uninitialized_ledger_refuses(tmp_path, bars):
    led = pl.PaperLedger(tmp_path / "p", bars_fn=bars)
    with pytest.raises(pl.LedgerError, match="init"):
        led.place_entry("RELIANCE", 1, stop=900)


# --------------------------------------------------------------------------- placing


def test_place_close_order_registers_entry_ready_thesis(ledger):
    order = ledger.place_entry(
        "RELIANCE", 50, stop=950, target=1150, placed_at=placed(), thesis="Breakout"
    )
    assert order["status"] == "OPEN"
    assert order["order_type"] == "CLOSE"
    assert order["valid_sessions"] == 1
    assert order["first_eligible"] == "2026-11-21"
    th = thesis(ledger, order["thesis_id"])
    assert th["status"] == "ENTRY_READY"
    assert th["ticker"] == "RELIANCE"
    assert th["exit"]["stop_loss"] == 950
    assert th["exit"]["take_profit"] == 1150
    assert th["origin"]["raw_provenance"]["nse_symbol"] == "RELIANCE"


def test_place_symbol_with_ampersand(ledger):
    order = ledger.place_entry("m&m", 5, stop=2900, placed_at=placed())
    assert order["symbol"] == "M&M"
    assert thesis(ledger, order["thesis_id"])["ticker"] == "MANDM"


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"qty": 0, "stop": 900}, "qty"),
        ({"qty": 1.5, "stop": 900}, "qty"),
        ({"qty": 1, "stop": 0}, "stop"),
        ({"qty": 1, "stop": 900, "order_type": "LIMIT"}, "limit"),
        ({"qty": 1, "stop": 900, "order_type": "LIMIT", "limit": 890}, "above the stop"),
        ({"qty": 1, "stop": 900, "order_type": "STOP"}, "trigger"),
        (
            {"qty": 1, "stop": 900, "order_type": "STOP_LIMIT", "trigger": 1000, "limit": 990},
            "limit",
        ),
        ({"qty": 1, "stop": 900, "target": 850}, "target"),
        ({"qty": 1, "stop": 900, "stop_basis": "weekly"}, "stop_basis"),
        ({"qty": 1, "stop": 900, "order_type": "MARKET"}, "order type"),
        ({"qty": 1, "stop": 900, "valid_sessions": 0}, "valid_sessions"),
    ],
)
def test_place_validation(ledger, kwargs, match):
    qty = kwargs.pop("qty")
    with pytest.raises(ValueError, match=match):
        ledger.place_entry("RELIANCE", qty, placed_at=placed(), **kwargs)


def test_place_with_existing_idea_thesis(ledger):
    ts = ledger.thesis_store
    tid = ts.register(
        ledger.theses_dir,
        {
            "ticker": "RELIANCE",
            "thesis_type": "growth_momentum",
            "thesis_statement": "existing idea",
            "entry": {},
            "exit": {},
            "origin": {"skill": "manual", "output_file": "x"},
        },
    )
    order = ledger.place_entry("RELIANCE", 10, stop=950, thesis_id=tid, placed_at=placed())
    assert order["thesis_id"] == tid
    th = thesis(ledger, tid)
    assert th["status"] == "ENTRY_READY"
    assert th["exit"]["stop_loss"] == 950


def test_place_with_thesis_for_other_ticker_fails(ledger):
    other = ledger.place_entry("M&M", 1, stop=2900, placed_at=placed())
    with pytest.raises(pl.LedgerError, match="ticker"):
        ledger.place_entry("RELIANCE", 1, stop=900, thesis_id=other["thesis_id"])


def test_place_with_active_thesis_fails(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=950, placed_at=placed())
    ledger.process(date(2026, 11, 23))
    with pytest.raises(pl.LedgerError, match="status"):
        ledger.place_entry("RELIANCE", 1, stop=900, thesis_id=order["thesis_id"])


# --------------------------------------------------------------------------- processing: entries


def test_close_order_fills_with_charges_and_opens_thesis(ledger):
    order = ledger.place_entry("RELIANCE", 50, stop=950, placed_at=placed())
    result = ledger.process(date(2026, 11, 23))
    assert result["sessions"] == ["2026-11-23"]
    filled = ledger.orders()[0]
    assert filled["status"] == "FILLED"
    assert filled["fill"]["price"] == 1010.0
    assert filled["fill"]["session"] == "2026-11-23"

    th = thesis(ledger, order["thesis_id"])
    assert th["status"] == "ACTIVE"
    assert th["position"]["shares"] == 50
    charges = filled["fill"]["charges"]["total"]
    assert charges > 50  # STT alone is ₹50.50
    assert th["entry"]["actual_price"] == pytest.approx(1010 + charges / 50, abs=1e-4)
    assert ledger.cash() == pytest.approx(1_000_000 - 50 * 1010 - charges, abs=0.01)

    pos = ledger.positions()[order["thesis_id"]]
    assert pos["qty"] == 50
    assert pos["stop"] == 950
    assert pos["last_evaluated"] == "2026-11-23"


def test_process_is_idempotent(ledger):
    ledger.place_entry("RELIANCE", 50, stop=950, placed_at=placed())
    ledger.process(date(2026, 11, 23))
    cash = ledger.cash()
    journal = ledger.journal()
    again = ledger.process(date(2026, 11, 23))
    assert again["events"] == []
    assert ledger.cash() == cash
    assert ledger.journal() == journal


def test_close_order_expires_after_one_session_when_locked(tmp_path):
    fb = FakeBars({"LOCKED": {"bars": [bar("2026-11-23", 50, 50, 50, 50)]}})
    led = pl.PaperLedger(tmp_path / "p", bars_fn=fb)
    led.init_account(100_000)
    order = led.place_entry("LOCKED", 10, stop=45, placed_at=placed())
    result = led.process(date(2026, 11, 23))
    assert led.orders()[0]["status"] == "EXPIRED"
    assert any("circuit" in w for w in result["warnings"])
    assert thesis(led, order["thesis_id"])["status"] == "INVALIDATED"


def test_stop_order_waits_then_fills_at_trigger(ledger):
    ledger.place_entry(
        "RELIANCE",
        10,
        order_type="STOP",
        trigger=1028,
        stop=990,
        valid_sessions=5,
        placed_at=placed(),
    )
    ledger.process(date(2026, 11, 23))
    assert ledger.orders()[0]["status"] == "OPEN"
    assert ledger.orders()[0]["sessions_evaluated"] == 1
    ledger.process(date(2026, 11, 24))
    filled = ledger.orders()[0]
    assert filled["status"] == "FILLED"
    assert filled["fill"]["price"] == 1028.0
    assert filled["fill"]["basis"] == "trigger"


def test_stop_order_slippage_applies(tmp_path, bars):
    led = pl.PaperLedger(tmp_path / "p", bars_fn=bars)
    led.init_account(1_000_000, slippage_bps=10)
    led.place_entry(
        "RELIANCE",
        10,
        order_type="STOP",
        trigger=1028,
        stop=990,
        valid_sessions=5,
        placed_at=placed(),
    )
    led.process(date(2026, 11, 24))
    assert led.orders()[0]["fill"]["price"] == pytest.approx(1029.03)


def test_limit_order_expires_and_invalidates_thesis(ledger):
    order = ledger.place_entry(
        "RELIANCE",
        10,
        order_type="LIMIT",
        limit=980,
        stop=950,
        valid_sessions=2,
        placed_at=placed(),
    )
    ledger.process(date(2026, 11, 24))
    assert ledger.orders()[0]["status"] == "EXPIRED"
    th = thesis(ledger, order["thesis_id"])
    assert th["status"] == "INVALIDATED"


def test_insufficient_cash_rejects(tmp_path, bars):
    led = pl.PaperLedger(tmp_path / "p", bars_fn=bars)
    led.init_account(10_000)
    order = led.place_entry("RELIANCE", 50, stop=950, placed_at=placed())
    result = led.process(date(2026, 11, 23))
    assert led.orders()[0]["status"] == "REJECTED"
    assert "cash" in led.orders()[0]["status_reason"]
    assert thesis(led, order["thesis_id"])["status"] == "INVALIDATED"
    assert led.cash() == 10_000
    assert any(e["type"] == "REJECTED" for e in result["events"])


def test_unknown_symbol_rejects_order(ledger):
    ledger.place_entry("NOSUCH", 1, stop=1, placed_at=placed())
    result = ledger.process(date(2026, 11, 23))
    assert ledger.orders()[0]["status"] == "REJECTED"
    assert "symbol" in ledger.orders()[0]["status_reason"]
    assert result["events"][0]["type"] == "REJECTED"


def test_data_error_leaves_state_untouched(tmp_path):
    def broken(symbol, start, end):
        raise pl.IndiaDataError("NSE MCP down")

    led = pl.PaperLedger(tmp_path / "p", bars_fn=broken)
    led.init_account(100_000)
    led.place_entry("RELIANCE", 1, stop=900, placed_at=placed())
    result = led.process(date(2026, 11, 23))
    assert led.orders()[0]["status"] == "OPEN"
    assert any("NSE MCP down" in w for w in result["warnings"])


def test_same_day_close_order_before_1500(ledger):
    ledger.place_entry("RELIANCE", 10, stop=950, placed_at=placed("2026-11-23", 14, 30))
    ledger.process(date(2026, 11, 23))
    assert ledger.orders()[0]["fill"]["session"] == "2026-11-23"


def test_entry_day_stop_touch_warns(tmp_path):
    fb = FakeBars({"ABC": {"bars": [bar("2026-11-23", 100, 101, 90, 100)]}})
    led = pl.PaperLedger(tmp_path / "p", bars_fn=fb)
    led.init_account(100_000)
    led.place_entry("ABC", 10, stop=95, placed_at=placed())
    result = led.process(date(2026, 11, 23))
    assert led.orders()[0]["status"] == "FILLED"
    assert any("entry day" in w for w in result["warnings"])
    assert len(led.positions()) == 1


def test_fill_below_stop_is_recorded_as_gap_fill(tmp_path):
    fb = FakeBars({"ABC": {"bars": [bar("2026-11-23", 100, 101, 80, 85)]}})
    led = pl.PaperLedger(tmp_path / "p", bars_fn=fb)
    led.init_account(100_000)
    order = led.place_entry("ABC", 10, stop=90, placed_at=placed())
    result = led.process(date(2026, 11, 23))
    assert thesis(led, order["thesis_id"])["status"] == "ACTIVE"
    assert any("below its stop" in w for w in result["warnings"])


# --------------------------------------------------------------------------- processing: exits


def test_stop_exit_closes_thesis_with_net_pnl(ledger):
    order = ledger.place_entry("RELIANCE", 50, stop=960, placed_at=placed())
    ledger.process(date(2026, 11, 26))
    th = thesis(ledger, order["thesis_id"])
    assert th["status"] == "CLOSED"
    assert th["exit"]["exit_reason"] == "stop_hit"
    sells = [j for j in ledger.journal() if j.get("side") == "SELL"]
    assert len(sells) == 1
    assert sells[0]["price"] == 960.0
    assert sells[0]["session"] == "2026-11-25"
    buy = next(j for j in ledger.journal() if j.get("side") == "BUY")
    gross = 50 * (960 - 1010)
    net = gross - buy["charges"]["total"] - sells[0]["charges"]["total"]
    assert sells[0]["realized_pnl"] == pytest.approx(net, abs=0.05)
    assert th["outcome"]["pnl_dollars"] == pytest.approx(net, abs=0.05)
    assert ledger.positions() == {}
    assert ledger.cash() == pytest.approx(1_000_000 + net, abs=0.05)


def test_target_exit(ledger):
    order = ledger.place_entry("M&M", 10, stop=2900, target=3200, placed_at=placed())
    ledger.process(date(2026, 11, 25))
    th = thesis(ledger, order["thesis_id"])
    assert th["status"] == "CLOSED"
    assert th["exit"]["exit_reason"] == "target_hit"
    sell = [j for j in ledger.journal() if j.get("side") == "SELL"][0]
    assert sell["price"] == 3200.0


def test_close_basis_stop(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=945, stop_basis="close", placed_at=placed())
    ledger.process(date(2026, 11, 26))
    # 11-25 low 940 breaches 945 intraday but closes 950 -> no exit on a close basis
    assert thesis(ledger, order["thesis_id"])["status"] == "ACTIVE"


def test_manual_sell_partial_then_full(ledger):
    order = ledger.place_entry("RELIANCE", 50, stop=900, placed_at=placed())
    ledger.process(date(2026, 11, 23))
    tid = order["thesis_id"]
    ledger.place_exit(tid, 20, placed_at=placed("2026-11-24", 10, 0))
    ledger.process(date(2026, 11, 24))
    th = thesis(ledger, tid)
    assert th["status"] == "PARTIALLY_CLOSED"
    assert th["position"]["shares_remaining"] == 30
    assert ledger.positions()[tid]["qty"] == 30
    ledger.place_exit(tid, placed_at=placed("2026-11-25", 10, 0))
    ledger.process(date(2026, 11, 25))
    th = thesis(ledger, tid)
    assert th["status"] == "CLOSED"
    assert th["exit"]["exit_reason"] == "manual"


def test_place_exit_validation(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=900, placed_at=placed())
    with pytest.raises(pl.LedgerError, match="no open paper position"):
        ledger.place_exit(order["thesis_id"])
    ledger.process(date(2026, 11, 23))
    with pytest.raises(ValueError, match="qty"):
        ledger.place_exit(order["thesis_id"], 11)
    ledger.place_exit(order["thesis_id"], 6, placed_at=placed("2026-11-24", 10, 0))
    with pytest.raises(ValueError, match="qty"):
        ledger.place_exit(order["thesis_id"], 5)


def test_stop_beats_pending_manual_sell(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=960, placed_at=placed())
    ledger.process(date(2026, 11, 24))
    ledger.place_exit(order["thesis_id"], placed_at=placed("2026-11-25", 10, 0))
    ledger.process(date(2026, 11, 25))
    statuses = [o["status"] for o in ledger.orders()]
    assert statuses == ["FILLED", "CANCELLED"]
    assert thesis(ledger, order["thesis_id"])["exit"]["exit_reason"] == "stop_hit"


def test_set_exit_updates_position_and_thesis(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=900, placed_at=placed())
    ledger.process(date(2026, 11, 23))
    pos = ledger.set_exit(order["thesis_id"], stop=1000, target=1200)
    assert pos["stop"] == 1000
    th = thesis(ledger, order["thesis_id"])
    assert th["exit"]["stop_loss"] == 1000
    assert th["exit"]["take_profit"] == 1200
    with pytest.raises(ValueError):
        ledger.set_exit(order["thesis_id"], stop=1300)  # stop above target
    with pytest.raises(pl.LedgerError):
        ledger.set_exit("th_missing_pvt_20260101_abcd", stop=1)


def test_cancel_open_order(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=900, placed_at=placed())
    cancelled = ledger.cancel(order["order_id"])
    assert cancelled["status"] == "CANCELLED"
    assert thesis(ledger, order["thesis_id"])["status"] == "INVALIDATED"
    with pytest.raises(pl.LedgerError):
        ledger.cancel(order["order_id"])
    with pytest.raises(pl.LedgerError):
        ledger.cancel("po_missing")


# --------------------------------------------------------------------------- corporate actions


def test_dividend_credited_to_cash(tmp_path):
    fb = FakeBars(
        {
            "ITC": {
                "bars": [
                    bar("2026-11-23", 400, 405, 398, 400),
                    bar("2026-11-24", 395, 398, 393, 396),
                ],
                "actions": [
                    {
                        "ex_date": "2026-11-24",
                        "action_type": "DIVIDEND",
                        "purpose": "Interim Dividend - Rs 6.50 Per Share",
                        "factor": 1.0,
                    }
                ],
            }
        }
    )
    led = pl.PaperLedger(tmp_path / "p", bars_fn=fb)
    led.init_account(100_000)
    led.place_entry("ITC", 100, stop=350, placed_at=placed())
    led.process(date(2026, 11, 23))
    cash_before = led.cash()
    led.process(date(2026, 11, 24))
    div = [j for j in led.journal() if j["event"] == "DIVIDEND"]
    assert len(div) == 1
    assert div[0]["amount"] == 650.0
    assert led.cash() == pytest.approx(cash_before + 650)


def test_bonus_blocks_position_and_suspends_orders(tmp_path):
    actions = [
        {"ex_date": "2026-11-24", "action_type": "BONUS", "purpose": "Bonus 1:1", "factor": 0.5}
    ]
    fb = FakeBars(
        {
            "ABC": {
                "bars": [
                    bar("2026-11-23", 200, 205, 198, 200),
                    bar("2026-11-24", 100, 102, 90, 95),
                ],
                "actions": actions,
            },
            "XYZ": {
                "bars": [
                    bar("2026-11-23", 50, 51, 49, 50),
                    bar("2026-11-24", 25, 26, 24, 25),
                ],
                "actions": actions,
            },
        }
    )
    led = pl.PaperLedger(tmp_path / "p", bars_fn=fb)
    led.init_account(100_000)
    held = led.place_entry("ABC", 10, stop=180, placed_at=placed())
    led.process(date(2026, 11, 23))
    pending = led.place_entry(
        "XYZ",
        10,
        order_type="LIMIT",
        limit=45,
        stop=40,
        valid_sessions=5,
        placed_at=placed("2026-11-23", 20, 0),
    )
    result = led.process(date(2026, 11, 24))
    # 90 low would breach the 180 stop, but the bonus halved prices: no fake exit
    assert thesis(led, held["thesis_id"])["status"] == "ACTIVE"
    assert led.positions()[held["thesis_id"]]["blocked"]
    order = next(o for o in led.orders() if o["order_id"] == pending["order_id"])
    assert order["status"] == "CANCELLED"
    assert "corporate action" in order["status_reason"]
    assert any("Bonus" in w for w in result["warnings"])


def test_state_mismatch_blocks_position(ledger):
    order = ledger.place_entry("RELIANCE", 10, stop=900, placed_at=placed())
    ledger.process(date(2026, 11, 23))
    ledger.thesis_store.terminate(
        ledger.theses_dir,
        order["thesis_id"],
        "INVALIDATED",
        "manual",
        event_date="2026-11-23T12:00:00+00:00",
    )
    result = ledger.process(date(2026, 11, 24))
    assert any("does not match" in w for w in result["warnings"])
    assert ledger.positions()[order["thesis_id"]]["blocked"]


# --------------------------------------------------------------------------- snapshot / reports


def test_snapshot_marks_to_market(ledger):
    a = ledger.place_entry("RELIANCE", 50, stop=900, placed_at=placed())
    ledger.place_entry(
        "M&M", 10, order_type="LIMIT", limit=2950, stop=2800, valid_sessions=5, placed_at=placed()
    )
    ledger.process(date(2026, 11, 24))
    snap = ledger.snapshot(date(2026, 11, 24))
    assert snap["as_of"] == "2026-11-24"
    assert snap["currency"] == "INR"
    (pos,) = snap["positions"]
    assert pos["thesis_id"] == a["thesis_id"]
    assert pos["last_close"] == 1025.0
    assert pos["market_value"] == 51_250.0
    assert pos["unrealized_net"] < pos["unrealized_gross"] == 750.0
    assert pos["open_risk"] > 0
    assert len(snap["open_orders"]) == 1
    acct = snap["account"]
    assert acct["equity"] == pytest.approx(acct["cash"] + 51_250.0)
    assert acct["charges_paid"] > 0
    assert acct["realized_pnl"] == 0
    md = pl.render_markdown(snap)
    assert "RELIANCE" in md and "₹" in md and "M&M" in md


def test_write_reports(ledger, tmp_path):
    ledger.place_entry("RELIANCE", 10, stop=900, placed_at=placed())
    ledger.process(date(2026, 11, 23))
    snap = ledger.snapshot(date(2026, 11, 23))
    json_path, md_path = pl.write_reports(snap, tmp_path / "reports")
    assert json_path.name == "india_paper_ledger_2026-11-23.json"
    assert json.loads(json_path.read_text())["positions"][0]["symbol"] == "RELIANCE"
    assert md_path.read_text().startswith("# India Paper Ledger")


def test_lock_prevents_concurrent_runs(ledger):
    ledger.state_dir.joinpath(".lock").write_text("123")
    with pytest.raises(pl.LedgerError, match="lock"):
        ledger.process(date(2026, 11, 23))


def test_format_inr():
    assert pl.format_inr(1234567.5) == "₹12,34,567.50"
    assert pl.format_inr(-950) == "-₹950.00"
    assert pl.format_inr(None) == "—"
