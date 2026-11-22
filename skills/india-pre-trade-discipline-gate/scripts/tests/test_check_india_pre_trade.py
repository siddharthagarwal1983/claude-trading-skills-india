"""Tests for the India pre-trade discipline gate (reuses the US gate engine)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import check_india_pre_trade as gate
import pytest
import yaml

IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = "2026-11-22T21:00:00+05:30"  # Sunday evening; plans are for Monday 23 Nov


def weekdays(start: date, end: date) -> list:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def actionable(symbol="BHEL", qty=113, trigger=436.4, limit=440.3, stop=408.15, risk=3755.69):
    return {
        "symbol": symbol,
        "plan_valid_for_session": "2026-11-23",
        "trade_plan": {
            "shares": qty,
            "risk_inr": risk,
            "worst_entry": limit,
            "stop_loss_price": stop,
        },
        "paper_order": {
            "symbol": symbol,
            "qty": qty,
            "order_type": "STOP_LIMIT",
            "trigger": trigger,
            "limit": limit,
            "stop": stop,
            "target": 504.6,
            "valid_sessions": 1,
            "thesis_type": "pivot_breakout",
            "thesis": f"VCP breakout in {symbol}",
        },
        "warnings": [],
    }


@pytest.fixture
def env(tmp_path):
    """A clean, passing setup; tests break one piece at a time."""
    plan = {
        "schema_version": "1.0",
        "skill": "india-breakout-trade-planner",
        "plan_valid_for_session": "2026-11-23",
        "parameters": {"brokerage_per_order": 0.0, "dp_charge": 15.93},
        "actionable_orders": [actionable()],
    }
    paths = {
        "plan": tmp_path / "plan.json",
        "exposure": tmp_path / "exposure.json",
        "cb": tmp_path / "cb.json",
        "results": tmp_path / "results_calendar.json",
        "ledger": tmp_path / "paper",
        "theses": tmp_path / "paper" / "theses",
        "out": tmp_path / "reports",
        "journal": tmp_path / "journal",
    }
    paths["plan"].write_text(json.dumps(plan))
    paths["exposure"].write_text(
        json.dumps(
            {
                "skill": "india-exposure-coach",
                "as_of": "2026-11-20",
                "recommendation": "NEW_ENTRY_ALLOWED",
            }
        )
    )
    paths["cb"].write_text(
        json.dumps(
            {
                "skill": "india-drawdown-circuit-breaker",
                "as_of_date": "2026-11-22",
                "recommendation": "TRADING_ALLOWED",
            }
        )
    )
    paths["results"].write_text(
        json.dumps({"schema_version": 1, "events": [{"symbol": "TCS", "date": "2026-11-24"}]})
    )
    paths["theses"].mkdir(parents=True)
    paths["ledger"].joinpath("orders.json").write_text(json.dumps({"orders": []}))
    paths["ledger"].joinpath("positions.json").write_text(json.dumps({"positions": {}}))
    paths["plan_data"] = plan
    return paths


def run(env, *extra, plan=True):
    argv = [
        "--exposure-decision",
        str(env["exposure"]),
        "--circuit-breaker-decision",
        str(env["cb"]),
        "--results-calendar",
        str(env["results"]),
        "--ledger-state-dir",
        str(env["ledger"]),
        "--state-dir",
        str(env["theses"]),
        "--output-dir",
        str(env["out"]),
        "--journal-dir",
        str(env["journal"]),
        "--as-of",
        AS_OF,
    ]
    if plan:
        argv += ["--plan", str(env["plan"])]
    return gate.main(argv + list(extra), sessions_fn=weekdays)


def result(env):
    (path,) = sorted(env["out"].glob("india_pre_trade_decision_*.json"))
    return json.loads(path.read_text())


def rewrite_plan(env, **changes):
    plan = env["plan_data"]
    plan["actionable_orders"][0]["paper_order"].update(changes)
    env["plan"].write_text(json.dumps(plan))


# --------------------------------------------------------------------------- GO path


def test_clean_plan_is_go(env, capsys):
    assert run(env) == 0
    out = result(env)
    assert out["overall_decision"] == "GO"
    assert out["skill"] == "india-pre-trade-discipline-gate"
    (cand,) = out["candidate_results"]
    assert cand["symbol"] == "BHEL"
    assert cand["checklist_answers"]["planned_risk_dollars"] == 3755.69
    assert cand["checklist_answers"]["actual_risk_dollars"] == 3755.69
    assert out["india_checks"]["BHEL"]["results_date"] is None
    assert "Decision: GO" in capsys.readouterr().out
    journal = list(env["journal"].glob("*.jsonl"))
    assert len(journal) == 1


def test_markdown_report_has_india_section(env):
    run(env)
    (md,) = env["out"].glob("india_pre_trade_decision_*.md")
    text = md.read_text()
    assert text.startswith("# India Pre-Trade Discipline Decision")
    assert "₹3,755.69" in text


# --------------------------------------------------------------------------- plan checks


def test_edited_quantity_exceeds_planned_risk(env):
    rewrite_plan(env, qty=150)
    run(env)
    out = result(env)
    assert out["overall_decision"] == "NO_GO"
    reasons = " ".join(out["candidate_results"][0]["reasons"])
    assert "exceeds planned risk" in reasons
    assert "size is not confirmed within plan" in reasons


def test_missing_stop_is_no_go(env):
    rewrite_plan(env, stop=None)
    run(env)
    assert "stop is not predefined" in result(env)["candidate_results"][0]["reasons"]


def test_stop_above_trigger_is_no_go(env):
    rewrite_plan(env, stop=450.0)
    run(env)
    assert result(env)["overall_decision"] == "NO_GO"


def test_stale_plan_is_no_go(env):
    run(env, "--as-of", "2026-11-23T10:00:00+05:30")
    out = result(env)
    assert out["overall_decision"] == "NO_GO"
    assert any("re-run" in r for r in out["candidate_results"][0]["reasons"])


def test_symbol_filter(env):
    env["plan_data"]["actionable_orders"].append(actionable("SIEMENS"))
    env["plan"].write_text(json.dumps(env["plan_data"]))
    run(env, "--symbols", "SIEMENS")
    assert [c["symbol"] for c in result(env)["candidate_results"]] == ["SIEMENS"]


def test_plan_without_actionable_orders(env):
    env["plan_data"]["actionable_orders"] = []
    env["plan"].write_text(json.dumps(env["plan_data"]))
    run(env)
    assert result(env)["overall_decision"] == "NO_ACTIONABLE_ORDERS"


def test_wrong_plan_file_exits_1(env, capsys):
    env["plan"].write_text(json.dumps({"skill": "breakout-trade-planner"}))
    assert run(env) == 1
    assert "india-breakout-trade-planner" in capsys.readouterr().err


# --------------------------------------------------------------------------- ledger duplicates


def test_open_paper_order_blocks_duplicate(env):
    env["ledger"].joinpath("orders.json").write_text(
        json.dumps({"orders": [{"symbol": "BHEL", "status": "OPEN", "side": "BUY"}]})
    )
    run(env)
    out = result(env)
    assert out["overall_decision"] == "NO_GO"
    assert any("paper ledger" in r for r in out["candidate_results"][0]["reasons"])


def test_held_position_blocks_duplicate(env):
    env["ledger"].joinpath("positions.json").write_text(
        json.dumps({"positions": {"th_x": {"symbol": "BHEL", "qty": 10}}})
    )
    run(env)
    assert result(env)["overall_decision"] == "NO_GO"


# --------------------------------------------------------------------------- results risk


def test_results_within_buffer_needs_review(env):
    env["results"].write_text(json.dumps({"events": [{"symbol": "BHEL", "date": "2026-11-26"}]}))
    run(env)
    out = result(env)
    assert out["overall_decision"] == "REVIEW_REQUIRED"
    assert out["india_checks"]["BHEL"]["results_date"] == "2026-11-26"
    assert any("results" in r for r in out["candidate_results"][0]["reasons"])


def test_results_beyond_buffer_pass(env):
    env["results"].write_text(json.dumps({"events": [{"symbol": "BHEL", "date": "2026-12-15"}]}))
    run(env)
    assert result(env)["overall_decision"] == "GO"


def test_results_risk_can_be_accepted(env):
    env["results"].write_text(json.dumps({"events": [{"symbol": "BHEL", "date": "2026-11-26"}]}))
    run(env, "--accept-results-risk", "BHEL")
    out = result(env)
    assert out["overall_decision"] == "GO"
    assert out["india_checks"]["BHEL"]["results_risk_accepted"] is True


def test_missing_results_calendar_warns(env):
    env["results"].unlink()
    run(env)
    out = result(env)
    assert out["overall_decision"] == "GO"
    assert any("results calendar" in w for w in out["warnings"])


# --------------------------------------------------------------------------- upstream artifacts


def test_us_circuit_breaker_artifact_needs_review(env):
    env["cb"].write_text(
        json.dumps({"as_of_date": "2026-11-22", "recommendation": "TRADING_ALLOWED"})
    )
    run(env)
    out = result(env)
    assert out["overall_decision"] == "REVIEW_REQUIRED"
    assert any(
        "india-drawdown-circuit-breaker" in r for r in out["candidate_results"][0]["reasons"]
    )


def test_stale_exposure_artifact_needs_review(env):
    env["exposure"].write_text(
        json.dumps(
            {
                "skill": "india-exposure-coach",
                "as_of": "2026-11-13",
                "recommendation": "NEW_ENTRY_ALLOWED",
            }
        )
    )
    run(env)
    out = result(env)
    assert out["overall_decision"] == "REVIEW_REQUIRED"
    assert any("stale" in r for r in out["candidate_results"][0]["reasons"])


def test_reduce_only_posture_is_no_go(env):
    env["exposure"].write_text(
        json.dumps(
            {
                "skill": "india-exposure-coach",
                "as_of": "2026-11-20",
                "recommendation": "REDUCE_ONLY",
            }
        )
    )
    run(env)
    assert result(env)["overall_decision"] == "NO_GO"


def test_halted_circuit_breaker_is_no_go(env):
    env["cb"].write_text(
        json.dumps(
            {
                "skill": "india-drawdown-circuit-breaker",
                "as_of_date": "2026-11-22",
                "recommendation": "HALTED",
            }
        )
    )
    run(env)
    assert result(env)["overall_decision"] == "NO_GO"


def test_missing_artifacts_need_review(env):
    argv = [
        "--plan",
        str(env["plan"]),
        "--results-calendar",
        str(env["results"]),
        "--ledger-state-dir",
        str(env["ledger"]),
        "--output-dir",
        str(env["out"]),
        "--journal-dir",
        str(env["journal"]),
        "--as-of",
        AS_OF,
    ]
    gate.main(argv, sessions_fn=weekdays)
    assert result(env)["overall_decision"] == "REVIEW_REQUIRED"


# --------------------------------------------------------------------------- revenge window


def test_recent_paper_loss_blocks_revenge_trade(env):
    thesis = {
        "thesis_id": "th_tcs_pvt_20261120_abcd",
        "ticker": "TCS",
        "status": "CLOSED",
        "status_history": [
            {
                "status": "CLOSED",
                "at": "2026-11-22T10:00:00+00:00",
                "shares_sold": 10,
                "price": 2300.0,
                "realized_pnl": -1500.0,
            }
        ],
        "outcome": {"pnl_dollars": -1500.0},
    }
    env["theses"].joinpath("th_tcs_pvt_20261120_abcd.yaml").write_text(yaml.safe_dump(thesis))
    run(env)
    out = result(env)
    assert out["overall_decision"] == "NO_GO"
    assert any("recent losing exit" in r for r in out["candidate_results"][0]["reasons"])


# --------------------------------------------------------------------------- answers file


def test_answers_file_with_rupee_aliases(env, tmp_path):
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "candidates": [
                    {
                        "symbol": "INFY",
                        "order_intent": "MANUAL_ORDER",
                        "entry_in_written_plan": True,
                        "stop_predefined": True,
                        "size_within_plan": True,
                        "planned_risk_inr": 4000,
                        "actual_risk_inr": 3900,
                    }
                ]
            }
        )
    )
    run(env, "--answers-file", str(answers), plan=False)
    out = result(env)
    assert out["overall_decision"] == "GO"
    assert out["candidate_results"][0]["checklist_answers"]["actual_risk_dollars"] == 3900


def test_needs_plan_or_answers(env, capsys):
    assert run(env, plan=False) == 1
    assert "--plan" in capsys.readouterr().err


# --------------------------------------------------------------------------- CLI behaviour


def test_fail_on_non_go_exit_2(env):
    rewrite_plan(env, stop=None)
    assert run(env, "--fail-on-non-go") == 2


def test_missing_us_engine_exits_2(env, monkeypatch, capsys):
    monkeypatch.setenv("INDIA_PRE_TRADE_US_GATE_DIR", str(env["out"] / "nowhere"))
    monkeypatch.setattr(gate, "_ENGINE", None)
    assert run(env) == 2
    assert "pre-trade-discipline-gate" in capsys.readouterr().err


def test_parse_as_of_is_ist():
    parsed = gate.parse_as_of("2026-11-22")
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)
    assert parsed.hour == 23
    assert gate.parse_as_of(None).tzinfo is not None
    assert gate.parse_as_of("2026-11-22T09:00:00+00:00").hour == 14


def test_plan_risk_recompute_matches_planner_formula():
    order = actionable()["paper_order"]
    assert gate.order_risk_inr(order, brokerage=0.0, dp_charge=15.93) == 3755.69


def test_india_checks_do_not_break_engine_link_step(env):
    # thesis_id present -> engine links the report into trader-memory-core state.
    ts_path = (
        Path(__file__).resolve().parents[3] / "trader-memory-core" / "scripts" / "thesis_store.py"
    )
    if not ts_path.exists():
        pytest.skip("trader-memory-core not present")
    answers = env["out"].parent / "answers.json"
    answers.write_text(
        json.dumps(
            [
                {
                    "symbol": "INFY",
                    "thesis_id": "th_missing_pvt_20261122_abcd",
                    "order_intent": "ENTRY_READY",
                    "entry_in_written_plan": True,
                    "stop_predefined": True,
                    "size_within_plan": True,
                    "planned_risk_inr": 100,
                    "actual_risk_inr": 100,
                }
            ]
        )
    )
    run(env, "--answers-file", str(answers), plan=False)
    out = result(env)
    assert out["candidate_results"][0]["link_status"] == "failed"
    assert out["overall_decision"] == "REVIEW_REQUIRED"
    assert datetime.fromisoformat(out["as_of"]).utcoffset() == timedelta(hours=5, minutes=30)
