"""Momentum-burst strategy: screening parity, entry/exit rules and the CLI (offline)."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import india_swing_backtest as cli
import pytest
import swing_backtest_engine as engine
from swing_backtest_siblings import load, load_all

SCREENER, PLANNER, PAPER = load_all()
MB = load("momentum")
IST = timezone(timedelta(hours=5, minutes=30))
DAYS = [
    "2026-03-02",
    "2026-03-03",
    "2026-03-04",
    "2026-03-05",
    "2026-03-06",
    "2026-03-09",
    "2026-03-10",
]


def weekdays(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


@pytest.fixture(autouse=True)
def fake_calendar(monkeypatch):
    monkeypatch.setattr(MB, "SESSIONS_FN", weekdays)
    MB._SESSION_CACHE.clear()

    def offline(key, cache_root, **kwargs):
        raise india.IndiaDataError(f"offline: {key}")

    monkeypatch.setattr(india, "load_universe", offline)


def bar(day, o, h, low, c, volume=1_000_000):
    return {
        "date": day,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": volume,
        "adj_factor": 1.0,
    }


def signal(
    day=DAYS[0], state="ACTIONABLE_DAY1", score=85, ref=100.0, stop=95.0, tags=None, symbol="ACME"
):
    return {
        "symbol": symbol,
        "date": day,
        "state": state,
        "rating": "A",
        "setup_score": score,
        "entry_reference": ref,
        "stop_reference": stop,
        "risk_pct_to_stop": 5.0,
        "primary_trigger": "4pct_breakout",
        "soft_failure_tags": tags or [],
        "day_gain_pct": 5.0,
        "turnover_cr": 10.0,
    }


def market(stock_bars):
    bench = engine.Series([bar(d, 1000, 1000, 1000, 1000) for d in DAYS])
    rows = [{"symbol": "ACME", "company": "ACME Ltd", "industry": "Capital Goods"}]
    return engine.MarketData(rows, {"ACME": engine.Series(stock_bars)}, bench, "NIFTY 500")


def run(stock_bars, results, **params):
    screens = {DAYS[0]: {"date": DAYS[0], "results": results}}
    p = engine.SimParams.for_strategy("momentum-burst", **params)
    return engine.Simulator(market(stock_bars), screens, p, PLANNER, PAPER).run(DAYS)


TRIGGER = bar(DAYS[0], 95.5, 100.5, 95, 100)
FLAT = [bar(d, 101, 102, 100.5, 101.5) for d in DAYS[2:]]


def test_defaults_for_momentum():
    p = engine.SimParams.for_strategy("momentum-burst")
    assert (p.target_r_multiple, p.max_hold_sessions, p.max_risk_pct, p.min_score) == (
        0.0,
        5,
        10.0,
        80.0,
    )
    assert (
        engine.SimParams.for_strategy("momentum-burst", max_hold_sessions=3).max_hold_sessions == 3
    )
    with pytest.raises(ValueError):
        engine.SimParams.for_strategy("bogus")


def test_fills_at_open_below_limit_then_time_exit():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    out = run(bars, [signal()], max_hold_sessions=3)
    (trade,) = out["trades"]
    assert trade["entry_price"] == 101  # LIMIT 102.00 (= 100 + 2%) filled at the open, no slippage
    assert trade["stop"] == 95.0 and trade["target"] is None
    assert trade["exit_reason"] == "time_stop"
    assert trade["exit_session"] == DAYS[4] and trade["sessions_held"] == 3
    assert trade["exit_price"] == 101.5
    qty = trade["qty"]
    assert 650 <= qty <= 714  # 0.5% of ₹10 lakh over ₹7 risk at the limit, less charges


def test_fills_at_limit_when_open_is_above():
    bars = [TRIGGER, bar(DAYS[1], 103, 104, 101.5, 103)] + FLAT
    (trade,) = run(bars, [signal()], max_hold_sessions=2)["trades"]
    assert trade["entry_price"] == 102.0


def test_gap_above_limit_is_missed():
    bars = [TRIGGER, bar(DAYS[1], 104, 106, 103, 105)] + FLAT
    out = run(bars, [signal()])
    assert out["trades"] == [] and out["open_positions"] == []
    assert out["events"]["orders_gapped_past_limit"] == 1


def test_stop_at_trigger_low_with_slippage():
    bars = [TRIGGER, bar(DAYS[1], 101, 102, 100, 101), bar(DAYS[2], 99, 99.5, 94, 95)] + FLAT[1:]
    (trade,) = run(bars, [signal()])["trades"]
    assert trade["exit_reason"] == "stop_hit"
    assert trade["exit_price"] == pytest.approx(94.91)  # 95.00 less 10 bps = 94.905, rounded up
    assert trade["r_multiple"] < -0.8


def test_optional_target():
    bars = [TRIGGER, bar(DAYS[1], 101, 102, 100, 101), bar(DAYS[2], 105, 117, 104, 116)] + FLAT[1:]
    (trade,) = run(bars, [signal()], target_r_multiple=2.0)["trades"]
    assert trade["target"] == 116.0  # 102 + 2 x (102 - 95)
    assert trade["exit_reason"] == "target_hit"


@pytest.mark.parametrize(
    "sig, params, why",
    [
        (signal(state="MANUAL_REVIEW", score=75), {}, "score below 80"),
        (signal(state="WATCH_ONLY", score=90), {}, "watch only"),
        (signal(tags=["possible_upper_circuit"]), {}, "upper circuit"),
        (signal(day=DAYS[1]), {}, "not today's signal"),
        (signal(stop=85.0), {}, "risk 16.7% > 10%"),
    ],
)
def test_signals_not_traded(sig, params, why):
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    assert run(bars, [sig], **params)["events"]["orders_filled"] == 0, why


def test_lower_min_score_takes_manual_review():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    out = run(bars, [signal(state="MANUAL_REVIEW", score=75)], min_score=70)
    assert out["events"]["orders_filled"] == 1


def test_risk_cap_counts_rejections():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    assert run(bars, [signal(stop=85.0)])["events"]["rejected_risk"] == 1


def test_momentum_requires_time_stop():
    with pytest.raises(ValueError, match="max_hold_sessions"):
        engine.validate_params(engine.SimParams(strategy="momentum-burst", target_r_multiple=0))


# --------------------------------------------------------------------------- parity


AS_OF = date(2026, 3, 31)


def burst_series(base=500.0, n=60, gain_pct=5.0, end=AS_OF, burst=True):
    days = weekdays(end - timedelta(days=n * 2), end)[-n:]
    rows = []
    for i, day in enumerate(days[:-1]):
        close = base * (1 + 0.002 * ((-1) ** i))
        rows.append(
            {
                "date": day.isoformat(),
                "open": close,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": 300_000,
            }
        )
    prev = rows[-1]["close"]
    close = prev * (1 + (gain_pct if burst else 0.1) / 100)
    rows.append(
        {
            "date": days[-1].isoformat(),
            "open": prev * 1.001,
            "high": close * 1.003,
            "low": prev * 1.001 * 0.995,
            "close": close,
            "volume": 750_000,
        }
    )
    return rows


STOCKS = {
    "BURST": burst_series(),
    "BIGBURST": burst_series(base=900, gain_pct=6.0),
    "QUIET": burst_series(burst=False),
}


class FakeCache:
    def stock_bars(self, symbol, start, end, adjusted=True):
        return {
            "bars": [b for b in STOCKS[symbol] if start.isoformat() <= b["date"] <= end.isoformat()]
        }


def test_screen_day_momentum_matches_live_screener(tmp_path):
    now = datetime(2026, 3, 31, 20, 0, tzinfo=IST)
    assert (
        MB.main(
            [
                "--symbols",
                *STOCKS,
                "--output-dir",
                str(tmp_path),
                "--cache-dir",
                str(tmp_path / "c"),
            ],
            cache=FakeCache(),
            now_fn=lambda: now,
        )
        == 0
    )
    live = json.loads(next(tmp_path.glob("india_stockbee_momentum_burst_*.json")).read_text())
    expected = [
        {k: c.get(k) for k in engine.MB_FIELDS}
        for c in live["candidates"]
        if c["state"] != "REJECTED"
    ]
    assert expected, "fixture should produce bursts"
    rows = [{"symbol": s} for s in sorted(STOCKS)]
    data = engine.MarketData(
        rows,
        {s: engine.Series(b) for s, b in STOCKS.items()},
        engine.Series(STOCKS["QUIET"]),
        "NIFTY 500",
    )
    args = MB.parse_arguments(["--symbols", *STOCKS])
    ours = engine.screen_day_momentum(AS_OF.isoformat(), data, MB, args)
    assert ours["results"] == expected
    assert ours["funnel"]["analyzed"] == 3


# --------------------------------------------------------------------------- CLI


def test_cli_momentum_end_to_end(tmp_path, capsys):
    root = tmp_path / "cache"
    stocks = {
        s: burst_series(base=200 + 50 * i, n=180, gain_pct=5.0 + i)
        for i, s in enumerate(["AAA", "BBB", "CCC"])
    }
    for sym, bars in stocks.items():
        path = root / "stocks" / f"{sym}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bars": bars, "actions": [], "coverage_end": bars[-1]["date"]}))
    bench = burst_series(base=20000, n=180, burst=False)
    path = root / "indices" / "NIFTY_500.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"bars": bench, "coverage_end": bench[-1]["date"]}))
    out = tmp_path / "out"
    code = cli.main(
        [
            "--strategy",
            "momentum-burst",
            "--symbols",
            *stocks,
            "--no-fetch",
            "--cache-dir",
            str(root),
            "--data-start",
            "2025-01-01",
            "--end",
            AS_OF.isoformat(),
            "--output-dir",
            str(out),
            "--sweep",
            "max_hold_sessions=3,5",
        ],
        now_fn=lambda: datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
    )
    assert code == 0
    report = json.loads(
        next(out.glob("india_swing_backtest_momentum-burst_custom_*.json")).read_text()
    )
    assert report["strategy"] == "momentum-burst"
    assert report["base"]["params"]["max_hold_sessions"] == 5
    assert len(report["sweep"]) == 2
    md = next(out.glob("india_swing_backtest_momentum-burst_*.md")).read_text()
    assert md.startswith("# India Momentum Burst Backtest") and "trigger-day low" in md
    assert list((root / "backtest").glob("momentum_screens_*.jsonl"))
    assert "India Momentum Burst Backtest" in capsys.readouterr().out


def test_screen_key_differs_by_strategy():
    data = market([bar(d, 1, 1, 1, 1) for d in DAYS])
    ns = argparse.Namespace(x=1, _expected_session="2026-01-01")
    assert engine.screen_key("u", "b", ns, 20, data, "vcp") != engine.screen_key(
        "u", "b", ns, 20, data, "momentum-burst"
    )
