"""Tests for the India stock fact-sheet builder."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import india_stock_snapshot as snap
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


def series(drift, n=300, price=100.0, volume=2_000_000):
    days = sessions(AS_OF - timedelta(days=n * 2), AS_OF)[-n:]
    out = []
    for i, d in enumerate(days):
        price *= 1 + drift + (0.004 if i % 2 else -0.004)
        out.append(
            {
                "date": d.isoformat(),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": round(price, 4),
                "volume": volume,
            }
        )
    return out


ACTIONS = [
    {
        "ex_date": "2026-01-21",
        "action_type": "DIVIDEND",
        "purpose": "Interim Dividend - Rs 20 Per Share",
        "factor": 1.0,
    },
    {"ex_date": "2026-02-26", "action_type": "SPLIT", "purpose": "Split", "factor": 0.5},
    {
        "ex_date": "2026-06-10",
        "action_type": "DIVIDEND",
        "purpose": "Dividend - Rs 3 Per Share /Special Dividend - Rs 2 Per Share",
        "factor": 1.0,
    },
    {
        "ex_date": "2025-06-10",
        "action_type": "DIVIDEND",
        "purpose": "Dividend - Rs 50 Per Share",
        "factor": 1.0,
    },
]


class FakeCache:
    _sessions_fn = staticmethod(sessions)

    def __init__(self, stock=None):
        self.stock = stock if stock is not None else series(0.002)

    def stock_bars(self, symbol, start, end, adjusted=True):
        if symbol == "NOPE":
            raise india.SymbolNotFoundError("No NSE price history for NOPE")
        return {
            "bars": self.stock,
            "actions": ACTIONS,
            "warnings": [{"date": "2026-03-02", "change_pct": -25.0, "note": "demerger"}],
        }

    def index_bars(self, name, start, end):
        return {"bars": series(0.001)}


def loader(key, root):
    data = {
        "niftytotalmarket": [
            {"symbol": "DEMO", "industry": "Capital Goods", "company": "Demo Ltd"}
        ],
        "nifty100": [],
        "niftymidcap150": [{"symbol": "DEMO"}],
    }
    if key not in data:
        raise india.IndiaDataError("offline")
    return data[key]


def run(tmp_path, *argv, cache=None):
    return snap.main(
        [*argv, "--cache-dir", str(tmp_path / "c"), "--output-dir", str(tmp_path / "out")],
        cache=cache or FakeCache(),
        now_fn=lambda: NOW,
        loader=loader,
    )


def test_helpers():
    assert snap.sma([1, 2, 3], 5) is None and snap.sma([1, 2, 3, 4], 2) == 3.5
    assert snap.pct_return([100, 110], 1) == 10.0 and snap.pct_return([100], 1) is None
    assert snap.rsi([1.0] * 5) is None and snap.rsi([float(i) for i in range(20)]) == 100.0
    bars = [{"high": 11, "low": 9, "close": 10}] * 20
    assert snap.atr(bars) == pytest.approx(2.0) and snap.atr(bars[:5]) is None


def test_dividends_are_adjusted_for_the_split():
    divs = snap.ttm_dividends(ACTIONS, AS_OF)
    assert divs == {
        "ttm_regular": 13.0,
        "ttm_special": 2.0,
    }  # 20 x 0.5 + 3; the 2025 dividend is outside TTM


def test_relative_strength_and_trend():
    up, flat = series(0.003), series(0.0)
    rs = snap.relative_strength(up, flat)
    assert rs["3m"] > 0 and rs["6m"] > rs["3m"]
    assert snap.relative_strength(up[:50], flat)["6m"] is None
    assert snap.technicals(up)["trend"] == "uptrend"
    assert snap.technicals(series(-0.003))["trend"] == "downtrend"


def test_snapshot_cli(tmp_path, capsys):
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "results_calendar.json").write_text(
        json.dumps(
            {
                "events": [
                    {"symbol": "DEMO", "date": "2026-11-10"},
                    {"symbol": "DEMO", "date": "2027-01-15", "quarter": "q3_27"},
                    {"symbol": "DEMO", "date": "2026-12-01"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert run(tmp_path, "demo") == 0
    data = json.loads(
        next((tmp_path / "out").glob("india_stock_snapshot_DEMO_*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert data["classification"] == {
        "industry": "Capital Goods",
        "company": "Demo Ltd",
        "tier": "Mid",
    }
    assert data["next_results"] == {"date": "2026-12-01", "quarter": None}
    assert data["technicals"]["avg_turnover_cr_50d"] > 0
    assert data["dividends"]["regular_yield_pct"] > 0
    md = next((tmp_path / "out").glob("*.md")).read_text(encoding="utf-8")
    assert (
        "DEMO — NSE Fact Sheet" in md
        and "Warning: -25.0% move" in md
        and "Next results: 2026-12-01" in md
    )
    assert "DEMO: ₹" in capsys.readouterr().out


def test_snapshot_without_archive_or_classification(tmp_path):
    assert (
        snap.main(
            ["OTHER", "--cache-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")],
            cache=FakeCache(),
            now_fn=lambda: NOW,
            loader=lambda key, root: (_ for _ in ()).throw(india.IndiaDataError("offline")),
        )
        == 0
    )
    data = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert data["next_results"] is None and data["classification"]["tier"] == "Unknown"


@pytest.mark.parametrize(
    "argv,message",
    [
        (("NOPE",), "No NSE price history"),
        (("../x",), "Invalid NSE symbol"),
        (("DEMO", "--as-of", "2026-12-31"), "after the latest"),
    ],
)
def test_errors(tmp_path, capsys, argv, message):
    assert run(tmp_path, *argv) == 1
    assert message in capsys.readouterr().err


def test_short_history_is_an_error(tmp_path, capsys):
    assert run(tmp_path, "DEMO", cache=FakeCache(series(0.001, n=10))) == 1
    assert "only 10 sessions" in capsys.readouterr().err
