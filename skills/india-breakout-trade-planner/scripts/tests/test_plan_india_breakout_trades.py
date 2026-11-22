"""Planner tests: Minervini gate on India VCP output, ledger link, outputs, CLI."""

import json
from datetime import date, datetime, timedelta, timezone

import plan_india_breakout_trades as planner
import pytest

IST = timezone(timedelta(hours=5, minutes=30))


def weekdays(start, end):
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def vcp(
    symbol,
    score=75.0,
    state="Pre-breakout",
    valid=True,
    pivot=435.95,
    low=418.0,
    price=433.65,
    sector="Capital Goods",
    turnover=400.0,
    breakout_volume=False,
    distance=-0.5,
):
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Ltd.",
        "sector": sector,
        "price": price,
        "composite_score": score,
        "valid_vcp": valid,
        "execution_state": state,
        "avg_turnover_cr": turnover,
        "vcp_pattern": {
            "pivot_price": pivot,
            "contractions": [
                {"label": "T1", "low_price": low - 20},
                {"label": "T2", "low_price": low},
            ],
        },
        "volume_pattern": {"breakout_volume_detected": breakout_volume},
        "pivot_proximity": {"distance_from_pivot_pct": distance},
    }


def report(*results, as_of="2026-11-20"):
    return {
        "schema_version": "1.0",
        "metadata": {
            "skill": "india-vcp-screener",
            "exchange": "NSE",
            "currency": "INR",
            "as_of": as_of,
            "generated_at": f"{as_of} 20:00:00",
        },
        "results": list(results),
    }


AS_OF = datetime(2026, 11, 20, 20, 0, tzinfo=IST)  # Friday evening


def plan(*results, ledger=None, as_of=AS_OF, **params):
    params.setdefault("account_size", 1_000_000)
    return planner.generate_plans(
        report(*results),
        planner.PlanParams(**params),
        as_of=as_of,
        ledger=ledger,
        sessions_fn=weekdays,
        input_path="reports/india_vcp.json",
    )


# --------------------------------------------------------------------------- input


def test_load_input_requires_india_report(tmp_path):
    us = tmp_path / "us.json"
    us.write_text(json.dumps({"schema_version": "1.0", "metadata": {}, "results": []}))
    with pytest.raises(ValueError, match="INR"):
        planner.load_input(str(us))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "2.0", "metadata": {"currency": "INR"}}))
    with pytest.raises(ValueError, match="schema_version"):
        planner.load_input(str(bad))
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(report(vcp("BHEL"))))
    assert planner.load_input(str(ok))["results"][0]["symbol"] == "BHEL"


# --------------------------------------------------------------------------- gate


def test_actionable_prebreakout_builds_paper_order():
    out = plan(vcp("BHEL", score=72.5))
    (order,) = out["actionable_orders"]
    tp = order["trade_plan"]
    assert tp["signal_entry"] == 436.4
    assert tp["worst_entry"] == 444.65
    assert tp["stop_loss_price"] == 413.8
    assert tp["sizing_multiplier"] == 0.75
    assert tp["shares"] > 0
    assert tp["risk_inr"] <= 1_000_000 * 0.5 * 0.75 / 100
    assert tp["target_price"] == 506.35  # 2R from the worst entry
    po = order["paper_order"]
    assert po == {
        "symbol": "BHEL",
        "qty": tp["shares"],
        "order_type": "STOP_LIMIT",
        "trigger": 436.4,
        "limit": 444.65,
        "stop": 413.8,
        "target": 506.35,
        "valid_sessions": 1,
        "thesis_type": "pivot_breakout",
        "thesis": po["thesis"],
    }
    assert "435.95" in po["thesis"]
    assert "--type stop-limit" in order["paper_command"]
    assert order["broker_order"]["product"] == "CNC"
    assert order["broker_order"]["order_type"] == "SL"
    assert order["plan_valid_for_session"] == "2026-11-23"  # next Monday
    assert out["summary"]["actionable_count"] == 1


def test_paper_command_quotes_ampersand_symbols():
    out = plan(vcp("M&M", score=85, pivot=3500, low=3400, price=3480))
    assert "--symbol 'M&M'" in out["actionable_orders"][0]["paper_command"]


def test_developing_score_goes_to_watchlist():
    out = plan(vcp("ETERNAL", score=65))
    assert out["watchlist"][0]["symbol"] == "ETERNAL"
    assert "₹435.95" in out["watchlist"][0]["alert_trigger"]


