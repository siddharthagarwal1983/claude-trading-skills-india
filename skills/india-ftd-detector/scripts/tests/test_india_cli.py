"""India FTD detector CLI: NSE loading, Muhurat removal, output shape."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import india_ftd_detector as cli
import pytest

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
END = date(2026, 11, 20)


def sessions(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def ftd_series(end=END, muhurat=None):
    """Oldest-first: uptrend, ~12% correction, rally attempt, +2.2% FTD on day 5, follow-through."""
    moves = [
        (40, 1.004, 1.0),
        (20, 0.9935, 1.0),
        (1, 1.006, 0.8),
        (3, 1.004, 0.8),
        (1, 1.022, 1.5),
        (8, 1.003, 1.0),
    ]
    total = sum(n for n, _, _ in moves)
    days = sessions(end - timedelta(days=total * 2), end)[-total:]
    bars, close, i = [], 20000.0, 0
    for n, move, vol in moves:
        for _ in range(n):
            close *= move
            bars.append(
                {
                    "date": days[i].isoformat(),
                    "open": close / move,
                    "high": close * 1.003,
                    "low": min(close, close / move) * 0.997,
                    "close": round(close, 2),
                    "volume": int(300_000_000 * vol),
                }
            )
            i += 1
    if muhurat:
        bars.append(
            {
                "date": muhurat.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 20_000_000,
            }
        )
        bars.sort(key=lambda b: b["date"])
    return bars


class FakeCache:
    def __init__(self, series):
        self.series = series
        self._sessions_fn = sessions

    def index_bars(self, name, start, end):
        if name not in self.series:
            raise india.SymbolNotFoundError(f"no history for {name}")
        return {
            "bars": [
                b for b in self.series[name] if start.isoformat() <= b["date"] <= end.isoformat()
            ]
        }


def run(tmp_path, series, *extra):
    return cli.main(
        ["--output-dir", str(tmp_path), "--cache-dir", str(tmp_path / "c"), *extra],
        cache=FakeCache(series),
        now_fn=lambda: NOW,
    )


def report(tmp_path):
    return json.loads(next(tmp_path.glob("india_ftd_detector_*.json")).read_text(encoding="utf-8"))


def test_follow_through_day_is_confirmed_on_both_indexes(tmp_path, capsys):
    assert run(tmp_path, {"NIFTY 50": ftd_series(), "NIFTY 500": ftd_series()}) == 0
    data = report(tmp_path)
    assert data["metadata"]["skill"] == "india-ftd-detector"
    assert data["metadata"]["instruments"]["secondary"]["label"] == "NIFTY 500"
    assert data["metadata"]["expected_session"] == END.isoformat()
    assert {"primary", "secondary"} <= set(data) and "sp500" not in data
    assert data["primary"]["ftd"]["ftd_detected"] is True
    assert data["market_state"]["dual_confirmation"] is True
    assert data["quality_score"]["total_score"] > 0
    md = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "India FTD Detector Report (NSE)" in md and "S&P" not in md and "$" not in md
    assert "Combined:" in capsys.readouterr().out


def test_muhurat_session_is_removed_before_analysis(tmp_path):
    muhurat = date(2025, 10, 21)
    end = date(2025, 11, 14)
    series = {"NIFTY 50": ftd_series(end=end, muhurat=muhurat)}
    now = datetime(2025, 11, 15, 10, 0, tzinfo=IST)
    rc = cli.main(
        ["--output-dir", str(tmp_path), "--secondary-index", "NIFTY MIDCAP 150"],
        cache=FakeCache(series),
        now_fn=lambda: now,
    )
    assert rc == 0
    assert report(tmp_path)["metadata"]["muhurat_sessions_removed"] == [muhurat.isoformat()]


def test_missing_second_index_falls_back_to_primary(tmp_path, capsys):
    assert run(tmp_path, {"NIFTY 50": ftd_series()}) == 0
    assert "using NIFTY 50 only" in capsys.readouterr().err


def test_missing_primary_is_an_error(tmp_path, capsys):
    assert run(tmp_path, {"NIFTY 500": ftd_series()}) == 1
    assert "Error" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value,message", [("2026-12-01", "after the latest"), ("soon", "YYYY-MM-DD")]
)
def test_bad_as_of(tmp_path, capsys, value, message):
    assert run(tmp_path, {"NIFTY 50": ftd_series()}, "--as-of", value) == 1
    assert message in capsys.readouterr().err


def test_as_of_weekend_snaps_to_friday(tmp_path):
    assert (
        run(
            tmp_path, {"NIFTY 50": ftd_series(), "NIFTY 500": ftd_series()}, "--as-of", "2026-11-15"
        )
        == 0
    )
    assert report(tmp_path)["metadata"]["expected_session"] == "2026-11-13"
