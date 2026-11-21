"""Tests for the India VCP screener (NSE data adapter + funnel)."""

import json
import math
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import pytest
import screen_india_vcp as vcp

IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = date(2026, 11, 20)
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)


def weekdays(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def make_bars(n=320, start_price=200.0, drift=0.003, volume=1_000_000, end=AS_OF, wiggle=0.012):
    days = weekdays(end - timedelta(days=n * 2), end)[-n:]
    bars, price = [], start_price
    for i, day in enumerate(days):
        price *= 1 + drift + wiggle * math.sin(i / 3.0) * 0.5
        bars.append(
            {
                "date": day.isoformat(),
                "open": round(price * 0.995, 2),
                "high": round(price * 1.012, 2),
                "low": round(price * 0.985, 2),
                "close": round(price, 2),
                "prev_close": None,
                "volume": volume,
                "value": None,
                "series": "EQ",
                "adj_factor": 1.0,
            }
        )
    return bars


@pytest.fixture(autouse=True)
def offline_universe(monkeypatch):
    """Never download NSE constituent files in tests."""
    rows = [{"symbol": "LEADER", "company": "Leader Ltd", "industry": "Capital Goods"}]

    def fake_load(key, cache_root, **kwargs):
        if key != "niftytotalmarket":
            raise india.IndiaDataError(f"offline: {key}")
        return rows

    monkeypatch.setattr(india, "load_universe", fake_load)


class FakeCache:
    def __init__(self, stocks, benchmark, warnings=None):
        self.stocks = stocks
        self.benchmark = benchmark
        self.warnings = warnings or {}
        self._sessions_fn = weekdays

    def stock_bars(self, symbol, start, end, adjusted=True):
        value = self.stocks[symbol]
        if isinstance(value, Exception):
            raise value
        return {"symbol": symbol, "bars": value, "warnings": self.warnings.get(symbol, [])}

    def index_bars(self, name, start, end):
        return {"index": name, "bars": self.benchmark}


# --------------------------------------------------------------------------- adapters


def test_to_history_is_newest_first_with_ohlcv_only():
    bars = make_bars(5)
    hist = vcp.to_history(bars)
    assert [h["date"] for h in hist] == [b["date"] for b in reversed(bars)]
    assert set(hist[0]) == {"date", "open", "high", "low", "close", "volume"}


def test_synthesize_quote_uses_rupee_turnover():
    hist = vcp.to_history(make_bars(300, start_price=100.0, drift=0.0, wiggle=0.0, volume=200_000))
    quote = vcp.synthesize_quote(hist, "Demo Ltd", "Capital Goods")
    assert quote["price"] == hist[0]["close"]
    assert quote["avgTurnover"] == pytest.approx(hist[0]["close"] * 200_000)
    assert quote["yearHigh"] >= quote["price"] >= quote["yearLow"]
    assert quote["sector"] == "Capital Goods"


@pytest.mark.parametrize(
    "quote,passed",
    [
        ({"price": 500, "yearHigh": 520, "yearLow": 300, "avgTurnover": 6e7}, True),
        ({"price": 40, "yearHigh": 42, "yearLow": 20, "avgTurnover": 6e7}, False),  # < ₹50
        ({"price": 500, "yearHigh": 520, "yearLow": 300, "avgTurnover": 4e7}, False),  # < ₹5 cr
        (
            {"price": 500, "yearHigh": 800, "yearLow": 300, "avgTurnover": 6e7},
            False,
        ),  # far off high
        ({"price": 500, "yearHigh": 520, "yearLow": 450, "avgTurnover": 6e7}, False),  # near low
    ],
)
def test_pre_filter_thresholds(quote, passed):
    assert vcp.pre_filter_stock(quote)[0] is passed


def test_recent_unadjusted_jump_only_inside_window():
    hist = vcp.to_history(make_bars(300))
    recent = [{"date": hist[10]["date"], "change_pct": -25.0, "note": "x"}]
    old = [{"date": hist[-1]["date"], "change_pct": -25.0, "note": "x"}]
    assert vcp.recent_unadjusted_jump(recent, hist) == recent[0]
    assert vcp.recent_unadjusted_jump(old, hist) is None


def test_rescore_passes_sma200_distance(monkeypatch):
    seen = {}

    def fake_score(**kwargs):
        seen.update(kwargs)
        return {
            "composite_score": 1,
            "rating": "x",
            "rating_description": "",
            "guidance": "",
            "weakest_component": "",
            "weakest_score": 0,
            "strongest_component": "",
            "strongest_score": 0,
        }

    monkeypatch.setattr(vcp, "calculate_composite_score", fake_score)
    result = {
        "trend_template": {},
        "vcp_pattern": {},
        "volume_pattern": {},
        "pivot_proximity": {},
        "relative_strength": {},
        "sma200_distance_pct": 61.0,
    }
    vcp.rescore(result)
    assert seen["sma200_extension_pct"] == 61.0  # US screener reads a non-existent key here


# --------------------------------------------------------------------------- end to end


def run(tmp_path, cache, symbols):
    return vcp.main(
        ["--symbols", *symbols, "--output-dir", str(tmp_path), "--cache-dir", str(tmp_path / "c")],
        cache=cache,
        now_fn=lambda: NOW,
    )


def test_funnel_and_coverage(tmp_path, capsys):
    jumpy = make_bars()
    stocks = {
        "LEADER": make_bars(),
        "LAGGARD": make_bars(start_price=900, drift=-0.003),
        "PENNY": make_bars(start_price=10, drift=0.003),
        "THIN": make_bars(volume=500),
        "JUMPY": jumpy,
        "GONE": india.SymbolNotFoundError("No NSE price history for GONE"),
        "STALE": make_bars(end=AS_OF - timedelta(days=7)),
    }
    warnings = {"JUMPY": [{"date": jumpy[-5]["date"], "change_pct": -30.0, "note": "demerger"}]}
    cache = FakeCache(stocks, make_bars(drift=0.001), warnings)
    assert run(tmp_path, cache, list(stocks)) == 0

    report = json.loads(
        next(tmp_path.glob("india_vcp_screener_*.json")).read_text(encoding="utf-8")
    )
    meta = report["metadata"]
    assert meta["exchange"] == "NSE" and meta["currency"] == "INR"
    assert meta["as_of"] == AS_OF.isoformat()
    assert meta["funnel"]["universe"] == 7
    assert meta["funnel"]["pre_filter_passed"] == 1  # only LEADER is liquid, priced and near highs
    coverage = report["evaluation_coverage"]
    assert coverage["universe_size"] == 7
    assert coverage["not_evaluated_reasons"] == {
        "missing_data": 1,
        "stale_price_data": 1,
        "unadjusted_corporate_action": 1,
    }
    md = next(tmp_path.glob("india_vcp_screener_*.md")).read_text(encoding="utf-8")
    assert "India VCP Screener Report" in md and "$" not in md
    assert "India VCP screening complete" in capsys.readouterr().out


def test_short_benchmark_is_an_error(tmp_path, capsys):
    cache = FakeCache({"LEADER": make_bars()}, make_bars(50))
    assert run(tmp_path, cache, ["LEADER"]) == 1
    assert "benchmark" in capsys.readouterr().err


def test_invalid_symbol_is_reported(tmp_path, capsys):
    cache = FakeCache({}, make_bars())
    assert run(tmp_path, cache, ["../x"]) == 1
    assert "Error" in capsys.readouterr().err


def test_explicit_symbols_pick_up_name_and_industry(tmp_path):
    args = vcp.parse_arguments(["--symbols", "leader", "OTHER", "LEADER"])
    rows, desc = vcp.load_universe_rows(args, tmp_path)
    assert [r["symbol"] for r in rows] == ["LEADER", "OTHER"]
    assert rows[0]["industry"] == "Capital Goods" and rows[0]["company"] == "Leader Ltd"
    assert rows[1]["industry"] == "Unknown"
    assert desc == "Custom (2 NSE stocks)"


def test_to_history_drops_bars_without_volume():
    bars = make_bars(3)
    bars[1]["volume"] = None
    hist = vcp.to_history(bars)
    assert [h["date"] for h in hist] == [bars[2]["date"], bars[0]["date"]]
