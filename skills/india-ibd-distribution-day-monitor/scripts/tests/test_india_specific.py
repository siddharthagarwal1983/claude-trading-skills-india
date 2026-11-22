"""India-specific behaviour: NSE loader, peer risk combination, exposure policy, CLI."""

import json
import re
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import data_loader
import india_ibd_monitor as monitor
import pytest
from exposure_policy import generate_portfolio_action, supported_instruments
from models import IndexResult
from risk_classifier import combine_index_risks

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)  # Saturday -> latest session Fri 2026-11-20
HOLIDAY = date(2026, 11, 13)


def sessions(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5 and day != HOLIDAY:
            out.append(day)
        day += timedelta(days=1)
    return out


def index_series(n=120, end=date(2026, 11, 20), dd_offsets=(), muhurat=None):
    """Oldest-first index bars rising 0.3%/day; ``dd_offsets`` (sessions before the end)
    become -1% closes on 30% higher volume."""
    days = sessions(end - timedelta(days=n * 2), end)[-n:]
    if muhurat:
        days = sorted(set(days) | {muhurat})
    bars, close = [], 20000.0
    for i, day in enumerate(days):
        age = len(days) - 1 - i
        volume = 300_000_000
        if age in dd_offsets:
            close *= 0.99
            volume = 390_000_000
        elif day == muhurat:
            close *= 0.999
            volume = 30_000_000
        else:
            close *= 1.003
        bars.append(
            {
                "date": day.isoformat(),
                "open": close,
                "high": close * 1.004,
                "low": close * 0.996,
                "close": round(close, 2),
                "volume": volume,
            }
        )
    return bars


class FakeCache:
    def __init__(self, series):
        self.series = series
        self._sessions_fn = sessions

    def index_bars(self, name, start, end):
        if name not in self.series:
            raise india.SymbolNotFoundError(f"no history for {name}")
        bars = [b for b in self.series[name] if start.isoformat() <= b["date"] <= end.isoformat()]
        return {"index": name, "bars": bars}


def _idx(symbol, level):
    return IndexResult(
        symbol=symbol,
        benchmark_name=symbol,
        is_distribution_day_today=False,
        today={},
        d5_count=0,
        d15_count=0,
        d25_count=0,
        active_distribution_days=[],
        removed_distribution_days=[],
        risk_level=level,
        cluster_state={},
        trend_filters={},
        explanation="",
    )


# --------------------------------------------------------------------------- risk + policy


@pytest.mark.parametrize(
    "levels,expected",
    [
        (("NORMAL", "NORMAL"), "NORMAL"),
        (("CAUTION", "NORMAL"), "CAUTION"),
        (("CAUTION", "CAUTION"), "HIGH"),  # confirmed across large caps and broad market
        (("HIGH", "CAUTION"), "HIGH"),
        (("NORMAL", "SEVERE"), "SEVERE"),
        (("HIGH",), "HIGH"),
    ],
)
def test_peer_risk_combination(levels, expected):
    results = [_idx(f"I{i}", lv) for i, lv in enumerate(levels)]
    assert combine_index_risks(results) == expected


def test_combination_needs_results():
    with pytest.raises(ValueError):
        combine_index_risks([])


def test_swing_book_cuts_harder_than_index_etf():
    book = generate_portfolio_action("HIGH", "swing_book", 100, 10)
    etf = generate_portfolio_action("HIGH", "NIFTY_ETF", 100, 10)
    assert (book.target_exposure_pct, etf.target_exposure_pct) == (50, 75)
    assert book.alternative_action == "TIGHTEN_STOPS_AND_SELL_LAGGARDS"
    severe = generate_portfolio_action("SEVERE", "SWING_BOOK", 80, 10)
    assert severe.recommended_action == "RAISE_CASH" and severe.exposure_delta_pct == -55
    assert severe.trailing_stop_pct == 4
    assert generate_portfolio_action("NORMAL", "SWING_BOOK", 100, 10).trailing_stop_pct == 10
    assert supported_instruments() == ("NIFTY_ETF", "SWING_BOOK")


@pytest.mark.parametrize("instrument,level", [("TQQQ", "HIGH"), ("SWING_BOOK", "PANIC")])
def test_policy_rejects_unknown_inputs(instrument, level):
    with pytest.raises(ValueError):
        generate_portfolio_action(level, instrument, 100, 10)


# --------------------------------------------------------------------------- loader


def test_loader_drops_muhurat_and_returns_newest_first():
    muhurat = date(2025, 10, 21)
    series = index_series(n=60, end=date(2025, 11, 7), muhurat=muhurat)
    history, audit = data_loader.fetch_index_ohlcv(
        FakeCache({"NIFTY 50": series}), "NIFTY 50", 40, date(2025, 11, 7)
    )
    dates = [h["date"] for h in history]
    assert dates == sorted(dates, reverse=True)
    assert muhurat.isoformat() not in dates
    assert audit["muhurat_sessions_removed"] == [muhurat.isoformat()]
    assert audit["data_source"] == "nse_bhavcopy"


def test_loader_reports_missing_index():
    history, audit = data_loader.fetch_index_ohlcv(FakeCache({}), "NIFTY X", 40, date(2026, 11, 20))
    assert (
        history == [] and "no_data_returned" in audit["audit_flags"] and "NIFTY X" in audit["error"]
    )


def test_validate_history_quality_flags_bad_rows():
    flags, skipped = data_loader.validate_history_quality(
        [{"date": "d1", "close": None, "volume": 0}, {"date": "d2", "close": 1.0, "volume": 5}]
    )
    assert flags == ["data_quality_warnings"]
    assert {s["reason"] for s in skipped} == {"missing_close", "invalid_volume"}


# --------------------------------------------------------------------------- CLI


def run(tmp_path, series, *extra):
    return monitor.main(
        ["--output-dir", str(tmp_path), "--cache-dir", str(tmp_path / "c"), *extra],
        cache=FakeCache(series),
        now_fn=lambda: NOW,
    )


def load_report(tmp_path):
    return json.loads(
        next(tmp_path.glob("india_ibd_distribution_day_monitor_*.json")).read_text(encoding="utf-8")
    )


def test_cli_detects_clustered_distribution(tmp_path, capsys):
    series = {
        "NIFTY 50": index_series(dd_offsets=(1, 3, 6, 9)),
        "NIFTY 500": index_series(dd_offsets=(2, 5, 8)),
    }
    assert run(tmp_path, series) == 0
    payload = load_report(tmp_path)
    state = payload["market_distribution_state"]
    n50, n500 = state["index_results"]
    assert (n50["symbol"], n500["symbol"]) == ("NIFTY 50", "NIFTY 500")
    assert (n50["d15_count"], n500["d15_count"]) == (4, 3)
    assert n50["risk_level"] == "SEVERE"
    assert state["overall_risk_level"] == "SEVERE" and state["primary_signal_symbol"] == "NIFTY 50"
    assert payload["portfolio_action"]["instrument"] == "SWING_BOOK"
    assert payload["audit"]["data_source"] == "nse_bhavcopy"
    assert payload["skill"] == "india-ibd-distribution-day-monitor"
    text = json.dumps(payload, ensure_ascii=False) + next(tmp_path.glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert not re.search(r"[぀-ヿ一-鿿]", text)  # English output only
    assert "India IBD Distribution Day Monitor Report (NSE)" in text
    assert "Overall: SEVERE" in capsys.readouterr().out


def test_cli_quiet_market_is_normal_with_etf_policy(tmp_path):
    series = {"NIFTY 50": index_series(), "NIFTY 500": index_series()}
    assert run(tmp_path, series, "--instrument", "NIFTY_ETF") == 0
    payload = load_report(tmp_path)
    assert payload["market_distribution_state"]["overall_risk_level"] == "NORMAL"
    assert payload["portfolio_action"]["instrument"] == "NIFTY_ETF"


def test_cli_as_of_holiday_snaps_to_previous_session(tmp_path):
    series = {"NIFTY 50": index_series(), "NIFTY 500": index_series()}
    assert run(tmp_path, series, "--as-of", HOLIDAY.isoformat()) == 0
    audit = load_report(tmp_path)["audit"]
    assert audit["expected_session"] == "2026-11-12" == audit["as_of_resolved"]


def test_cli_symbols_override_and_partial_failure(tmp_path, capsys):
    series = {"NIFTY 50": index_series()}
    assert run(tmp_path, series, "--symbols", "NIFTY 50,NIFTY BANK") == 0
    audit = load_report(tmp_path)["audit"]
    assert audit["symbols_loaded"] == ["NIFTY 50"]
    assert "NIFTY BANK" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra,message",
    [(("--as-of", "2026-12-01"), "after the latest"), (("--as-of", "1st Nov"), "YYYY-MM-DD")],
)
def test_cli_rejects_bad_as_of(tmp_path, capsys, extra, message):
    assert run(tmp_path, {"NIFTY 50": index_series()}, *extra) == 1
    assert message in capsys.readouterr().err


def test_cli_fails_when_nothing_loads(tmp_path, capsys):
    assert run(tmp_path, {}) == 1
    assert "no NSE index history" in capsys.readouterr().err


def test_cli_reports_bad_config(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("indexes: 5\n", encoding="utf-8")
    assert run(tmp_path, {}, "--config", str(bad)) == 1
    assert "invalid configuration" in capsys.readouterr().err
