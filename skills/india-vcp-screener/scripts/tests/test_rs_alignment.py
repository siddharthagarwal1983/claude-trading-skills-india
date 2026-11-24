"""Relative strength joins stock and benchmark bars on trading dates (Phase 1.1, F9).

The RS calculator compares ``closes[0]`` with ``closes[63/126/189/252]`` of each series
separately. Without a date join, one session missing from either series shifts every
comparison by a session. ``--rs-alignment offset`` keeps the old behaviour.
"""

from datetime import date, timedelta

import pytest
import screen_india_vcp as vcp
from calculators.relative_strength_calculator import calculate_relative_strength


def weekdays(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = [d.isoformat() for d in weekdays(date(2025, 6, 2), date(2026, 11, 20))][-300:]


def series(days, scale=1.0, step=0.004):
    """Newest-first bars whose closes compound by ``step`` a session (any scale)."""
    bars = []
    for i, day in enumerate(days):
        close = scale * 100 * (1 + step) ** i * (1 + 0.03 * ((i % 7) - 3) / 3)
        bars.append(
            {
                "date": day,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000,
            }
        )
    return list(reversed(bars))


def drop(bars, day):
    return [b for b in bars if b["date"] != day]


MISSING = DAYS[-30]  # inside the 63-session window


def test_benchmark_missing_a_date():
    stock = series(DAYS)
    bench = drop(series(DAYS, scale=200), MISSING)  # same path, one session missing
    offset = calculate_relative_strength(stock, bench)
    s, b, info = vcp.align_for_rs(stock, bench)
    aligned = calculate_relative_strength(s, b)
    assert aligned["weighted_rs"] == pytest.approx(0.0, abs=1e-6)
    assert abs(offset["weighted_rs"]) > 0.05  # every period compared one session apart
    assert info["dropped_stock_dates"] == [MISSING]
    assert info["dropped_benchmark_dates"] == []
    assert [x["date"] for x in s] == [x["date"] for x in b]


def test_stock_missing_a_date():
    stock = drop(series(DAYS), MISSING)  # e.g. a suspended session
    bench = series(DAYS, scale=200)
    s, b, info = vcp.align_for_rs(stock, bench)
    assert calculate_relative_strength(s, b)["weighted_rs"] == pytest.approx(0.0, abs=1e-6)
    assert info["dropped_benchmark_dates"] == [MISSING]
    assert info["dropped_stock_dates"] == []


def test_different_histories_use_common_dates_only():
    stock = series(DAYS[-120:])  # newly listed: 120 sessions
    bench = series(DAYS, scale=200)  # 300 sessions
    s, b, info = vcp.align_for_rs(stock, bench)
    assert len(s) == len(b) == 120
    assert s[0]["date"] == b[0]["date"] == DAYS[-1]
    assert info["dropped_benchmark_dates"] == []  # sessions before listing are not gaps
    assert info["common_sessions"] == 120
    assert info["latest_common_date"] == DAYS[-1]
    assert info["latest_date_mismatch"] is False


def test_benchmark_missing_the_latest_session_is_flagged():
    stock = series(DAYS)
    bench = drop(series(DAYS, scale=200), DAYS[-1])
    s, b, info = vcp.align_for_rs(stock, bench)
    assert info["latest_date_mismatch"] is True
    assert info["latest_common_date"] == DAYS[-2]
    assert s[0]["date"] == b[0]["date"] == DAYS[-2]  # never pairs different dates


def test_lookback_counts_common_sessions_after_alignment():
    stock = series(DAYS)
    bench = drop(series(DAYS, scale=200), MISSING)
    s, b, _ = vcp.align_for_rs(stock, bench)
    assert s[63]["date"] == b[63]["date"]  # the 3-month comparison pairs one date
    assert s[63]["date"] == DAYS[-65]  # 63 common sessions back (one day dropped)


def quote_for(history):
    closes = [b["close"] for b in history[:252]]
    return {"price": history[0]["close"], "yearHigh": max(closes), "yearLow": min(closes)}


@pytest.mark.parametrize("mode, rs_is_zero", [("date", True), ("offset", False)])
def test_analyze_stock_applies_the_alignment_mode(mode, rs_is_zero):
    stock = series(DAYS)
    bench = drop(series(DAYS, scale=200), MISSING)
    out = vcp.analyze_stock("ACME", stock, quote_for(stock), bench, rs_alignment=mode)
    rs = out["relative_strength"]
    assert (abs(rs["weighted_rs"]) < 1e-6) is rs_is_zero
    assert rs["alignment"]["mode"] == mode


def test_cli_flag_defaults_to_date_alignment():
    assert vcp.parse_arguments([]).rs_alignment == "date"
    assert vcp.parse_arguments(["--rs-alignment", "offset"]).rs_alignment == "offset"
    with pytest.raises(SystemExit):
        vcp.parse_arguments(["--rs-alignment", "nearest"])