def test_weak_and_invalid_rejected():
    out = plan(vcp("A", score=50), vcp("B", valid=False))
    reasons = {r["symbol"]: r["reason"] for r in out["rejected"]}
    assert "rating_band=weak" in reasons["A"]
    assert "valid_vcp=False" in reasons["B"]


def test_wide_stop_rejected():
    out = plan(vcp("WIDE", pivot=100, low=88, price=99))
    assert "risk_pct_worst" in out["rejected"][0]["reason"]


def test_breakout_state_needs_revalidation_or_is_rejected():
    ok = plan(vcp("BRK", state="Breakout", breakout_volume=True, distance=1.0, price=440))
    assert ok["revalidation"][0]["decision_code"] == "REVALIDATION_BREAKOUT"
    no_vol = plan(vcp("BRK", state="Breakout", breakout_volume=False, distance=1.0, price=440))
    assert "no breakout volume" in no_vol["rejected"][0]["reason"]
    chased = plan(vcp("BRK", state="Breakout", breakout_volume=True, distance=3.0, price=449))
    assert "distance" in chased["rejected"][0]["reason"]


def test_missing_fields_rejected_with_warning():
    broken = vcp("BRK")
    del broken["vcp_pattern"]
    out = plan(broken)
    assert out["rejected"][0]["symbol"] == "BRK"
    assert out["warnings"][0]["code"] == "MISSING_FIELD"


def test_portfolio_heat_defers_later_candidates():
    out = plan(
        vcp("A", score=95),
        vcp("B", score=94),
        vcp("C", score=93),
        vcp("D", score=92),
        max_portfolio_heat_pct=2.0,
        risk_pct=0.5,
        max_position_pct=100,
        max_sector_pct=100,
    )
    # textbook: 0.875% each -> two fit under 2%, the rest defer
    assert [o["symbol"] for o in out["actionable_orders"]] == ["A", "B"]
    assert {d["symbol"] for d in out["deferred"]} == {"C", "D"}


def test_sector_room_shrinks_with_each_plan():
    out = plan(
        vcp("A", score=95),
        vcp("B", score=94),
        risk_pct=5,
        max_position_pct=20,
        max_sector_pct=30,
        max_portfolio_heat_pct=50,
    )
    a, b = out["actionable_orders"]
    assert a["trade_plan"]["binding_constraint"] == "max_position_pct"
    assert b["trade_plan"]["binding_constraint"] == "max_sector_pct"
    assert b["trade_plan"]["position_value"] < a["trade_plan"]["position_value"]


def test_illiquid_stock_constrained_to_zero():
    out = plan(vcp("THIN", score=85, turnover=0.0001))
    assert out["constrained"][0]["symbol"] == "THIN"
    assert "max_adv_pct" in out["constrained"][0]["reason"]


# --------------------------------------------------------------------------- warnings


def test_far_trigger_warns_about_price_band():
    out = plan(vcp("GAP", score=85, price=410, pivot=435.95, low=418))
    assert any("price band" in w for w in out["actionable_orders"][0]["warnings"])


def test_stale_screener_report_warns():
    late = datetime(2026, 11, 25, 20, 0, tzinfo=IST)
    out = plan(vcp("BHEL"), as_of=late)
    assert any(w["code"] == "STALE_SCREENER" for w in out["warnings"])


def test_plan_session_before_open_is_same_day():
    morning = datetime(2026, 11, 23, 8, 30, tzinfo=IST)
    out = plan(vcp("BHEL"), as_of=morning)
    assert out["actionable_orders"][0]["plan_valid_for_session"] == "2026-11-23"


# --------------------------------------------------------------------------- ledger link


def ledger_report(tmp_path, **account):
    snap = {
        "skill": "india-paper-ledger",
        "as_of": "2026-11-20",
        "currency": "INR",
        "account": {"equity": 500_000.0, "cash": 30_000.0, "open_risk_pct": 1.0, **account},
        "positions": [{"symbol": "BHEL", "market_value": 60_000.0}],
        "open_orders": [{"symbol": "TCS", "side": "BUY", "status": "OPEN"}],
    }
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(snap))
    return planner.load_ledger_report(str(path))


def test_load_ledger_report_rejects_other_files(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"skill": "something-else"}))
    with pytest.raises(ValueError, match="india-paper-ledger"):
        planner.load_ledger_report(str(path))


