"""Tests for the India signal postmortem.

``fixtures/cache`` holds raw official NSE bars (20 Oct - 20 Nov 2026) for six
large caps plus NIFTY 50 closes, so every test runs offline. Expected values
are worked out by hand from those bars in the comments.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import india_signal_postmortem as sp
import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CACHE = FIXTURES / "cache"
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
HOLIDAYS = {date(2026, 11, 10)}


def sessions(start: date, end: date) -> list:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in HOLIDAYS:
            out.append(day)
        day += timedelta(days=1)
    return out


BARS_FN, INDEX_FN = sp.cache_only_fns(CACHE)


def write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def reports(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    write(
        d / "india_vcp_screener_2026-10-21_190500.json",
        {
            "schema_version": "1.0",
            "metadata": {"skill": "india-vcp-screener", "as_of": "2026-10-21"},
            "results": [
                {
                    "symbol": "RELIANCE",
                    "price": 1209.0,
                    "composite_score": 72.5,
                    "rating": "Good",
                    "entry_ready": True,
                },
                {
                    "symbol": "TCS",
                    "price": 2529.0,
                    "composite_score": 60.0,
                    "rating": "Fair",
                    "entry_ready": False,
                },
            ],
        },
    )
    write(
        d / "india_stockbee_momentum_burst_2026-11-13_190500.json",
        {
            "schema_version": "1.0",
            "skill": "india-stockbee-momentum-burst-screener",
            "metadata": {"as_of": "2026-11-13"},
            "candidates": [
                {
                    "symbol": "SBIN",
                    "date": "2026-11-13",
                    "close": 1080.0,
                    "rating": "A",
                    "setup_score": 80,
                    "state": "ACTIONABLE_DAY1",
                },
                {
                    "symbol": "INFY",
                    "date": "2026-11-13",
                    "close": 1141.5,
                    "rating": "Reject",
                    "setup_score": 10,
                    "state": "REJECTED",
                },
            ],
        },
    )
    write(
        d / "india_earnings_trade_2026-10-21_190600.json",
        {
            "schema_version": "1.0",
            "metadata": {"generator": "india-earnings-trade-analyzer", "as_of": "2026-10-21"},
            "results": [
                {"symbol": "SBIN", "current_price": 963.0, "grade": "B", "composite_score": 66}
            ],
        },
    )
    write(
        d / "india_dividend_screener_2026-11-18_190000.json",
        {
            "schema_version": "1.0",
            "skill": "india-dividend-screener",
            "as_of": "2026-11-18",
            "results": [{"symbol": "TCS", "price": 2379.5, "grade": "A", "score": 80}],
        },
    )
    write(d / "india_exposure_posture_2026-10-20_190000.json", {"skill": "india-exposure-coach"})
    return d


# --------------------------------------------------------------------------- extraction


def test_extract_vcp(reports):
    out = sp.extract_signals(reports / "india_vcp_screener_2026-10-21_190500.json")
    assert out["skill"] == "india-vcp-screener"
    rel = out["signals"][0]
    assert rel["signal_id"] == "india_vcp_RELIANCE_20261021"
    assert rel["ticker"] == "RELIANCE"
    assert rel["signal_date"] == "2026-10-21"
    assert rel["predicted_direction"] == "LONG"
    assert rel["source_skill"] == "india-vcp-screener"
    assert rel["entry_price"] == 1209.0
    assert (rel["grade"], rel["score"], rel["setup_state"]) == ("Good", 72.5, "entry_ready")
    assert out["signals"][1]["setup_state"] == "not_entry_ready"


def test_extract_momentum_burst_drops_rejected(reports):
    out = sp.extract_signals(reports / "india_stockbee_momentum_burst_2026-11-13_190500.json")
    assert [s["ticker"] for s in out["signals"]] == ["SBIN"]
    assert out["skipped"] == 1
    assert out["signals"][0]["setup_state"] == "ACTIONABLE_DAY1"


def test_extract_earnings_and_dividend(reports):
    earn = sp.extract_signals(reports / "india_earnings_trade_2026-10-21_190600.json")
    assert earn["skill"] == "india-earnings-trade-analyzer"
    assert earn["signals"][0]["signal_id"] == "india_etr_SBIN_20261021"
    div = sp.extract_signals(reports / "india_dividend_screener_2026-11-18_190000.json")
    assert div["signals"][0]["signal_id"] == "india_div_TCS_20261118"


def test_extract_symbol_with_ampersand(tmp_path):
    path = write(
        tmp_path / "india_vcp_screener_x.json",
        {
            "metadata": {"skill": "india-vcp-screener", "as_of": "2026-11-13"},
            "results": [{"symbol": "M&M", "price": 3333.0, "rating": "Good"}],
        },
    )
    sig = sp.extract_signals(path)["signals"][0]
    assert sig["signal_id"] == "india_vcp_MANDM_20261113"
    assert sig["ticker"] == "M&M"


def test_extract_generic_signal_list(tmp_path):
    path = write(
        tmp_path / "my_signals.json",
        {
            "signals": [
                {
                    "signal_id": "mine_1",
                    "ticker": "TCS",
                    "signal_date": "2026-10-21",
                    "predicted_direction": "LONG",
                    "source_skill": "chart-review",
                }
            ]
        },
    )
    out = sp.extract_signals(path)
    assert out["skill"] == "signals-file"
    assert out["signals"][0]["source_skill"] == "chart-review"


def test_extract_rejects_unknown_and_us_reports(tmp_path):
    with pytest.raises(ValueError, match="not a supported"):
        sp.extract_signals(write(tmp_path / "x.json", {"hello": 1}))
    with pytest.raises(ValueError, match="INR"):
        sp.extract_signals(
            write(
                tmp_path / "vcp.json",
                {
                    "metadata": {
                        "skill": "india-vcp-screener",
                        "as_of": "2026-10-21",
                        "currency": "USD",
                    },
                    "results": [],
                },
            )
        )


# --------------------------------------------------------------------------- forward outcome


def bars(symbol, start="2026-10-01", end="2026-12-31"):
    return BARS_FN(symbol, date.fromisoformat(start), date.fromisoformat(end))


def nifty():
    return INDEX_FN("NIFTY 50", date(2026, 10, 1), date(2026, 12, 31))


def test_forward_outcome_counts_nse_sessions():
    out = sp.forward_outcome(bars("RELIANCE"), nifty(), "2026-10-21", (5, 20))
    # entry 21 Oct close 1209.0; 5th session 28 Oct close 1158.0; 20th session 19 Nov 1375.6
    assert out["entry_session"] == "2026-10-21"
    assert out["entry_close"] == 1209.0
    assert out["exits"]["5d"] == {"date": "2026-10-28", "close": 1158.0}
    assert out["exits"]["20d"] == {"date": "2026-11-19", "close": 1375.6}
    assert out["returns"]["5d"] == pytest.approx(-0.042184, abs=1e-6)
    assert out["returns"]["20d"] == pytest.approx(0.137800, abs=1e-6)
    # NIFTY 50: 22811.35 -> 22698.85 (5) and -> 25514.30 (20)
    assert out["benchmark"]["5d"] == pytest.approx(-0.004932, abs=1e-6)
    assert out["excess"]["5d"] == pytest.approx(-0.037252, abs=1e-6)
    # lows/highs of sessions 1-5: low 1156.4, high 1208.0
    assert out["mae_5d_pct"] == pytest.approx(-4.35)
    assert out["mfe_5d_pct"] == pytest.approx(-0.08)
    assert out["complete"] is True


def test_forward_outcome_partial_and_pending():
    part = sp.forward_outcome(bars("SBIN"), nifty(), "2026-11-13", (5, 20))
    assert part["returns"]["5d"] == pytest.approx(0.078796, abs=1e-6)
    assert "20d" not in part["returns"]
    assert part["complete"] is False
    assert sp.forward_outcome(bars("TCS"), nifty(), "2026-11-18", (5, 20)) is None


def test_forward_outcome_signal_on_weekend_uses_prior_close():
    out = sp.forward_outcome(bars("SBIN"), nifty(), "2026-11-15", (5,))
    assert out["entry_session"] == "2026-11-13"


def test_forward_outcome_without_benchmark():
    out = sp.forward_outcome(bars("SBIN"), None, "2026-10-21", (5,))
    assert out["benchmark"] == {} and out["excess"] == {}


# --------------------------------------------------------------------------- regimes and ledger


def test_regime_book(tmp_path):
    write(
        tmp_path / "india_exposure_posture_a.json",
        {
            "skill": "india-exposure-coach",
            "as_of": "2026-10-20",
            "recommendation": "NEW_ENTRY_ALLOWED",
        },
    )
    write(
        tmp_path / "india_exposure_posture_b.json",
        {"skill": "india-exposure-coach", "as_of": "2026-10-27", "recommendation": "CASH_PRIORITY"},
    )
    book = sp.RegimeBook.from_dir(tmp_path)
    assert book.regime_at("2026-10-19") == "UNKNOWN"
    assert book.regime_at("2026-10-21") == "NEW_ENTRY_ALLOWED"
    assert book.regime_at("2026-10-28") == "CASH_PRIORITY"
    assert book.regime_at("2026-11-20") == "UNKNOWN"  # newest report older than 7 days


def test_ledger_orders_mark_taken(tmp_path):
    ledger = tmp_path / "india-paper"
    ledger.mkdir()
    write(
        ledger / "orders.json",
        {
            "orders": [
                {
                    "order_id": "po_1",
                    "side": "BUY",
                    "symbol": "SBIN",
                    "status": "FILLED",
                    "placed_at": "2026-11-13T18:00:00+05:30",
                },
                {
                    "order_id": "po_2",
                    "side": "BUY",
                    "symbol": "TCS",
                    "status": "EXPIRED",
                    "placed_at": "2026-10-21T18:00:00+05:30",
                },
            ]
        },
    )
    orders = sp.load_orders(ledger)
    assert sp.taken_by(orders, "SBIN", "2026-11-13", "2026-11-20") == ("po_1", True)
    assert sp.taken_by(orders, "TCS", "2026-10-21", "2026-10-28") == ("po_2", False)
    assert sp.taken_by(orders, "RELIANCE", "2026-10-21", "2026-10-28") == (None, False)
    assert sp.taken_by(None, "SBIN", "2026-11-13", "2026-11-20") == (None, None)


# --------------------------------------------------------------------------- recording


def record(reports, tmp_path, **kwargs):
    pm_dir = tmp_path / "postmortems"
    kwargs.setdefault("regimes", sp.RegimeBook([]))
    kwargs.setdefault("orders", None)
    batch = sp.record_reports(
        sorted(reports.glob("india_*.json")),
        pm_dir,
        bars_fn=BARS_FN,
        index_fn=INDEX_FN,
        horizons=(5, 20),
        since=date(2026, 10, 1),
        now=NOW,
        **kwargs,
    )
    return batch, pm_dir


def load(pm_dir, signal_id):
    return json.loads((pm_dir / f"pm_{signal_id}.json").read_text())


def test_record_reports_writes_records(reports, tmp_path):
    batch, pm_dir = record(reports, tmp_path)
    assert batch["signals"] == 5
    assert batch["recorded"] == 4
    assert batch["pending"] == ["india_div_TCS_20261118"]
    rel = load(pm_dir, "india_vcp_RELIANCE_20261021")
    assert rel["outcome_category"] == "FALSE_POSITIVE_SEVERE"
    assert rel["excess_outcome_category"] == "FALSE_POSITIVE_SEVERE"
    assert rel["realized_returns"]["5d"] == pytest.approx(-0.042184, abs=1e-6)
    assert rel["exit_date"] == "2026-10-28" and rel["exit_price"] == 1158.0
    assert rel["holding_days"] == 7
    assert rel["horizon_unit"] == "nse_sessions"
    assert (rel["exchange"], rel["currency"]) == ("NSE", "INR")
    assert rel["complete"] is True
    assert load(pm_dir, "india_etr_SBIN_20261021")["outcome_category"] == "FALSE_POSITIVE"
    sbin = load(pm_dir, "india_mbs_SBIN_20261113")
    assert sbin["outcome_category"] == "TRUE_POSITIVE"
    assert sbin["excess_returns"]["5d"] == pytest.approx(0.052695, abs=1e-6)
    assert sbin["complete"] is False


def test_record_uses_regimes(reports, tmp_path):
    write(
        reports / "india_exposure_posture_2026-10-20_190000.json",
        {
            "skill": "india-exposure-coach",
            "as_of": "2026-10-20",
            "recommendation": "NEW_ENTRY_ALLOWED",
        },
    )
    write(
        reports / "india_exposure_posture_2026-10-27_190000.json",
        {"skill": "india-exposure-coach", "as_of": "2026-10-27", "recommendation": "CASH_PRIORITY"},
    )
    _, pm_dir = record(reports, tmp_path, regimes=sp.RegimeBook.from_dir(reports))
    rel = load(pm_dir, "india_vcp_RELIANCE_20261021")
    assert (rel["regime_at_signal"], rel["regime_at_exit"]) == (
        "NEW_ENTRY_ALLOWED",
        "CASH_PRIORITY",
    )
    assert rel["outcome_category"] == "REGIME_MISMATCH"


def test_unknown_signal_regime_never_creates_mismatch(reports, tmp_path):
    write(
        reports / "india_exposure_posture_2026-10-27_190000.json",
        {"skill": "india-exposure-coach", "as_of": "2026-10-27", "recommendation": "CASH_PRIORITY"},
    )
    _, pm_dir = record(reports, tmp_path, regimes=sp.RegimeBook.from_dir(reports))
    rel = load(pm_dir, "india_vcp_RELIANCE_20261021")
    assert rel["regime_at_signal"] == "UNKNOWN"
    assert rel["regime_at_exit"] == "UNKNOWN"
    assert rel["outcome_category"] == "FALSE_POSITIVE_SEVERE"


def test_rerun_completes_partial_records_and_skips_complete(reports, tmp_path):
    batch, pm_dir = record(reports, tmp_path)
    path = pm_dir / "pm_india_mbs_SBIN_20261113.json"
    data = json.loads(path.read_text())
    data["outcome_notes"] = "kept"
    path.write_text(json.dumps(data))
    again, _ = record(reports, tmp_path)
    assert again["already_complete"] == 3
    assert again["recorded"] == 1
    assert load(pm_dir, "india_mbs_SBIN_20261113")["outcome_notes"] == "kept"


def test_duplicate_runs_of_one_screen_count_once(reports, tmp_path):
    src = reports / "india_vcp_screener_2026-10-21_190500.json"
    (reports / "india_vcp_screener_2026-10-21_201500.json").write_text(src.read_text())
    batch, _ = record(reports, tmp_path)
    assert batch["signals"] == 5
    assert batch["duplicates"] == 2


def test_since_filters_old_signals(reports, tmp_path):
    pm_dir = tmp_path / "pm"
    batch = sp.record_reports(
        sorted(reports.glob("india_*.json")),
        pm_dir,
        bars_fn=BARS_FN,
        index_fn=INDEX_FN,
        horizons=(5, 20),
        since=date(2026, 11, 1),
        now=NOW,
        regimes=sp.RegimeBook([]),
        orders=None,
    )
    assert batch["signals"] == 2
    assert batch["too_old"] == 3


def test_missing_bars_are_skipped_with_reason(tmp_path):
    reports = tmp_path / "r"
    reports.mkdir()
    write(
        reports / "india_vcp_screener_z.json",
        {
            "metadata": {"skill": "india-vcp-screener", "as_of": "2026-10-21"},
            "results": [{"symbol": "ZZZZ", "price": 10.0, "rating": "Good"}],
        },
    )
    batch, _ = record(reports, tmp_path)
    assert batch["recorded"] == 0
    assert "ZZZZ" in batch["errors"][0]


def test_taken_flag_from_ledger(reports, tmp_path):
    ledger = tmp_path / "india-paper"
    ledger.mkdir()
    write(
        ledger / "orders.json",
        {
            "orders": [
                {
                    "order_id": "po_1",
                    "side": "BUY",
                    "symbol": "SBIN",
                    "status": "FILLED",
                    "placed_at": "2026-11-13T18:00:00+05:30",
                }
            ]
        },
    )
    _, pm_dir = record(reports, tmp_path, orders=sp.load_orders(ledger))
    assert load(pm_dir, "india_mbs_SBIN_20261113")["taken"] is True
    assert load(pm_dir, "india_vcp_RELIANCE_20261021")["taken"] is False


# --------------------------------------------------------------------------- summary


def make_pm(
    i, skill, category, r5, excess5, *, grade="A", regime="UNKNOWN", taken=None, day="2026-10-21"
):
    return {
        "signal_id": f"s{i}",
        "source_skill": skill,
        "signal_date": day,
        "outcome_category": category,
        "excess_outcome_category": "TRUE_POSITIVE" if excess5 > 0 else "FALSE_POSITIVE",
        "realized_returns": {"5d": r5, "20d": r5 * 2},
        "excess_returns": {"5d": excess5, "20d": excess5 * 2},
        "grade": grade,
        "regime_at_signal": regime,
        "taken": taken,
        "recorded_at": "2026-11-23T00:00:00Z",
    }


def test_summarize_groups_and_feedback():
    pms = [
        make_pm(i, "india-vcp-screener", "TRUE_POSITIVE", 0.03, 0.01, grade="Strong")
        for i in range(8)
    ]
    pms += [
        make_pm(100 + i, "india-vcp-screener", "FALSE_POSITIVE_SEVERE", -0.04, -0.05, grade="Fair")
        for i in range(14)
    ]
    pms += [make_pm(200, "india-dividend-screener", "NEUTRAL", 0.001, -0.002, taken=False)]
    pms[0]["taken"] = False
    summary = sp.summarize(pms, min_sample=20)
    vcp = summary["by_skill"]["india-vcp-screener"]
    assert vcp["sample_size"] == 22
    assert vcp["accuracy"] == pytest.approx(8 / 22)
    assert vcp["avg_excess_5d"] == pytest.approx((8 * 0.01 - 14 * 0.05) / 22, abs=1e-6)
    assert vcp["beat_benchmark_rate_5d"] == pytest.approx(8 / 22, abs=1e-4)
    assert vcp["avg_return_20d"] == pytest.approx((8 * 0.06 - 14 * 0.08) / 22, abs=1e-6)
    grades = summary["by_skill_grade"]["india-vcp-screener"]
    assert grades["Strong"]["accuracy"] == 1.0 and grades["Fair"]["accuracy"] == 0.0
    assert summary["missed_winners"] == [
        {
            "signal_id": "s0",
            "ticker": None,
            "source_skill": "india-vcp-screener",
            "signal_date": "2026-10-21",
            "return_5d": 0.03,
        }
    ]
    adjustments = summary["weight_feedback"]["skill_adjustments"]
    assert [a["skill"] for a in adjustments] == ["india-vcp-screener"]
    issues = {e["issue_type"] for e in summary["improvement_backlog"]}
    assert {"false_positive_cluster", "overconfidence"} <= issues
    md = sp.render_summary_markdown(summary)
    assert "india-vcp-screener" in md and "Strong" in md


def test_summarize_empty():
    summary = sp.summarize([], min_sample=20)
    assert summary["total"] == 0
    assert "No recorded signals" in sp.render_summary_markdown(summary)


def test_load_postmortems_filters_by_signal_date(tmp_path):
    for i, day in enumerate(("2026-08-01", "2026-10-21", "2026-11-13")):
        write(tmp_path / f"pm_s{i}.json", make_pm(i, "x", "NEUTRAL", 0.0, 0.0, day=day))
    (tmp_path / "pm_bad.json").write_text("{nope")
    rows = sp.load_postmortems(tmp_path, date(2026, 10, 1), date(2026, 11, 30))
    assert sorted(r["signal_date"] for r in rows) == ["2026-10-21", "2026-11-13"]


# --------------------------------------------------------------------------- CLI


def cli(*argv):
    return sp.main(list(argv), now_fn=lambda: NOW, sessions_fn=sessions)


def test_cli_record_then_summary(reports, tmp_path, capsys):
    pm_dir = tmp_path / "pm"
    out = tmp_path / "out"
    assert (
        cli(
            "record",
            "--reports-dir",
            str(reports),
            "--no-fetch",
            "--cache-dir",
            str(CACHE),
            "--postmortems-dir",
            str(pm_dir),
            "--regime-dir",
            str(reports),
            "--ledger-dir",
            str(tmp_path / "no-ledger"),
            "--output-dir",
            str(out),
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "Recorded 4" in text and "pending 1" in text
    assert len(list(pm_dir.glob("pm_*.json"))) == 4
    assert list(out.glob("india_signal_postmortem_batch_*.json"))
    assert (
        cli(
            "summary",
            "--postmortems-dir",
            str(pm_dir),
            "--output-dir",
            str(out),
            "--from-date",
            "2026-10-01",
            "--to-date",
            "2026-11-30",
        )
        == 0
    )
    data = json.loads(next(out.glob("india_signal_postmortem_summary_*.json")).read_text())
    assert data["total"] == 4
    assert (
        next(out.glob("india_signal_postmortem_summary_*.md"))
        .read_text()
        .startswith("# India Signal Postmortem")
    )


def test_cli_record_explicit_report(reports, tmp_path):
    assert (
        cli(
            "record",
            "--reports",
            str(reports / "india_vcp_screener_2026-10-21_190500.json"),
            "--no-fetch",
            "--cache-dir",
            str(CACHE),
            "--postmortems-dir",
            str(tmp_path / "pm"),
            "--regime-dir",
            str(reports),
            "--output-dir",
            str(tmp_path / "o"),
            "--since",
            "2026-10-01",
        )
        == 0
    )
    assert len(list((tmp_path / "pm").glob("pm_*.json"))) == 2


@pytest.mark.parametrize(
    "argv, message",
    [
        (("record", "--horizons", "20"), "must include 5"),
        (("record", "--horizons", "5,x"), "positive"),
        (("record", "--since", "2026-99-01"), "YYYY-MM-DD"),
        (("summary", "--from-date", "2026-11-30", "--to-date", "2026-11-01"), "later"),
    ],
)
def test_cli_bad_arguments(tmp_path, capsys, argv, message):
    assert cli(*argv, "--output-dir", str(tmp_path)) == 1
    assert message in capsys.readouterr().err


def test_cli_record_with_no_reports(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        cli("record", "--reports-dir", str(empty), "--no-fetch", "--output-dir", str(tmp_path)) == 1
    )
    assert "no India screener reports" in capsys.readouterr().err
