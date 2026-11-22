"""India earnings trade analyzer: events files, reaction measures, gates, CLI."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import analyze_india_earnings_trades as eta
import pytest

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
AS_OF = date(2026, 11, 20)


def sessions(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def bars_with_reaction(
    meeting="2026-11-12", meeting_move=0.0, next_gap=0.0, price=500.0, volume=500_000, n=260
):
    """Oldest-first bars rising 0.2%/day; custom move on the meeting day and next-day gap."""
    days = sessions(AS_OF - timedelta(days=n * 2), AS_OF)[-n:]
    out, close = [], price
    for d in days:
        iso = d.isoformat()
        open_ = close
        if iso == meeting:
            close = close * (1 + meeting_move)
        elif out and out[-1]["date"] == meeting:
            open_ = close * (1 + next_gap)
            close = open_
        else:
            close *= 1.002
        out.append(
            {
                "date": iso,
                "open": open_,
                "high": max(open_, close) * 1.002,
                "low": min(open_, close) * 0.998,
                "close": close,
                "volume": volume,
            }
        )
    return out


class FakeCache:
    def __init__(self, stocks):
        self.stocks = stocks
        self._sessions_fn = sessions

    def stock_bars(self, symbol, start, end, adjusted=True):
        value = self.stocks.get(symbol)
        if value is None:
            raise india.SymbolNotFoundError(symbol)
        return {"bars": value}


def events_file(tmp_path, rows, name="events.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps({"data": {"events": [dict(type="results", **r) for r in rows]}}),
        encoding="utf-8",
    )
    return str(path)


def run(tmp_path, events, stocks, *extra, universe=("UPCO", "DOWNCO", "TINY", "NOHIST", "LATE")):
    return eta.main(
        [
            "--events",
            events,
            "--output-dir",
            str(tmp_path / "out"),
            "--lookback-days",
            "14",
            *extra,
        ],
        cache=FakeCache(stocks),
        now_fn=lambda: NOW,
        universe_loader=lambda key, root: [
            {"symbol": s, "industry": "Capital Goods"} for s in universe
        ],
    )


def report(tmp_path):
    return json.loads(
        next((tmp_path / "out").glob("india_earnings_trade_*.json")).read_text(encoding="utf-8")
    )


def test_two_day_reaction_captures_intraday_announcements():
    prices = list(reversed(bars_with_reaction(meeting_move=-0.06, next_gap=0.01)))
    analysis = eta.analyze_stock(prices, "2026-11-12")
    gap = analysis["gap"]
    assert gap["reaction_measure"] == "two_day"
    assert gap["gap_pct"] == pytest.approx(-5.06, abs=0.05)
    assert gap["next_session_gap_pct"] == pytest.approx(1.0, abs=0.05)
    legacy = eta.analyze_stock(prices, "2026-11-12", "next_open")["gap"]
    assert legacy["reaction_measure"] == "next_open" and legacy["gap_pct"] == pytest.approx(
        1.0, abs=0.05
    )


def test_two_day_reaction_edges():
    prices = list(reversed(bars_with_reaction()))
    assert eta.two_day_reaction(prices, "1999-01-01") is None
    assert eta.two_day_reaction(prices, prices[0]["date"]) is None  # no session after the meeting


def test_cli_filters_and_scores(tmp_path, capsys):
    stocks = {
        "UPCO": bars_with_reaction(meeting_move=0.05, next_gap=0.02),
        "DOWNCO": bars_with_reaction(meeting_move=-0.05),
        "TINY": bars_with_reaction(price=20.0),
        "OUTSIDER": bars_with_reaction(),
        "LATE": bars_with_reaction(),
    }
    events = events_file(
        tmp_path,
        [
            {"date": "2026-11-12", "nse_symbol": "UPCO", "name": "Up Co"},
            {"date": "2026-11-12", "nse_symbol": "DOWNCO"},
            {"date": "2026-11-12", "nse_symbol": "TINY"},
            {"date": "2026-11-12", "nse_symbol": "OUTSIDER"},
            {"date": "2026-11-12", "nse_symbol": "NOHIST"},
            {"date": "2026-11-20", "nse_symbol": "LATE"},
            {"date": "2026-10-01", "nse_symbol": "UPCO"},
        ],
    )
    assert run(tmp_path, events, stocks) == 0
    data = report(tmp_path)
    assert [r["symbol"] for r in data["results"]] == ["UPCO"]
    up = data["results"][0]
    assert up["two_day_reaction_pct"] == pytest.approx(7.1, abs=0.1)
    assert up["avg_turnover_cr"] > 5 and up["sector"] == "Capital Goods"
    meta = data["metadata"]
    assert meta["skipped"] == {
        "awaiting_reaction_session": ["LATE"],
        "below_liquidity_gate": ["TINY"],
        "negative_reaction": ["DOWNCO"],
        "no_price_history": ["NOHIST"],
        "outside_universe": ["OUTSIDER"],
    }
    assert meta["events_in_window"] == 6 and meta["reaction_measure"] == "two_day"
    md = next((tmp_path / "out").glob("*.md")).read_text(encoding="utf-8")
    assert "India Earnings Trade Analyzer Report (NSE)" in md and "2-day%" in md and "$" not in md
    assert "UPCO" in capsys.readouterr().out


def test_both_directions_and_all_universe(tmp_path):
    stocks = {
        "DOWNCO": bars_with_reaction(meeting_move=-0.05),
        "OUTSIDER": bars_with_reaction(meeting_move=0.03),
    }
    events = events_file(
        tmp_path,
        [
            {"date": "2026-11-12", "nse_symbol": "DOWNCO"},
            {"date": "2026-11-12", "nse_symbol": "OUTSIDER"},
        ],
    )
    assert run(tmp_path, events, stocks, "--direction", "both", "--universe", "all") == 0
    assert {r["symbol"] for r in report(tmp_path)["results"]} == {"DOWNCO", "OUTSIDER"}


def test_events_glob_and_drishti_shape(tmp_path):
    (tmp_path / "e1.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "symbol": "UPCO",
                        "date": "2026-11-12T00:00:00+00:00",
                        "quarter": "q2_27",
                        "event_id": "x",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    stocks = {"UPCO": bars_with_reaction(meeting_move=0.04)}
    assert run(tmp_path, str(tmp_path / "e*.json"), stocks) == 0
    assert report(tmp_path)["results"][0]["quarter"] == "q2_27"


@pytest.mark.parametrize(
    "extra,message",
    [
        (("--as-of", "2026-12-01"), "after the latest"),
        (("--as-of", "nope"), "YYYY-MM-DD"),
    ],
)
def test_bad_as_of(tmp_path, capsys, extra, message):
    events = events_file(tmp_path, [{"date": "2026-11-12", "nse_symbol": "UPCO"}])
    assert run(tmp_path, events, {}, *extra) == 1
    assert message in capsys.readouterr().err


def test_missing_or_bad_events_file(tmp_path, capsys):
    assert run(tmp_path, str(tmp_path / "none.json"), {}) == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert run(tmp_path, str(bad), {}) == 1
    err = capsys.readouterr().err
    assert "no events file" in err and "cannot read" in err


def test_argument_validation():
    with pytest.raises(SystemExit):
        eta.parse_arguments(["--events", "x", "--universe", "sensex"])
    with pytest.raises(SystemExit):
        eta.parse_arguments(["--events", "x", "--lookback-days", "-1"])


def test_default_events_come_from_calendar_archive(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "results_calendar.json").write_text(
        json.dumps({"events": [{"symbol": "UPCO", "date": "2026-11-12", "quarter": "q2_27"}]}),
        encoding="utf-8",
    )
    rc = eta.main(
        [
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--lookback-days",
            "14",
        ],
        cache=FakeCache({"UPCO": bars_with_reaction(meeting_move=0.04)}),
        now_fn=lambda: NOW,
        universe_loader=lambda key, root: [{"symbol": "UPCO"}],
    )
    assert rc == 0
    data = report(tmp_path)
    assert data["metadata"]["events_files"] == [str(cache_dir / "results_calendar.json")]
    assert data["results"][0]["symbol"] == "UPCO"