def test_ledger_supplies_equity_heat_cash_and_skips_held(tmp_path):
    ledger = ledger_report(tmp_path)
    out = planner.generate_plans(
        report(
            vcp("BHEL", score=95),
            vcp("TCS", score=94, sector="IT"),
            vcp("LT", score=93, pivot=3500, low=3400, price=3480),
        ),
        planner.PlanParams(account_size=None),
        as_of=AS_OF,
        ledger=ledger,
        sessions_fn=weekdays,
    )
    assert out["parameters"]["account_size"] == 500_000.0
    assert out["parameters"]["starting_open_risk_pct"] == 1.0
    assert {s["symbol"] for s in out["skipped"]} == {"BHEL", "TCS"}
    (lt,) = out["actionable_orders"]
    assert lt["trade_plan"]["binding_constraint"] == "cash"
    assert lt["trade_plan"]["position_value"] <= 30_000


def test_ledger_sector_exposure_counts_held_positions(tmp_path):
    ledger = ledger_report(tmp_path, cash=500_000.0)
    out = planner.generate_plans(
        report(vcp("BHEL", score=95), vcp("SIEMENS", score=94, sector="Capital Goods")),
        planner.PlanParams(account_size=None, risk_pct=5, max_position_pct=50, max_sector_pct=15),
        as_of=AS_OF,
        ledger=ledger,
        sessions_fn=weekdays,
    )
    # BHEL is 12% of ₹5 lakh in Capital Goods; 15% cap leaves 3% = ₹15,000
    (order,) = out["actionable_orders"]
    assert order["trade_plan"]["binding_constraint"] == "max_sector_pct"
    assert order["trade_plan"]["position_value"] <= 15_000


def test_account_size_required_without_ledger():
    with pytest.raises(ValueError, match="account"):
        planner.generate_plans(
            report(vcp("A")),
            planner.PlanParams(account_size=None),
            as_of=AS_OF,
            sessions_fn=weekdays,
        )


# --------------------------------------------------------------------------- outputs / CLI


def test_markdown_report():
    out = plan(vcp("BHEL", score=85), vcp("ETERNAL", score=65), vcp("WEAK", score=40))
    md = planner.generate_markdown(out)
    assert md.startswith("# India Breakout Trade Plan")
    assert "₹4,36" not in md  # Indian grouping only from ₹1 lakh up
    assert "₹436.40" in md
    assert "india_paper_ledger.py buy" in md
    assert "ETERNAL" in md


def test_cli_writes_reports(tmp_path, capsys):
    src = tmp_path / "vcp.json"
    src.write_text(json.dumps(report(vcp("BHEL", score=85))))
    out_dir = tmp_path / "reports"
    code = planner.main(
        [
            "--input",
            str(src),
            "--account-size",
            "1000000",
            "--output-dir",
            str(out_dir),
            "--as-of",
            "2026-11-20T20:00:00+05:30",
        ],
        sessions_fn=weekdays,
    )
    assert code == 0
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == [
        "india_breakout_trade_plan_2026-11-20_200000.json",
        "india_breakout_trade_plan_2026-11-20_200000.md",
    ]
    saved = json.loads((out_dir / files[0]).read_text())
    assert saved["skill"] == "india-breakout-trade-planner"
    assert "Actionable: 1" in capsys.readouterr().out


def test_cli_errors_exit_1(tmp_path, capsys):
    src = tmp_path / "vcp.json"
    src.write_text(json.dumps(report(vcp("BHEL"))))
    assert planner.main(["--input", str(src)], sessions_fn=weekdays) == 1
    assert "account" in capsys.readouterr().err
    assert planner.main(["--input", str(tmp_path / "missing.json"), "--account-size", "1"]) == 1


def test_cli_rejects_naive_as_of(tmp_path):
    src = tmp_path / "vcp.json"
    src.write_text(json.dumps(report(vcp("BHEL"))))
    with pytest.raises(SystemExit):
        planner.main(["--input", str(src), "--account-size", "1", "--as-of", "2026-11-20T20:00"])


def test_resolve_as_of_date_only_is_ist_evening():
    assert planner.resolve_as_of("2026-11-20") == datetime(2026, 11, 20, 20, 0, tzinfo=IST)
    assert planner.resolve_as_of(None).tzinfo is not None
    assert planner.resolve_as_of("2026-11-20T09:00:00+05:30").hour == 9


def test_plan_valid_session_skips_holidays():
    def sessions(start, end):
        return [d for d in weekdays(start, end) if d != date(2026, 11, 23)]

    assert planner.plan_valid_session(AS_OF, sessions) == date(2026, 11, 24)
