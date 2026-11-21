"""Tests for the India (NSE) downtrend duration analyzer."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import analyze_india_downtrends as dt
import generate_india_histogram_html as hist
import pytest

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)


def weekdays(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def bars_from_closes(closes, start=date(2024, 1, 1)):
    days = weekdays(start, start + timedelta(days=len(closes) * 2))[: len(closes)]
    return [
        {"date": d.isoformat(), "open": c, "high": c, "low": c, "close": c, "volume": 1000}
        for d, c in zip(days, closes)
    ]


def two_declines():
    """Up 30 sessions, down 40 (-20%), up 60, down 30 (-12%), up 40."""
    closes, price = [], 100.0
    for steps, move in ((30, 1.01), (40, 0.9944), (60, 1.008), (30, 0.9957), (40, 1.006)):
        for _ in range(steps):
            price *= move
            closes.append(round(price, 4))
    return closes


class FakeCache:
    def __init__(self, stocks, warnings=None):
        self.stocks = stocks
        self.warnings = warnings or {}
        self._sessions_fn = weekdays

    def stock_bars(self, symbol, start, end, adjusted=True):
        value = self.stocks[symbol]
        if isinstance(value, Exception):
            raise value
        return {"symbol": symbol, "bars": value, "warnings": self.warnings.get(symbol, [])}


def fake_loader(key, cache_root):
    lists = {
        "nifty500": [
            {"symbol": "BIGCO", "industry": "Information Technology"},
            {"symbol": "MIDCO", "industry": "Capital Goods"},
            {"symbol": "GONE", "industry": "Capital Goods"},
        ],
        "nifty100": [{"symbol": "BIGCO"}],
        "niftymidcap150": [{"symbol": "MIDCO"}],
        "niftysmallcap250": [],
    }
    if key not in lists:
        raise india.IndiaDataError(f"cannot download {key}")
    return lists[key]


# --------------------------------------------------------------------------- helpers


def test_tier_map_from_index_membership_reports_missing_lists():
    tiers, problems = dt.build_tier_map("unused", fake_loader)
    assert tiers == {"BIGCO": "Large", "MIDCO": "Mid"}
    assert problems and "niftymicrocap250" in problems[0]


def test_latest_continuous_segment_drops_history_before_gaps():
    old = bars_from_closes([1.0] * 5, start=date(2022, 12, 1))
    new = bars_from_closes([2.0] * 5, start=date(2023, 12, 1))
    segment, gaps = dt.latest_continuous_segment(old + new)
    assert gaps == 1 and segment == new


def test_two_declines_are_found_with_session_durations():
    found, notes = dt.analyze_symbol(
        "BIGCO", {"bars": bars_from_closes(two_declines())}, "IT", "Large", 20, 20, 5.0
    )
    assert len(found) == 2
    assert found[0]["duration_days"] == 40 and found[0]["depth_pct"] < -15
    assert found[1]["duration_days"] == 30
    assert found[0]["market_cap_tier"] == "Large" and found[0]["sector"] == "IT"
    assert notes["corporate_action_windows_dropped"] == 0


def test_declines_spanning_unadjusted_jumps_are_dropped():
    bars = bars_from_closes(two_declines())
    jump_day = bars[45]["date"]  # inside the first decline
    found, notes = dt.analyze_symbol(
        "X",
        {"bars": bars, "warnings": [{"date": jump_day, "change_pct": -25, "note": "demerger"}]},
        "IT",
        "Large",
        20,
        20,
        5.0,
    )
    assert len(found) == 1 and notes["corporate_action_windows_dropped"] == 1


def test_short_history_is_skipped():
    found, notes = dt.analyze_symbol(
        "X", {"bars": bars_from_closes([1.0] * 10)}, "IT", "Large", 20, 20, 5.0
    )
    assert found == [] and notes["skipped"] == "insufficient_history"


# --------------------------------------------------------------------------- main + HTML


def run(tmp_path, extra=()):
    cache = FakeCache(
        {
            "BIGCO": bars_from_closes(two_declines()),
            "MIDCO": bars_from_closes(two_declines()),
            "GONE": india.SymbolNotFoundError("no history for GONE"),
        }
    )
    return dt.main(
        ["--output-dir", str(tmp_path), "--cache-dir", str(tmp_path / "c"), *extra],
        cache=cache,
        loader=fake_loader,
        now_fn=lambda: NOW,
    )


def test_main_writes_reports_grouped_by_tier_and_industry(tmp_path, capsys):
    assert run(tmp_path) == 0
    result = json.loads(
        next(tmp_path.glob("india_downtrend_analysis_*.json")).read_text(encoding="utf-8")
    )
    assert result["exchange"] == "NSE"
    assert result["summary"]["total_downtrends"] == 4
    assert set(result["by_market_cap"]) == {"Large", "Mid"}
    assert set(result["by_sector"]) == {"Information Technology", "Capital Goods"}
    assert list(result["data_notes"]["skipped_symbols"]) == ["GONE"]
    md = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "Nifty index membership" in md and "sessions" in md and "$" not in md

    html = hist.generate_html(result)
    assert "NIFTY 100" in html and "$200B" not in html
    assert f"{result['parameters']['start_date']} to {result['parameters']['end_date']}" in html


def test_sector_filter_and_unknown_sector(tmp_path, capsys):
    assert run(tmp_path, ["--sector", "information technology"]) == 0
    result = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert set(result["by_sector"]) == {"Information Technology"}
    assert run(tmp_path / "b", ["--sector", "Shipbuilding"]) == 1
    assert "Shipbuilding" in capsys.readouterr().err


def test_start_after_end_is_an_error(tmp_path, capsys):
    assert run(tmp_path, ["--start", "2027-01-01"]) == 1
    assert "Error" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["2024-13-01", "tomorrow"])
def test_bad_dates_are_rejected(value):
    with pytest.raises(SystemExit):
        dt.parse_arguments(["--start", value])


def test_explicit_symbols_use_total_market_industry(tmp_path):
    def loader(key, cache_root):
        if key == "niftytotalmarket":
            return [{"symbol": "BIGCO", "industry": "Information Technology"}]
        return fake_loader(key, cache_root)

    args = dt.parse_arguments(["--symbols", "bigco", "NEWCO"])
    rows = dt.select_rows(args, tmp_path, loader)
    assert rows == [
        {"symbol": "BIGCO", "industry": "Information Technology"},
        {"symbol": "NEWCO", "industry": "Unknown"},
    ]


def test_histogram_cli_renders_latest_analysis(tmp_path, monkeypatch):
    assert run(tmp_path) == 0
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--input",
            str(tmp_path / "india_downtrend_analysis_*.json"),
            "--output-dir",
            str(tmp_path),
        ],
    )
    hist.main()
    page = next(tmp_path.glob("india_downtrend_histogram_*.html")).read_text(encoding="utf-8")
    assert "India Downtrend Duration Analysis (NSE)" in page and "NIFTY Midcap 150" in page


def test_histogram_cli_without_input_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["x", "--input", str(tmp_path / "none_*.json"), "--output-dir", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as exc:
        hist.main()
    assert exc.value.code == 1
