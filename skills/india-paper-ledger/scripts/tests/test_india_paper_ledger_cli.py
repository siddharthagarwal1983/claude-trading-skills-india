"""CLI tests for india_paper_ledger.py (fake bars, real trader-memory-core)."""

import argparse
import json

import india_paper_ledger as cli
import paper_ledger as pl
import pytest
from india_paper_fakes import FakeBars, bar


@pytest.fixture
def env(tmp_path):
    fb = FakeBars(
        {
            "RELIANCE": {
                "bars": [
                    bar("2026-11-23", 1000, 1020, 995, 1010),
                    bar("2026-11-24", 1012, 1030, 1005, 1025),
                ]
            }
        }
    )
    state = tmp_path / "paper"
    reports = tmp_path / "reports"

    def factory(args):
        return pl.PaperLedger(state, bars_fn=fb)

    def run(*argv):
        return cli.main(list(argv), ledger_factory=factory)

    return run, state, reports


def test_full_cli_flow(env, capsys, tmp_path):
    run, state, reports = env
    assert run("init", "--capital", "500000", "--slippage-bps", "5") == 0
    assert (
        run(
            "buy",
            "--symbol",
            "RELIANCE",
            "--qty",
            "20",
            "--stop",
            "960",
            "--target",
            "1200",
            "--placed-at",
            "2026-11-20T20:00+05:30",
        )
        == 0
    )
    capsys.readouterr()
    assert run("orders", "--status", "OPEN") == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1 and listed[0]["symbol"] == "RELIANCE"
    tid = listed[0]["thesis_id"]

    assert run("process", "--as-of", "2026-11-23", "--output-dir", str(reports)) == 0
    out = capsys.readouterr().out
    assert "Processed 1 session(s)" in out
    assert (reports / "india_paper_ledger_2026-11-23.md").exists()

    assert run("set-exit", "--thesis-id", tid, "--stop", "1000") == 0
    assert json.loads(capsys.readouterr().out)["stop"] == 1000
    assert (
        run("sell", "--thesis-id", tid, "--qty", "5", "--placed-at", "2026-11-24T10:00+05:30") == 0
    )
    capsys.readouterr()
    assert run("process", "--as-of", "2026-11-24", "--no-report") == 0
    assert "SELL" in capsys.readouterr().out
    assert run("status", "--as-of", "2026-11-24", "--output-dir", str(reports)) == 0
    snap = json.loads((reports / "india_paper_ledger_2026-11-24.json").read_text())
    assert snap["positions"][0]["qty"] == 15


def test_buy_from_sizer_report(env, tmp_path, capsys):
    run, _, _ = env
    run("init", "--capital", "500000")
    report = tmp_path / "sizer.json"
    report.write_text(
        json.dumps(
            {
                "mode": "shares",
                "final_recommended_shares": 12,
                "parameters": {"entry_price": 1028.0, "stop_price": 990.0},
            }
        )
    )
    capsys.readouterr()
    assert (
        run(
            "buy",
            "--symbol",
            "RELIANCE",
            "--sizer-report",
            str(report),
            "--type",
            "stop",
            "--placed-at",
            "2026-11-20T20:00+05:30",
        )
        == 0
    )
    order = json.loads(capsys.readouterr().out)
    assert order["qty"] == 12
    assert order["trigger"] == 1028.0
    assert order["stop"] == 990.0
    assert order["source_report"] == str(report)


def test_sizer_report_mismatch_and_budget_mode(env, tmp_path, capsys):
    run, _, _ = env
    run("init", "--capital", "500000")
    report = tmp_path / "sizer.json"
    report.write_text(
        json.dumps(
            {"mode": "shares", "final_recommended_shares": 12, "parameters": {"stop_price": 990}}
        )
    )
    assert run("buy", "--symbol", "RELIANCE", "--sizer-report", str(report), "--qty", "5") == 1
    assert "differs" in capsys.readouterr().err
    report.write_text(json.dumps({"mode": "budget"}))
    assert run("buy", "--symbol", "RELIANCE", "--sizer-report", str(report)) == 1
    assert "shares mode" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv,message",
    [
        (("buy", "--symbol", "RELIANCE", "--stop", "900"), "--qty"),
        (("buy", "--symbol", "RELIANCE", "--qty", "5"), "--stop"),
        (("cancel", "--order-id", "po_missing"), "No paper order"),
        (("sell", "--thesis-id", "th_x"), "no open paper position"),
    ],
)
def test_errors_exit_1(env, capsys, argv, message):
    run, _, _ = env
    run("init", "--capital", "100000")
    capsys.readouterr()
    assert run(*argv) == 1
    assert message in capsys.readouterr().err


def test_uninitialized_exit_1(env, capsys):
    run, _, _ = env
    assert run("orders") == 0  # listing works without an account
    assert run("process", "--as-of", "2026-11-23", "--no-report") == 1
    assert "init" in capsys.readouterr().err


def test_missing_dependency_exit_2(env, capsys):
    run, _, _ = env

    def broken(args):
        raise ImportError("No module named 'jsonschema'")

    assert cli.main(["orders"], ledger_factory=broken) == 2
    assert "requirements.txt" in capsys.readouterr().err


def test_placed_at_needs_timezone():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["buy", "--symbol", "X", "--qty", "1", "--stop", "1", "--placed-at", "2026-11-20T10:00"]
        )


def test_finite_float_rejects_nan():
    with pytest.raises(argparse.ArgumentTypeError):
        cli.finite_float("nan")


def test_place_plan_cli(env, tmp_path, capsys):
    run, _, _ = env
    run("init", "--capital", "500000")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "skill": "india-breakout-trade-planner",
                "plan_valid_for_session": "2026-11-23",
                "actionable_orders": [
                    {
                        "symbol": "RELIANCE",
                        "plan_valid_for_session": "2026-11-23",
                        "paper_order": {
                            "symbol": "RELIANCE",
                            "qty": 3,
                            "order_type": "STOP_LIMIT",
                            "trigger": 1028.0,
                            "limit": 1040.0,
                            "stop": 990.0,
                            "target": None,
                            "valid_sessions": 1,
                            "thesis_type": "pivot_breakout",
                            "thesis": "plan",
                        },
                    }
                ],
            }
        )
    )
    capsys.readouterr()
    assert run("place-plan", "--plan", str(plan), "--placed-at", "2026-11-20T20:00+05:30") == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["placed"]) == 1 and out["skipped"] == []
