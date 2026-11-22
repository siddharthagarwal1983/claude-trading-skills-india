"""Pure fill-rule, charge and eligibility tests for the India paper ledger."""

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import paper_engine as eng
import pytest

IST = timezone(timedelta(hours=5, minutes=30))


def bar(o, h, l, c, day="2026-11-23"):  # noqa: E741
    return {"date": day, "open": o, "high": h, "low": l, "close": c}


# --------------------------------------------------------------------------- tickers


@pytest.mark.parametrize(
    "symbol,expected",
    [("RELIANCE", "RELIANCE"), ("M&M", "MANDM"), ("BAJAJ-AUTO", "BAJAJAUTO"), ("l&tfh", "LANDTFH")],
)
def test_ledger_ticker_is_alphanumeric(symbol, expected):
    assert eng.ledger_ticker(symbol) == expected


def test_ledger_ticker_rejects_empty():
    with pytest.raises(ValueError):
        eng.ledger_ticker("--")


# --------------------------------------------------------------------------- entry fills


def test_close_order_fills_at_close():
    assert eng.entry_fill("CLOSE", bar(100, 110, 95, 104)) == {"price": 104.0, "basis": "close"}


def test_limit_fills_at_limit_when_low_reaches_it():
    assert eng.entry_fill("LIMIT", bar(100, 110, 95, 104), limit=97) == {
        "price": 97.0,
        "basis": "limit",
    }


def test_limit_gap_down_fills_at_open():
    assert eng.entry_fill("LIMIT", bar(90, 92, 88, 91), limit=97)["price"] == 90.0


def test_limit_not_reached():
    assert eng.entry_fill("LIMIT", bar(100, 110, 98, 104), limit=97) is None


def test_stop_entry_fills_at_trigger():
    assert eng.entry_fill("STOP", bar(100, 110, 95, 104), trigger=105) == {
        "price": 105.0,
        "basis": "trigger",
    }


def test_stop_entry_gap_up_fills_at_open():
    assert eng.entry_fill("STOP", bar(108, 112, 107, 110), trigger=105) == {
        "price": 108.0,
        "basis": "open",
    }


def test_stop_entry_not_triggered():
    assert eng.entry_fill("STOP", bar(100, 104.95, 95, 104), trigger=105) is None


def test_stop_limit_skips_gap_above_cap():
    assert eng.entry_fill("STOP_LIMIT", bar(109, 112, 107, 110), trigger=105, limit=107) is None


def test_stop_limit_fills_gap_inside_cap():
    assert eng.entry_fill("STOP_LIMIT", bar(106, 112, 104, 110), trigger=105, limit=107) == {
        "price": 106.0,
        "basis": "open",
    }


def test_stop_limit_fills_at_trigger_intraday():
    assert eng.entry_fill("STOP_LIMIT", bar(100, 112, 99, 110), trigger=105, limit=107) == {
        "price": 105.0,
        "basis": "trigger",
    }


def test_unknown_order_type():
    with pytest.raises(ValueError):
        eng.entry_fill("MARKET", bar(1, 1, 1, 1))


# --------------------------------------------------------------------------- exit fills


def test_intraday_stop_hit():
    assert eng.exit_fill(bar(100, 101, 89, 95), stop=90, target=120) == {
        "price": 90.0,
        "basis": "stop",
        "exit_reason": "stop_hit",
    }


def test_intraday_stop_gap_down_fills_at_open():
    assert eng.exit_fill(bar(85, 88, 84, 86), stop=90, target=120)["price"] == 85.0


def test_target_hit():
    assert eng.exit_fill(bar(115, 125, 112, 118), stop=90, target=120) == {
        "price": 120.0,
        "basis": "target",
        "exit_reason": "target_hit",
    }


def test_target_gap_up_fills_at_open():
    assert eng.exit_fill(bar(126, 130, 124, 128), stop=90, target=120)["price"] == 126.0


def test_both_hit_same_bar_assumes_stop_first():
    result = eng.exit_fill(bar(100, 125, 85, 110), stop=90, target=120)
    assert result["exit_reason"] == "stop_hit"
    assert result["price"] == 90.0


def test_close_basis_stop_ignores_intraday_breach():
    assert eng.exit_fill(bar(100, 101, 85, 92), stop=90, target=None, stop_basis="close") is None


def test_close_basis_stop_exits_at_close():
    assert eng.exit_fill(bar(100, 101, 85, 88), stop=90, target=None, stop_basis="close") == {
        "price": 88.0,
        "basis": "close",
        "exit_reason": "stop_hit",
    }


def test_no_exit_levels():
    assert eng.exit_fill(bar(100, 200, 1, 100), stop=None, target=None) is None


def test_bad_stop_basis():
    with pytest.raises(ValueError):
        eng.exit_fill(bar(1, 1, 1, 1), stop=1, target=None, stop_basis="weekly")


# --------------------------------------------------------------------------- locked / slippage


def test_locked_bar_detection():
    assert eng.is_locked(bar(100, 100, 100, 100))
    assert not eng.is_locked(bar(100, 101, 100, 100))


def test_slippage_is_adverse():
    assert eng.apply_slippage(100.0, 10, "BUY") == 100.1
    assert eng.apply_slippage(100.0, 10, "SELL") == 99.9
    assert eng.apply_slippage(100.0, 0, "BUY") == 100.0


