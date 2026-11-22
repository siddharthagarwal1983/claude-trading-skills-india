"""Tests for India breadth calculations and the analyzer CLI."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import breadth_calculator as bc
import india_breadth_analyzer as cli
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


def series(n=300, drift=0.002, end=END, start_price=100.0, wobble=0.0):
    days = sessions(end - timedelta(days=n * 2), end)[-n:]
    out, price = [], start_price
    for i, d in enumerate(days):
        price *= 1 + drift + (wobble if i % 2 else -wobble)
        out.append(
            {
                "date": d.isoformat(),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": round(price, 4),
                "volume": 1000,
            }
        )
    return out


# --------------------------------------------------------------------------- calculator


def test_stock_flags_sma_and_change():
    bars = [
        {"date": f"d{i:03d}", "high": c, "low": c, "close": c}
        for i, c in enumerate([10.0] * 19 + [11.0, 9.0])
    ]
    flags = bc.stock_flags(bars)
    assert flags["d018"]["above_20"] is None  # 19 bars: no 20-day SMA yet
    assert flags["d019"]["above_20"] is True  # 11 > mean(19x10, 11)
    assert flags["d020"]["above_20"] is False
    assert (
        flags["d019"]["change"] == 1
        and flags["d020"]["change"] == -1
        and flags["d000"]["change"] is None
    )
    assert flags["d020"]["above_200"] is None


def test_new_high_and_low_need_a_full_year():
    rising = series(260, drift=0.001)
    flags = bc.stock_flags(rising)
    dates = [b["date"] for b in rising]
    assert flags[dates[250]]["new_high"] is None  # 251 bars: prior window not complete
    assert flags[dates[251]]["new_high"] is True and flags[dates[251]]["new_low"] is False
    falling = series(260, drift=-0.001)
    assert bc.stock_flags(falling)[falling[-1]["date"]]["new_low"] is True


def test_rolling_extreme_requires_strict_break():
    flags = bc._rolling_extreme_flags([1, 3, 3, 2, 4], window=3, highest=True)
    assert flags == [None, None, False, False, True]


def test_daily_breadth_aggregates_measurable_stocks_only():
    up, down = series(220, 0.003), series(220, -0.003)
    young = series(30, 0.003)
    flags = {"UP": bc.stock_flags(up), "DOWN": bc.stock_flags(down), "YOUNG": bc.stock_flags(young)}
    row = bc.daily_breadth(flags, [up[-1]["date"]])[0]
    assert row["pct_above_200"] == 50.0 and row["measured_200"] == 2  # YOUNG has < 200 bars
    assert row["pct_above_20"] == pytest.approx(66.67)
    assert (row["advances"], row["declines"]) == (2, 1)
    assert row["net_new_highs_pct"] is None  # nobody has a 52-week window yet


def test_sma_and_ema_helpers():
    assert bc.sma([1, 2, 3, None, 5], 2) == [None, 1.5, 2.5, None, None]
    assert bc.ema([None, 10, 20], 3) == [None, 10, 15.0]


def test_derived_series_mcclellan():
    rows = [{"pct_above_200": 50.0, "advances": a, "declines": d} for a, d in [(300, 200)] * 60]
    out = bc.add_derived_series(rows)
    assert out[-1]["breadth_8ma"] == 50.0 and out[-1]["breadth_50ma"] == 50.0
    assert out[-1]["mcclellan_oscillator"] is not None
    assert out[-1]["mcclellan_summation"] == pytest.approx(
        sum(r["mcclellan_oscillator"] for r in out), abs=0.1
    )


@pytest.mark.parametrize(
    "value,points,expected",
    [
        (None, [(0, 0), (1, 100)], None),
        (-5, [(0, 0), (10, 100)], 0),
        (5, [(0, 0), (10, 100)], 50),
        (99, [(0, 0), (10, 100)], 100),
    ],
)
def test_interp(value, points, expected):
    assert bc._interp(value, points) == expected


def _row(p200, p50, ma8, ma50, nnh, osc):
    return {
        "pct_above_200": p200,
        "pct_above_50": p50,
        "breadth_8ma": ma8,
        "breadth_50ma": ma50,
        "net_new_highs_pct": nnh,
        "mcclellan_oscillator": osc,
    }


def test_score_zones_and_flags():
    strong = bc.score_latest([_row(75, 75, 70, 60, 6, 60)] * 10)
    assert strong["zone"] == "Strong" and strong["composite_score"] >= 80
    rows = [_row(40, 30, 40, 55, -2, -20)] * 5 + [_row(20, 10, 30, 50, -6, -60)]
    weak = bc.score_latest(rows)
    assert weak["zone"] in ("Weak", "Critical")
    assert weak["flags"]["washout"] is True and weak["flags"]["deteriorating"] is True
    partial = bc.score_latest([_row(60, 60, None, None, None, None)])
    assert partial["missing_components"] == ["mcclellan", "new_highs_lows", "trend"]
    assert partial["composite_score"] is not None
    empty = bc.score_latest([_row(None, None, None, None, None, None)])
    assert empty["zone"] == "Unavailable"


def test_percentile_rank():
    assert bc.percentile_rank([10, 20, None, 30], 20) == pytest.approx(66.7)
    assert bc.percentile_rank([], 5) is None


# --------------------------------------------------------------------------- CLI


class FakeCache:
    def __init__(self, stocks, index):
        self.stocks, self.index = stocks, index
        self._sessions_fn = sessions
        self.client = None

    def stock_bars(self, symbol, start, end, adjusted=True):
        value = self.stocks[symbol]
        if isinstance(value, Exception):
            raise value
        return {"bars": [b for b in value if start.isoformat() <= b["date"] <= end.isoformat()]}

    def index_bars(self, name, start, end):
        return {
            "bars": [b for b in self.index if start.isoformat() <= b["date"] <= end.isoformat()]
        }


def run(tmp_path, monkeypatch, stocks, *extra, market_ad=None):
    monkeypatch.setattr(
        india, "load_universe", lambda key, root, **kw: [{"symbol": s} for s in stocks]
    )
    return cli.main(
        ["--output-dir", str(tmp_path), "--cache-dir", str(tmp_path / "c"), *extra],
        cache=FakeCache(stocks, series(400)),
        now_fn=lambda: NOW,
        market_ad_fn=lambda day: market_ad,
    )


def report(tmp_path):
    return json.loads(
        next(tmp_path.glob("india_market_breadth_*.json")).read_text(encoding="utf-8")
    )


def test_broad_rally_scores_strong(tmp_path, monkeypatch, capsys):
    stocks = {f"S{i:02d}": series(400, 0.002 + i * 0.0001, wobble=0.004) for i in range(20)}
    ad = {
        "date": "2026-11-20",
        "total_stocks": 3742,
        "advances": 2000,
        "declines": 1600,
        "unchanged": 142,
        "ad_ratio": 1.25,
    }
    assert run(tmp_path, monkeypatch, stocks, market_ad=ad) == 0
    result = report(tmp_path)
    assert result["as_of"] == END.isoformat()
    assert result["latest"]["pct_above_200"] == 100.0
    assert result["composite"]["zone"] in ("Strong", "Healthy")
    assert result["coverage"] == {
        "universe_size": 20,
        "loaded": 20,
        "not_loaded": {},
        "verdict": "complete",
    }
    assert result["nse_market_breadth"]["advances"] == 2000
    assert len(result["history"]) == cli.REPORT_SESSIONS
    md = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "India Market Breadth Report (NSE)" in md and "NSE-wide (3742 securities)" in md
    assert "Breadth score" in capsys.readouterr().out


def test_broad_selloff_scores_weak(tmp_path, monkeypatch):
    stocks = {f"S{i:02d}": series(400, -0.002, wobble=0.004) for i in range(20)}
    assert run(tmp_path, monkeypatch, stocks, "--no-market-ad") == 0
    result = report(tmp_path)
    assert result["composite"]["zone"] in ("Weak", "Critical")
    assert result["latest"]["pct_above_200"] == 0.0
    assert result["nse_market_breadth"] is None


def test_low_coverage_is_refused(tmp_path, monkeypatch, capsys):
    stocks = {f"S{i:02d}": series(400) for i in range(8)}
    stocks.update({f"X{i}": india.SymbolNotFoundError("gone") for i in range(2)})
    assert run(tmp_path, monkeypatch, stocks) == 1
    assert "only 8 of 10" in capsys.readouterr().err


def test_partial_coverage_above_threshold_is_reported(tmp_path, monkeypatch):
    stocks = {f"S{i:02d}": series(400) for i in range(19)}
    stocks["GONE"] = india.SymbolNotFoundError("gone")
    assert run(tmp_path, monkeypatch, stocks) == 0
    cov = report(tmp_path)["coverage"]
    assert cov["verdict"] == "partial" and cov["not_loaded"] == {"GONE": "missing_data"}


@pytest.mark.parametrize("value,message", [("2026-12-01", "after the latest"), ("x", "YYYY-MM-DD")])
def test_bad_as_of(tmp_path, monkeypatch, capsys, value, message):
    assert run(tmp_path, monkeypatch, {"S": series(400)}, "--as-of", value) == 1
    assert message in capsys.readouterr().err


def test_calendar_must_reach_as_of(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(india, "load_universe", lambda key, root, **kw: [{"symbol": "S"}])
    cache = FakeCache({"S": series(400)}, series(400, end=END - timedelta(days=7)))
    rc = cli.main(
        ["--output-dir", str(tmp_path)],
        cache=cache,
        now_fn=lambda: NOW,
        market_ad_fn=lambda d: None,
    )
    assert rc == 1 and "no bar for" in capsys.readouterr().err


def test_nse_market_breadth_parses_and_tolerates_errors():
    class Client:
        def __init__(self, payload):
            self.payload = payload

        def call_tool(self, name, args):
            if isinstance(self.payload, Exception):
                raise self.payload
            return self.payload

    ok = cli.nse_market_breadth(
        Client({"date": "2026-11-20", "advances": 1, "declines": 2, "extra": 9}), END
    )
    assert ok["advances"] == 1 and "extra" not in ok
    assert cli.nse_market_breadth(Client(india.McpTransportError("down")), END) is None
    assert cli.nse_market_breadth(Client("text"), END) is None
