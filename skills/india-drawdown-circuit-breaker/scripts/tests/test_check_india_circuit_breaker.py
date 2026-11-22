"""Tests for the India drawdown circuit breaker (IST boundaries, NSE sessions)."""

from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import check_india_circuit_breaker as cb
import pytest
import yaml

HOLIDAYS: set = set()


def sessions(start: date, end: date) -> list:
    """Weekdays minus HOLIDAYS: a stand-in for the XBOM calendar."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture(autouse=True)
def clear_holidays():
    HOLIDAYS.clear()
    yield
    HOLIDAYS.clear()


def write_thesis(
    state_dir: Path,
    thesis_id: str,
    *,
    ticker: str = "RELIANCE",
    status: str = "CLOSED",
    history: list | None = None,
    pnl: float | None = None,
    exit_at: str | None = None,
) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    if history is None:
        history = []
        if status in {"CLOSED", "INVALIDATED"}:
            event = {"status": status, "at": exit_at or "2026-11-20T10:00:00+00:00", "reason": "x"}
            if pnl is not None:
                event.update({"shares_sold": 10, "price": 100.0, "realized_pnl": pnl})
            history = [event]
    thesis = {
        "thesis_id": thesis_id,
        "ticker": ticker,
        "created_at": "2026-11-01T00:00:00+00:00",
        "updated_at": exit_at or "2026-11-20T10:00:00+00:00",
        "thesis_type": "pivot_breakout",
        "status": status,
        "status_history": history,
        "thesis_statement": f"{ticker} paper trade",
        "origin": {"skill": "india-paper-ledger", "output_file": "orders.json"},
    }
    if pnl is not None:
        thesis["outcome"] = {"pnl_dollars": pnl, "pnl_pct": pnl / 1000}
    if exit_at is not None:
        thesis["exit"] = {"actual_date": exit_at, "actual_price": 100.0, "exit_reason": "stop_hit"}
    if status in {"ACTIVE", "PARTIALLY_CLOSED"}:
        thesis["entry"] = {"actual_price": 100.0, "actual_date": "2026-11-02T10:00:00+00:00"}
        thesis["position"] = {"shares": 100, "shares_remaining": 100 if status == "ACTIVE" else 50}
    path = state_dir / f"{thesis_id}.yaml"
    path.write_text(yaml.safe_dump(thesis, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def evaluate(state_dir: Path, as_of: str = "2026-11-20", account: float = 1_000_000, **config):
    theses, quality, warnings = cb.load_theses(state_dir)
    return cb.evaluate_circuit_breaker(
        theses,
        account,
        cb.parse_as_of(as_of),
        cb.CircuitConfig(**config),
        initial_quality=quality,
        initial_warnings=warnings,
        sessions_fn=sessions,
    )


def rules(result):
    return {r["rule"]: r for r in result["triggered_rules"]}


# --------------------------------------------------------------------------- basics


def test_empty_state_allows_trading(tmp_path):
    result = evaluate(tmp_path / "missing")
    assert result["recommendation"] == "TRADING_ALLOWED"
    assert result["data_quality"] == "EMPTY_STATE"
    assert result["exchange"] == "NSE" and result["currency"] == "INR"
    (tmp_path / "empty").mkdir()
    assert evaluate(tmp_path / "empty")["data_quality"] == "EMPTY_STATE"


def test_state_path_that_is_a_file_halts(tmp_path):
    f = tmp_path / "theses"
    f.write_text("x")
    result = evaluate(f)
    assert result["recommendation"] == "HALTED"
    assert "incomplete_state_data" in rules(result)


def test_small_loss_allows_trading(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-5_000)
    result = evaluate(tmp_path)
    assert result["recommendation"] == "TRADING_ALLOWED"
    assert result["metrics"]["realized_pnl_today"] == -5_000


# --------------------------------------------------------------------------- daily rule


def test_daily_loss_halts_until_next_nse_session(tmp_path):
    write_thesis(
        tmp_path, "th_a", pnl=-25_000, exit_at="2026-11-20T10:00:00+00:00"
    )  # Fri 15:30 IST
    result = evaluate(tmp_path)
    rule = rules(result)["max_daily_loss"]
    assert result["recommendation"] == "HALTED"
    assert rule["threshold"] == 20_000
    assert rule["active_until"] == "2026-11-23T00:00:00+05:30"  # Monday IST
    assert "₹" in rule["detail"]


def test_daily_halt_skips_an_nse_holiday(tmp_path):
    HOLIDAYS.add(date(2026, 11, 23))
    write_thesis(tmp_path, "th_a", pnl=-25_000)
    assert rules(evaluate(tmp_path))["max_daily_loss"]["active_until"] == (
        "2026-11-24T00:00:00+05:30"
    )


def test_ist_date_boundary_differs_from_utc(tmp_path):
    # 20:00 UTC on the 19th is 01:30 IST on the 20th: it is "today" in India.
    write_thesis(tmp_path, "th_a", pnl=-25_000, exit_at="2026-11-19T20:00:00+00:00")
    result = evaluate(tmp_path, as_of="2026-11-20")
    assert result["metrics"]["realized_pnl_today"] == -25_000
    assert evaluate(tmp_path, as_of="2026-11-19")["metrics"]["realized_pnl_today"] == 0


def test_producer_utc_midnight_counts_on_named_date(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-25_000, exit_at="2026-11-20T00:00:00+00:00")
    assert evaluate(tmp_path, as_of="2026-11-20")["metrics"]["realized_pnl_today"] == -25_000


def test_timestamp_as_of_excludes_later_events(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-25_000, exit_at="2026-11-20T10:00:00+00:00")
    before = evaluate(tmp_path, as_of="2026-11-20T12:00:00+05:30")
    assert before["metrics"]["realized_pnl_today"] == 0
    assert before["recommendation"] == "TRADING_ALLOWED"


# --------------------------------------------------------------------------- streak


def test_losing_streak_cooldown(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-1_000, exit_at="2026-11-19T10:00:00+00:00")
    write_thesis(tmp_path, "th_b", pnl=-1_000, exit_at="2026-11-20T10:00:00+00:00")
    result = evaluate(tmp_path, as_of="2026-11-20T20:00:00+05:30")
    rule = rules(result)["losing_streak_cooldown"]
    assert result["recommendation"] == "COOLDOWN"
    assert rule["observed"] == 2
    assert rule["active_until"] == "2026-11-21T15:30:00+05:30"
    later = evaluate(tmp_path, as_of="2026-11-21T16:00:00+05:30")
    assert later["recommendation"] == "TRADING_ALLOWED"


def test_win_resets_streak(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-1_000, exit_at="2026-11-18T10:00:00+00:00")
    write_thesis(tmp_path, "th_b", pnl=-1_000, exit_at="2026-11-19T10:00:00+00:00")
    write_thesis(tmp_path, "th_c", pnl=2_000, exit_at="2026-11-20T09:00:00+00:00")
    result = evaluate(tmp_path, as_of="2026-11-20T20:00:00+05:30")
    assert result["metrics"]["consecutive_losses"] == 0
    assert result["recommendation"] == "TRADING_ALLOWED"


# --------------------------------------------------------------------------- weekly / monthly


def test_weekly_halt_uses_ist_monday(tmp_path):
    # Sunday 15 Nov 23:00 IST belongs to the previous week; Mon-Wed losses sum to -54k.
    write_thesis(tmp_path, "th_sun", pnl=-30_000, exit_at="2026-11-15T17:30:00+00:00")
    for i, day in enumerate(("16", "17", "18")):
        write_thesis(tmp_path, f"th_{i}", pnl=-18_000, exit_at=f"2026-11-{day}T10:00:00+00:00")
    result = evaluate(tmp_path, as_of="2026-11-20", losing_streak_n=10)
    assert result["metrics"]["realized_pnl_wtd"] == -54_000
    rule = rules(result)["weekly_drawdown_halt"]
    assert rule["active_until"] == "2026-11-23T00:00:00+05:30"
    assert "max_daily_loss" not in rules(result)


def test_monthly_halt_releases_on_first_session_of_next_month(tmp_path):
    for i, day in enumerate(("03", "10", "17", "24")):
        write_thesis(tmp_path, f"th_{i}", pnl=-21_000, exit_at=f"2026-11-{day}T10:00:00+00:00")
    HOLIDAYS.add(date(2026, 12, 1))
    result = evaluate(tmp_path, as_of="2026-11-27", losing_streak_n=10)
    assert result["metrics"]["realized_pnl_mtd"] == -84_000
    assert rules(result)["monthly_drawdown_halt"]["active_until"] == "2026-12-02T00:00:00+05:30"


def test_december_rolls_into_january(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-90_000, exit_at="2026-12-15T10:00:00+00:00")
    result = evaluate(tmp_path, as_of="2026-12-28", losing_streak_n=10)
    assert rules(result)["monthly_drawdown_halt"]["active_until"] == "2027-01-01T00:00:00+05:30"


def test_partial_trim_ledger_counts(tmp_path):
    history = [
        {"status": "ACTIVE", "at": "2026-11-02T10:00:00+00:00", "reason": "fill"},
        {
            "status": "PARTIALLY_CLOSED",
            "at": "2026-11-20T10:00:00+00:00",
            "shares_sold": 50,
            "price": 90.0,
            "proceeds": 4500.0,
            "realized_pnl": -25_000,
        },
    ]
    write_thesis(tmp_path, "th_a", status="PARTIALLY_CLOSED", history=history)
    result = evaluate(tmp_path)
    assert result["metrics"]["realized_pnl_today"] == -25_000
    assert result["recommendation"] == "HALTED"


# --------------------------------------------------------------------------- fail closed


def test_malformed_yaml_halts(tmp_path):
    tmp_path.joinpath("th_bad.yaml").write_text("- not a mapping\n", encoding="utf-8")
    result = evaluate(tmp_path)
    assert result["data_quality"] == "PARTIAL"
    assert result["recommendation"] == "HALTED"


def test_duplicate_thesis_ids_halt(tmp_path):
    write_thesis(tmp_path, "th_a", pnl=-1)
    path = write_thesis(tmp_path, "th_a", pnl=-1)
    path.rename(tmp_path / "th_a_copy.yaml")
    write_thesis(tmp_path, "th_a", pnl=-1)
    result = evaluate(tmp_path)
    assert any("Duplicate thesis_id" in w for w in result["warnings"])
    assert result["recommendation"] == "HALTED"


def test_ledger_event_without_realized_pnl_halts(tmp_path):
    history = [
        {"status": "ACTIVE", "at": "2026-11-02T10:00:00+00:00"},
        {"status": "CLOSED", "at": "2026-11-20T10:00:00+00:00", "shares_sold": 10, "price": 90.0},
    ]
    write_thesis(tmp_path, "th_a", history=history, pnl=-100, exit_at="2026-11-20T10:00:00+00:00")
    result = evaluate(tmp_path)
    assert result["recommendation"] == "HALTED"


def test_outcome_fallback_is_recoverable(tmp_path):
    history = [{"status": "CLOSED", "at": "2026-11-20T10:00:00+00:00", "reason": "legacy"}]
    write_thesis(tmp_path, "th_a", history=history, pnl=-500, exit_at="2026-11-20T10:00:00+00:00")
    result = evaluate(tmp_path)
    assert result["data_quality"] == "PARTIAL"
    assert result["recommendation"] == "TRADING_ALLOWED"
    assert result["metrics"]["realized_pnl_today"] == -500


def test_active_thesis_without_entry_is_skipped_and_halts(tmp_path):
    path = write_thesis(
        tmp_path,
        "th_a",
        status="ACTIVE",
        history=[{"status": "ACTIVE", "at": "2026-11-02T10:00:00+00:00"}],
    )
    data = yaml.safe_load(path.read_text())
    del data["entry"]
    path.write_text(yaml.safe_dump(data))
    assert evaluate(tmp_path)["recommendation"] == "HALTED"


# --------------------------------------------------------------------------- real producer


def load_thesis_store():
    module_path = (
        Path(__file__).resolve().parents[3] / "trader-memory-core" / "scripts" / "thesis_store.py"
    )
    if not module_path.exists():
        pytest.skip("trader-memory-core not present (standalone install)")
    spec = importlib.util.spec_from_file_location("thesis_store_for_india_cb_tests", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paper_ledger_style_theses_from_trader_memory_core(tmp_path):
    ts = load_thesis_store()
    for i, (exit_day, price) in enumerate((("2026-11-19", 940.0), ("2026-11-20", 930.0))):
        tid = ts.register(
            tmp_path,
            {
                "ticker": f"STOCK{i}",
                "thesis_type": "pivot_breakout",
                "thesis_statement": f"paper trade {i}",
                "entry": {},
                "exit": {"stop_loss": 950.0},
                "origin": {"skill": "india-paper-ledger", "output_file": "orders.json"},
                "_source_date": "2026-11-10",
            },
        )
        ts.transition(
            tmp_path, tid, "ENTRY_READY", "placed", event_date="2026-11-10T00:00:00+00:00"
        )
        ts.open_position(
            tmp_path, tid, 1000.25, "2026-11-11", shares=100, event_date="2026-11-11T10:00:00+00:00"
        )
        ts.close(
            tmp_path, tid, "stop_hit", price, exit_day, event_date=f"{exit_day}T10:00:00+00:00"
        )
    result = evaluate(tmp_path, as_of="2026-11-20T20:00:00+05:30")
    assert result["data_quality"] == "OK"
    assert result["metrics"]["consecutive_losses"] == 2
    assert result["metrics"]["realized_pnl_today"] == pytest.approx(-7025.0)
    assert result["recommendation"] == "COOLDOWN"


# --------------------------------------------------------------------------- config / output / CLI


def test_parse_as_of_date_only_covers_ist_day():
    parsed = cb.parse_as_of("2026-11-20")
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)
    assert parsed.hour == 23 and parsed.minute == 59
    assert cb.parse_as_of(None).utcoffset() == timedelta(hours=5, minutes=30)


def test_format_inr():
    assert cb.format_inr(-125000) == "-₹1,25,000.00"
    assert cb.format_inr(None) == "—"


def test_markdown_and_reports(tmp_path):
    write_thesis(tmp_path / "s", "th_a", pnl=-25_000)
    result = evaluate(tmp_path / "s")
    md = cb.generate_markdown_report(result)
    assert md.startswith("# India Drawdown Circuit Breaker Decision")
    assert "-₹25,000.00" in md and "IST" in md
    json_path, md_path = cb.write_reports(result, tmp_path / "out", json_only=False)
    assert json_path.name.startswith("india_circuit_breaker_decision_")
    assert json.loads(json_path.read_text())["recommendation"] == "HALTED"
    assert md_path.exists()
    only, none = cb.write_reports(result, tmp_path / "out2", json_only=True)
    assert none is None and only.exists()


def run_cli(*argv):
    return cb.main(list(argv), sessions_fn=sessions)


def test_cli_with_account_size_and_config(tmp_path, capsys):
    state = tmp_path / "theses"
    write_thesis(state, "th_a", pnl=-15_000)
    config = tmp_path / "cb.json"
    config.write_text(json.dumps({"max_daily_loss_pct": 1.0}))
    code = run_cli(
        "--state-dir",
        str(state),
        "--account-size",
        "1000000",
        "--config",
        str(config),
        "--as-of",
        "2026-11-20",
        "--output-dir",
        str(tmp_path / "out"),
    )
    assert code == 0
    assert "Recommendation: HALTED" in capsys.readouterr().out


def test_cli_unknown_config_key_exits_1(tmp_path, capsys):
    config = tmp_path / "cb.json"
    config.write_text(json.dumps({"max_weekly": 1}))
    assert (
        run_cli("--account-size", "1000", "--config", str(config), "--state-dir", str(tmp_path))
        == 1
    )
    assert "Unknown config key" in capsys.readouterr().err


def test_cli_ledger_report_supplies_account_size(tmp_path, capsys):
    state = tmp_path / "theses"
    write_thesis(state, "th_a", pnl=-15_000)
    report = tmp_path / "ledger.json"
    report.write_text(json.dumps({"skill": "india-paper-ledger", "account": {"equity": 500_000}}))
    code = run_cli(
        "--state-dir",
        str(state),
        "--ledger-report",
        str(report),
        "--as-of",
        "2026-11-20",
        "--output-dir",
        str(tmp_path / "out"),
        "--json-only",
    )
    assert code == 0
    assert "Recommendation: HALTED" in capsys.readouterr().out  # 3% of ₹5 lakh


def test_cli_needs_account_size(tmp_path, capsys):
    assert run_cli("--state-dir", str(tmp_path)) == 1
    assert "--account-size" in capsys.readouterr().err
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"skill": "other"}))
    assert run_cli("--state-dir", str(tmp_path), "--ledger-report", str(bad)) == 1
    assert "india-paper-ledger" in capsys.readouterr().err


def test_default_state_dir_is_india_paper():
    args = cb.build_parser().parse_args(["--account-size", "1"])
    assert args.state_dir == Path("state/india-paper/theses")


def test_calendar_gap_raises():
    def no_sessions(start, end):
        return []

    with pytest.raises(ValueError, match="No NSE session"):
        cb._next_session(date(2026, 11, 20), no_sessions)


def test_now_is_ist():
    assert datetime.now(cb.IST).utcoffset() == timedelta(hours=5, minutes=30)
