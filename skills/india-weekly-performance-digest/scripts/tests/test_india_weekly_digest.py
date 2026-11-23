"""Tests for the India weekly performance digest.

The ``fixtures/ledger`` directory is a real india-paper-ledger state built from
NSE bhavcopy fills (Oct-Nov 2026): six entries, BHEL sold before the review
week, TCS and INFY stopped out, SBIN sold by hand, RELIANCE hit its target and
M&M was trimmed. ``fixtures/cache`` holds the matching raw NSE bars and NIFTY 50
closes, so the CLI runs fully offline with ``--no-fetch``.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import india_weekly_digest as wd
import pytest
import yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOLIDAYS = {date(2026, 11, 10)}  # Diwali Balipratipada: no NSE session


def sessions(start: date, end: date) -> list:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in HOLIDAYS:
            out.append(day)
        day += timedelta(days=1)
    return out


def cache_bars(kind: str, name: str):
    path = FIXTURES / "cache" / kind / f"{name.replace(' ', '_')}.json"
    return json.loads(path.read_text(encoding="utf-8"))["bars"]


def bars_fn(symbol, start, end):
    return [
        b for b in cache_bars("stocks", symbol) if start.isoformat() <= b["date"] <= end.isoformat()
    ]


def index_fn(name, start, end):
    return [
        b for b in cache_bars("indices", name) if start.isoformat() <= b["date"] <= end.isoformat()
    ]


@pytest.fixture
def ledger(tmp_path):
    target = tmp_path / "india-paper"
    shutil.copytree(FIXTURES / "ledger", target)
    return target


def build(state_dir, start=date(2026, 11, 16), end=date(2026, 11, 22), **kwargs):
    kwargs.setdefault("sessions_fn", sessions)
    kwargs.setdefault("bars_fn", bars_fn)
    kwargs.setdefault("index_fn", index_fn)
    kwargs.setdefault("now", datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc))
    return wd.build_digest(state_dir, start, end, **kwargs)


def trade(digest, symbol):
    return next(t for t in digest["india"]["trades"] if t["symbol"] == symbol)


# --------------------------------------------------------------------------- dates


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-11-17", date(2026, 11, 17)),
        ("2026-11-17T00:00:00+00:00", date(2026, 11, 17)),
        ("2026-11-20T20:00:00+00:00", date(2026, 11, 21)),  # 01:30 IST next day
        ("2026-11-20T23:00:00+05:30", date(2026, 11, 20)),
        ("2026-11-20T10:00:00Z", date(2026, 11, 20)),
        (None, None),
        ("not a date", None),
    ],
)
def test_ist_date(value, expected):
    assert wd.ist_date(value) == expected


def test_week_window_is_monday_to_sunday_with_nse_sessions():
    window = wd.week_window(date(2026, 11, 11), sessions)
    assert window["from"] == date(2026, 11, 9)
    assert window["to"] == date(2026, 11, 15)
    assert date(2026, 11, 10) not in window["sessions"]
    assert len(window["sessions"]) == 4


def test_default_window_uses_latest_completed_session():
    # Monday 23 Nov 2026 14:30 IST: today's bhavcopy is not final yet.
    now = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
    window = wd.default_window(now, sessions)
    assert (window["from"], window["to"]) == (date(2026, 11, 16), date(2026, 11, 22))
    assert window["sessions"][-1] == date(2026, 11, 20)


# --------------------------------------------------------------------------- real ledger week


def test_headline_metrics_use_closed_theses_in_the_week(ledger):
    digest = build(ledger)
    s = digest["summary"]
    assert (s["total_trades"], s["winners"], s["losers"]) == (4, 2, 2)
    assert s["total_realized_pnl"] == pytest.approx(4148.01)
    assert {t["symbol"] for t in digest["india"]["trades"]} == {"TCS", "SBIN", "RELIANCE", "INFY"}
    assert digest["currency"] == "INR"
    assert digest["exchange"] == "NSE"


def test_partial_trim_of_open_position_is_informational(ledger):
    digest = build(ledger)
    trims = digest["partial_trims"]
    assert trims["count"] == 1
    assert trims["trims"][0]["symbol"] == "M&M"
    assert trims["total_realized_pnl"] == pytest.approx(4824.35)
    assert digest["summary"]["total_realized_pnl"] == pytest.approx(4148.01)


def test_trade_detail_from_journal(ledger):
    rel = trade(build(ledger), "RELIANCE")
    assert rel["entry_session"] == "2026-11-02"
    assert rel["exit_session"] == "2026-11-17"
    assert rel["entry_price"] == pytest.approx(1300.0)
    assert rel["exit_price"] == pytest.approx(1365.0)
    assert rel["qty"] == 70
    assert rel["charges"] == pytest.approx(222.89)
    assert rel["gross_pnl"] == pytest.approx(4550.0)
    assert rel["net_pnl"] == pytest.approx(4327.11)
    assert rel["exit_reason"] == "target_hit"
    assert rel["holding_sessions"] == 10


def test_dividends_are_reported_beside_thesis_pnl(ledger):
    infy = trade(build(ledger), "INFY")
    assert infy["dividends"] == pytest.approx(2080.0)
    assert infy["net_pnl"] == pytest.approx(-7403.17)
    assert infy["net_pnl_incl_dividends"] == pytest.approx(-5323.17)


def test_benchmark_week_and_trade_excess(ledger):
    digest = build(ledger)
    bench = digest["india"]["benchmark"]
    assert bench["index"] == "NIFTY 50"
    assert bench["start_session"] == "2026-11-13"
    assert bench["end_session"] == "2026-11-20"
    assert bench["change_pct"] == pytest.approx(2.61)
    rel = trade(digest, "RELIANCE")
    assert rel["benchmark_pct"] == pytest.approx(8.30)
    assert rel["excess_pct"] == pytest.approx(-3.55)


def test_mae_mfe_from_nse_closes_feed_metrics(ledger):
    digest = build(ledger)
    rel = trade(digest, "RELIANCE")
    assert rel["mae_pct"] == pytest.approx(-0.12)
    assert rel["mfe_pct"] == pytest.approx(5.03)
    assert rel["mae_mfe_source"] == "nse_eod_close"
    assert digest["metrics"]["avg_mae_pct"] is not None
    assert digest["metrics"]["avg_mfe_pct"] is not None


def test_thesis_files_are_not_modified(ledger):
    before = {p.name: p.read_text() for p in (ledger / "theses").glob("*.yaml")}
    build(ledger)
    after = {p.name: p.read_text() for p in (ledger / "theses").glob("*.yaml")}
    assert before == after


def test_week_activity_and_realized_equity(ledger):
    india = build(ledger)["india"]
    act = india["week_activity"]
    assert act["sell_fills"] == 5
    assert act["buy_fills"] == 0
    assert act["charges_paid"] == pytest.approx(516.63)
    assert act["realized_pnl"] == pytest.approx(8972.36)
    assert act["dividends"] == pytest.approx(0.0)
    acct = india["account"]
    assert acct["starting_capital"] == pytest.approx(1_000_000.0)
    assert acct["realized_equity_start"] == pytest.approx(1_008_356.70)
    assert acct["realized_equity_end"] == pytest.approx(1_017_329.06)
    assert acct["realized_return_pct"] == pytest.approx(0.89)


def test_charges_drag_and_india_lessons(ledger):
    digest = build(ledger)
    drag = digest["india"]["charges_drag"]
    assert drag["charges"] == pytest.approx(919.39)
    assert drag["gross_pnl"] == pytest.approx(5067.40)
    assert drag["net_pnl"] == pytest.approx(4148.01)
    tips = " ".join(digest["lessons"]["process_improvements"])
    assert "stop_hit" in tips
    assert "1 of 4" in tips and "NIFTY 50" in tips
    assert "charges" in tips.lower()


def test_lesson_rows_carry_nse_symbol(ledger):
    lessons = build(ledger)["lessons"]
    assert lessons["top_wins"][0]["symbol"] == "SBIN"
    assert lessons["top_losses"][0]["symbol"] == "INFY"


def test_manual_paper_orders_are_attributed_to_manual(ledger):
    patterns = build(ledger)["pattern_analysis"]
    assert set(patterns["by_source_skill"]) == {"manual"}


def test_earlier_week_contains_only_bhel(ledger):
    digest = build(ledger, date(2026, 11, 2), date(2026, 11, 8))
    assert [t["symbol"] for t in digest["india"]["trades"]] == ["BHEL"]
    assert digest["partial_trims"]["count"] == 0


def test_empty_week_is_valid(ledger):
    digest = build(ledger, date(2026, 10, 5), date(2026, 10, 11))
    assert digest["summary"]["total_trades"] == 0
    assert digest["india"]["trades"] == []
    md = wd.render_markdown(digest)
    assert "No closed trades" in md


# --------------------------------------------------------------------------- IST boundaries


def _closed_thesis(tid, exit_at, pnl=100.0, **extra):
    thesis = {
        "thesis_id": tid,
        "ticker": "SBIN",
        "thesis_type": "pivot_breakout",
        "status": "CLOSED",
        "status_history": [{"status": "CLOSED", "at": exit_at, "realized_pnl": pnl}],
        "entry": {"actual_price": 100.0, "actual_date": "2026-11-02T00:00:00+00:00"},
        "exit": {"stop_loss": 95.0, "actual_price": 110.0, "actual_date": exit_at},
        "position": {"shares": 10},
        "origin": {"skill": "manual"},
        "outcome": {"pnl_dollars": pnl, "pnl_pct": 10.0},
    }
    thesis.update(extra)
    return thesis


def write_theses(directory: Path, *theses):
    directory.mkdir(parents=True, exist_ok=True)
    for t in theses:
        (directory / f"{t['thesis_id']}.yaml").write_text(yaml.safe_dump(t), encoding="utf-8")


def test_exit_dates_are_ist(tmp_path):
    theses = tmp_path / "theses"
    write_theses(
        theses,
        # 00:30 IST on Monday 16 Nov: inside the week; a UTC window would miss it.
        _closed_thesis("th_a", "2026-11-15T19:00:00+00:00"),
        # 00:30 IST on Monday 23 Nov: next week.
        _closed_thesis("th_b", "2026-11-22T19:00:00+00:00"),
    )
    digest = build(theses, bars_fn=None, index_fn=None)
    assert [t["thesis_id"] for t in digest["india"]["trades"]] == ["th_a"]


def test_plain_theses_dir_without_ledger(tmp_path):
    theses = tmp_path / "theses"
    write_theses(theses, _closed_thesis("th_a", "2026-11-17T10:00:00+00:00"))
    digest = build(theses, bars_fn=None, index_fn=None)
    assert digest["india"]["account"] is None
    row = digest["india"]["trades"][0]
    assert row["charges"] is None
    assert row["entry_price"] == pytest.approx(100.0)
    assert digest["india"]["benchmark"]["change_pct"] is None
    assert any("benchmark" in n.lower() for n in digest["india"]["data_notes"])


def test_existing_outcome_mae_is_kept(tmp_path):
    theses = tmp_path / "theses"
    t = _closed_thesis("th_a", "2026-11-17T10:00:00+00:00")
    t["outcome"].update({"mae_pct": -2.5, "mfe_pct": 7.0, "mae_mfe_source": "fmp_eod"})
    write_theses(theses, t)
    row = build(theses)["india"]["trades"][0]
    assert (row["mae_pct"], row["mfe_pct"], row["mae_mfe_source"]) == (-2.5, 7.0, "fmp_eod")


def test_bars_failure_becomes_data_note(tmp_path):
    theses = tmp_path / "theses"
    write_theses(theses, _closed_thesis("th_a", "2026-11-17T10:00:00+00:00"))

    def broken(*_args):
        raise wd.IndiaDataError("NSE unreachable")

    digest = build(theses, bars_fn=broken, index_fn=broken)
    row = digest["india"]["trades"][0]
    assert row["mae_pct"] is None
    notes = " ".join(digest["india"]["data_notes"])
    assert "SBIN" in notes and "NSE unreachable" in notes


# --------------------------------------------------------------------------- signal source


def test_signal_source_follows_plan_to_screener(tmp_path):
    vcp = tmp_path / "india_vcp_screener_2026-11-13_190000.json"
    vcp.write_text(json.dumps({"metadata": {"skill": "india-vcp-screener"}, "results": []}))
    plan = tmp_path / "india_breakout_trade_plan_2026-11-13_191000.json"
    plan.write_text(
        json.dumps(
            {"skill": "india-breakout-trade-planner", "input_metadata": {"source_file": str(vcp)}}
        )
    )
    thesis = {"origin": {"skill": "india-paper-ledger", "output_file": str(plan)}}
    resolved = wd.signal_source(thesis)
    assert resolved["source"] == "india-vcp-screener"
    assert resolved["chain"] == ["india-vcp-screener", "india-breakout-trade-planner"]


def test_signal_source_from_filename_when_file_is_gone():
    thesis = {
        "origin": {
            "skill": "india-paper-ledger",
            "output_file": "reports/india_stockbee_momentum_burst_2026-11-13_190000.json",
        }
    }
    assert wd.signal_source(thesis)["source"] == "india-stockbee-momentum-burst-screener"


@pytest.mark.parametrize(
    "origin, expected",
    [
        ({"skill": "india-paper-ledger", "output_file": "state/india-paper/orders.json"}, "manual"),
        ({"skill": "india-paper-ledger"}, "manual"),
        ({"skill": "kanchi-dividend-sop", "output_file": "x.json"}, "kanchi-dividend-sop"),
        (None, "unknown"),
    ],
)
def test_signal_source_fallbacks(origin, expected):
    assert wd.signal_source({"origin": origin})["source"] == expected


def test_signal_source_relative_path_resolves_against_repo_root(tmp_path):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    (repo / "reports" / "picks.json").write_text(json.dumps({"skill": "india-dividend-screener"}))
    thesis = {"origin": {"skill": "india-paper-ledger", "output_file": "reports/picks.json"}}
    assert wd.signal_source(thesis, search_roots=[repo])["source"] == "india-dividend-screener"


# --------------------------------------------------------------------------- CLI


def run_cli(ledger, tmp_path, *extra):
    out = tmp_path / "out"
    code = wd.main(
        [
            "--state-dir",
            str(ledger),
            "--no-fetch",
            "--cache-dir",
            str(FIXTURES / "cache"),
            "--output-dir",
            str(out),
            *extra,
        ],
        sessions_fn=sessions,
        now_fn=lambda: datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc),
    )
    return code, out


def test_cli_writes_json_and_markdown(ledger, tmp_path):
    code, out = run_cli(ledger, tmp_path, "--week-ending", "2026-11-20")
    assert code == 0
    data = json.loads((out / "india_weekly_digest_2026-11-22.json").read_text())
    assert data["report_type"] == "india_weekly_performance_digest"
    assert data["summary"]["total_trades"] == 4
    assert trade(data, "RELIANCE")["mae_pct"] == pytest.approx(-0.12)
    assert data["india"]["benchmark"]["change_pct"] == pytest.approx(2.61)
    md = (out / "india_weekly_digest_2026-11-22.md").read_text()
    for text in ("# India Weekly Performance Digest", "₹", "RELIANCE", "NIFTY 50", "₹4,148.01"):
        assert text in md
    infy_row = next(line for line in md.splitlines() if line.startswith("| INFY"))
    assert infy_row.endswith("₹2,080.00 |")


def test_cli_default_week(ledger, tmp_path):
    code, out = run_cli(ledger, tmp_path)
    assert code == 0
    assert (out / "india_weekly_digest_2026-11-22.json").exists()


def test_cli_explicit_range(ledger, tmp_path):
    code, out = run_cli(ledger, tmp_path, "--from-date", "2026-11-17", "--to-date", "2026-11-17")
    assert code == 0
    data = json.loads((out / "india_weekly_digest_2026-11-17.json").read_text())
    assert [t["symbol"] for t in data["india"]["trades"]] == ["RELIANCE"]
    assert data["period"]["week_complete"] is True


def test_cli_partial_week_flag(ledger, tmp_path):
    code = wd.main(
        [
            "--state-dir",
            str(ledger),
            "--no-fetch",
            "--cache-dir",
            str(FIXTURES / "cache"),
            "--output-dir",
            str(tmp_path / "o"),
        ],
        sessions_fn=sessions,
        now_fn=lambda: datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc),  # Wed 20:30 IST
    )
    assert code == 0
    data = json.loads((tmp_path / "o" / "india_weekly_digest_2026-11-22.json").read_text())
    assert data["period"]["week_complete"] is False
    assert data["period"]["last_session"] == "2026-11-18"


@pytest.mark.parametrize(
    "args",
    [
        ["--week-ending", "2026-13-01"],
        ["--from-date", "2026-11-20", "--to-date", "2026-11-16"],
        ["--from-date", "2026-11-16"],
        ["--week-ending", "2026-11-20", "--from-date", "2026-11-16", "--to-date", "2026-11-20"],
    ],
)
def test_cli_rejects_bad_dates(ledger, tmp_path, capsys, args):
    code, _ = run_cli(ledger, tmp_path, *args)
    assert code == 1
    assert "error" in capsys.readouterr().err.lower()


def test_cli_missing_state_dir(tmp_path, capsys):
    code = wd.main(["--state-dir", str(tmp_path / "nope"), "--no-fetch"], sessions_fn=sessions)
    assert code == 1
    assert "not found" in capsys.readouterr().err
