"""Tests for the India results calendar archive and upcoming report."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import india_results_calendar as cal
import pytest

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 22, 10, 0, tzinfo=IST)

UNIVERSES = {
    "nifty500": [
        {"symbol": "ENRIN", "industry": "Capital Goods", "company": "Siemens Energy"},
        {"symbol": "TCS", "industry": "Information Technology", "company": "TCS"},
        {"symbol": "SMALLCO", "industry": "Chemicals"},
    ],
    "nifty100": [{"symbol": "ENRIN"}, {"symbol": "TCS"}],
    "niftysmallcap250": [{"symbol": "SMALLCO"}],
}


def loader(key, root):
    if key not in UNIVERSES:
        raise india.IndiaDataError(f"offline: {key}")
    return UNIVERSES[key]


def sessions(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class FakeCache:
    _sessions_fn = staticmethod(sessions)

    def stock_bars(self, symbol, start, end, adjusted=True):
        if symbol == "SMALLCO":
            raise india.SymbolNotFoundError("gone")
        days = sessions(start, end)
        return {
            "bars": [
                {"date": d.isoformat(), "close": 100.0 + i, "volume": 1_000_000}
                for i, d in enumerate(days)
            ]
        }


def drishti(rows):
    return {"data": [dict(event_id="x", **r) for r in rows], "has_next": False}


def write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def run(tmp_path, *argv):
    return cal.main(
        ["--cache-dir", str(tmp_path / "cache"), *argv],
        cache=FakeCache(),
        now_fn=lambda: NOW,
        loader=loader,
    )


def test_merge_keeps_first_sighting_and_fills_gaps():
    old = [
        {"symbol": "TCS", "date": "2026-12-01", "quarter": None, "company": "", "first_seen": "t0"}
    ]
    new = [
        {"symbol": "TCS", "date": "2026-12-01", "quarter": "q3_27", "company": "TCS"},
        {"symbol": "INFY", "date": "2026-11-30", "quarter": None, "company": ""},
    ]
    merged, added = cal.merge_events(old, new, "t1")
    assert added == 1
    assert [(e["symbol"], e["first_seen"]) for e in merged] == [("INFY", "t1"), ("TCS", "t0")]
    assert merged[1]["quarter"] == "q3_27" and merged[1]["company"] == "TCS"


def test_ingest_is_idempotent(tmp_path, capsys):
    f = write(
        tmp_path, "d.json", drishti([{"symbol": "ENRIN", "date": "2026-11-25T00:00:00+00:00"}])
    )
    assert run(tmp_path, "ingest", "--events", f) == 0
    assert run(tmp_path, "ingest", "--events", f) == 0
    out = capsys.readouterr().out
    assert "1 new events" in out and "0 new events" in out
    archive = json.loads((tmp_path / "cache" / "results_calendar.json").read_text(encoding="utf-8"))
    assert len(archive["events"]) == 1 and archive["events"][0]["first_seen"]


def test_upcoming_report_with_holdings_tiers_and_prices(tmp_path, capsys):
    f = write(
        tmp_path,
        "d.json",
        drishti(
            [
                {
                    "symbol": "ENRIN",
                    "date": "2026-11-25T00:00:00+00:00",
                    "quarter": "q2_27",
                    "company": "Siemens Energy India",
                },
                {"symbol": "SMALLCO", "date": "2026-11-27T00:00:00+00:00"},
                {"symbol": "OUTSIDE", "date": "2026-11-26T00:00:00+00:00"},
                {"symbol": "TCS", "date": "2027-01-09T00:00:00+00:00"},
            ]
        ),
    )
    assert (
        run(
            tmp_path,
            "upcoming",
            "--events",
            f,
            "--holdings",
            "enrin,TCS",
            "--output-dir",
            str(tmp_path / "out"),
        )
        == 0
    )
    data = json.loads(
        next((tmp_path / "out").glob("india_earnings_calendar_*.json")).read_text(encoding="utf-8")
    )
    assert data["count"] == 2 and data["holdings_reporting"] == ["ENRIN"]
    enrin = data["by_date"]["2026-11-25"][0]
    assert (enrin["tier"], enrin["industry"], enrin["days_until"], enrin["held"]) == (
        "Large",
        "Capital Goods",
        3,
        True,
    )
    assert enrin["last_close"] > 100 and enrin["avg_turnover_cr"] > 0
    small = data["by_date"]["2026-11-27"][0]
    assert small["tier"] == "Small" and "last_close" not in small  # price lookup failed quietly
    md = next((tmp_path / "out").glob("*.md")).read_text(encoding="utf-8")
    assert "Holdings Reporting In This Window" in md and "2026-11-25 (Wednesday)" in md
    assert "holdings reporting: ENRIN" in capsys.readouterr().out


def test_upcoming_all_universe_no_prices_and_as_of(tmp_path):
    f = write(
        tmp_path, "d.json", drishti([{"symbol": "OUTSIDE", "date": "2026-11-26T00:00:00+00:00"}])
    )
    run(tmp_path, "ingest", "--events", f)
    assert (
        run(
            tmp_path,
            "upcoming",
            "--universe",
            "all",
            "--no-prices",
            "--as-of",
            "2026-11-20",
            "--days",
            "7",
            "--output-dir",
            str(tmp_path / "out"),
        )
        == 0
    )
    data = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert data["count"] == 1 and data["by_date"]["2026-11-26"][0]["tier"] == "Unknown"


def test_empty_archive_warns(tmp_path, capsys):
    assert run(tmp_path, "upcoming", "--output-dir", str(tmp_path / "out")) == 0
    assert "archive is empty" in capsys.readouterr().err


def test_symbols_command(tmp_path, capsys):
    assert run(tmp_path, "symbols", "--universe", "nifty500") == 0
    assert capsys.readouterr().out.strip() == "ENRIN,TCS,SMALLCO"


def test_errors(tmp_path, capsys):
    assert run(tmp_path, "ingest", "--events", str(tmp_path / "nope.json")) == 1
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "results_calendar.json").write_text("{", encoding="utf-8")
    assert run(tmp_path, "upcoming", "--output-dir", str(tmp_path / "out")) == 1
    err = capsys.readouterr().err
    assert "no events file" in err and "corrupt results archive" in err
    with pytest.raises(SystemExit):
        cal.parse_arguments(["upcoming", "--universe", "sensex"])
    with pytest.raises(SystemExit):
        cal.parse_arguments(["upcoming", "--days", "-1"])


def test_tier_map_tolerates_missing_lists(tmp_path):
    assert cal.tier_map(tmp_path, loader) == {"ENRIN": "Large", "TCS": "Large", "SMALLCO": "Small"}


def test_price_context_handles_empty_bars():
    class Empty:
        def stock_bars(self, *a, **k):
            return {"bars": []}

    assert cal.price_context(Empty(), "X", date(2026, 11, 20)) == {}