def test_slippage_rejects_negative():
    with pytest.raises(ValueError):
        eng.apply_slippage(100.0, -1, "BUY")


# --------------------------------------------------------------------------- eligibility


def test_close_order_placed_before_1500_ist_eligible_same_day():
    placed = datetime(2026, 11, 23, 14, 0, tzinfo=IST)
    assert eng.first_eligible_date("CLOSE", placed) == date(2026, 11, 23)


def test_close_order_placed_after_1500_waits_a_day():
    placed = datetime(2026, 11, 23, 15, 5, tzinfo=IST)
    assert eng.first_eligible_date("CLOSE", placed) == date(2026, 11, 24)


def test_trigger_order_placed_before_open_eligible_same_day():
    placed = datetime(2026, 11, 23, 9, 0, tzinfo=IST)
    assert eng.first_eligible_date("STOP", placed) == date(2026, 11, 23)


def test_trigger_order_placed_intraday_waits_a_day():
    placed = datetime(2026, 11, 23, 9, 20, tzinfo=IST)
    assert eng.first_eligible_date("LIMIT", placed) == date(2026, 11, 24)


def test_eligibility_converts_utc_to_ist():
    placed = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)  # 13:30 IST
    assert eng.first_eligible_date("CLOSE", placed) == date(2026, 11, 23)


def test_eligibility_requires_aware_datetime():
    with pytest.raises(ValueError):
        eng.first_eligible_date("CLOSE", datetime(2026, 11, 23, 9, 0))


# --------------------------------------------------------------------------- charges


def test_buy_leg_charges():
    leg = eng.leg_charges(100_000, buy=True)
    assert leg["stt"] == 100.0
    assert leg["stamp_duty"] == 15.0
    assert leg["exchange_txn"] == 2.97
    assert leg["sebi_fee"] == 0.1
    assert leg["gst"] == 0.55
    assert leg["dp_charge"] == 0.0
    assert leg["total"] == 118.62


def test_sell_leg_adds_dp_and_no_stamp():
    leg = eng.leg_charges(100_000, buy=False, dp_charge=15.93)
    assert leg["stamp_duty"] == 0.0
    assert leg["dp_charge"] == 15.93
    assert leg["total"] == 119.55


def test_brokerage_attracts_gst():
    leg = eng.leg_charges(100_000, buy=True, brokerage=20)
    assert leg["brokerage"] == 20.0
    assert leg["gst"] == 4.15


def test_effective_price_includes_charges():
    assert eng.effective_price(100.0, 10, 5.0, side="BUY") == 100.5
    assert eng.effective_price(100.0, 10, 5.0, side="SELL") == 99.5


def test_effective_price_rejects_bad_qty():
    with pytest.raises(ValueError):
        eng.effective_price(100.0, 0, 5.0, side="BUY")


def _load_sizer():
    path = (
        Path(__file__).resolve().parents[3]
        / "india-position-sizer"
        / "scripts"
        / "india_position_sizer.py"
    )
    if not path.exists():
        pytest.skip("india-position-sizer not present (standalone install)")
    spec = importlib.util.spec_from_file_location("india_sizer_for_parity", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def test_charges_match_india_position_sizer():
    sizer = _load_sizer()
    assert {k: str(v) for k, v in sizer.CHARGES.items()} == {
        k: str(v) for k, v in eng.CHARGES.items()
    }
    assert sizer.DEFAULT_DP_CHARGE == eng.DEFAULT_DP_CHARGE
    costs = sizer.delivery_costs(37, 2950.5, 3011.25, brokerage_per_order=20, dp_charge=15.93)
    buy = eng.leg_charges(37 * 2950.5, buy=True, brokerage=20)
    sell = eng.leg_charges(37 * 3011.25, buy=False, brokerage=20, dp_charge=15.93)
    assert buy["total"] == pytest.approx(costs["buy"]["total"], abs=0.01)
    assert sell["total"] == pytest.approx(costs["sell"]["total"], abs=0.01)


# --------------------------------------------------------------------------- corporate actions


def test_dividend_per_share_sums_regular_and_special():
    assert eng.dividend_per_share("Dividend - Rs 6.75 Per Share /Special Dividend - Rs 2.75") == 9.5
    assert eng.dividend_per_share("Bonus 1:1") == 0.0


def test_classify_actions():
    actions = [
        {
            "ex_date": "2026-11-24",
            "action_type": "DIVIDEND",
            "purpose": "Dividend - Rs 5",
            "factor": None,
        },
        {"ex_date": "2026-11-25", "action_type": "BONUS", "purpose": "Bonus 1:1", "factor": 0.5},
        {"ex_date": "2026-11-26", "action_type": "OTHER", "purpose": "AGM", "factor": 1.0},
    ]
    div, adj = eng.split_actions(actions)
    assert [a["ex_date"] for a in div] == ["2026-11-24"]
    assert [a["ex_date"] for a in adj] == ["2026-11-25"]


def test_split_purpose_is_not_a_dividend_and_demerger_blocks():
    actions = [
        {
            "ex_date": "2026-11-24",
            "action_type": "SPLIT",
            "purpose": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share",
            "factor": 0.2,
        },
        {"ex_date": "2026-11-25", "action_type": "OTHER", "purpose": "Demerger", "factor": 1.0},
    ]
    div, adj = eng.split_actions(actions)
    assert div == []
    assert [a["ex_date"] for a in adj] == ["2026-11-24", "2026-11-25"]
