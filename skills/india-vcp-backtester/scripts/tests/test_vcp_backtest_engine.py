"""Simulation tests on hand-built bars, using the real planner and paper fill rules."""

from __future__ import annotations

import argparse
import json

import pytest
import vcp_backtest_engine as engine
from vcp_backtest_siblings import load_all

SCREENER, PLANNER, PAPER = load_all()
DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"]


def bar(day, o, h, low, c, **extra):
    return {
        "date": day,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": 1_000_000,
        "adj_factor": 1.0,
        **extra,
    }


def candidate(symbol="ACME", score=85.0, pivot=100.0, low=96.0, price=99.0):
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Ltd",
        "sector": "Capital Goods",
        "price": price,
        "composite_score": score,
        "rating": "Strong VCP",
        "execution_state": "Pre-breakout",
        "valid_vcp": True,
        "avg_turnover_cr": 100.0,
        "distance_from_pivot_pct": -1.0,
        "vcp_pattern": {"pivot_price": pivot, "contractions": [{"low_price": low}]},
        "volume_pattern": {"breakout_volume_detected": False},
        "pivot_proximity": {"distance_from_pivot_pct": -1.0},
    }


def market(stock_bars, actions=None):
    bench = engine.Series([bar(d, 1000, 1000, 1000, 1000) for d in DAYS])
    stocks = {"ACME": engine.Series(stock_bars, [], actions or [])}
    rows = [{"symbol": "ACME", "company": "ACME Ltd", "industry": "Capital Goods"}]
    return engine.MarketData(rows, stocks, bench, "NIFTY 500")


def screens(*days, **kw):
    return {d: {"date": d, "results": [candidate(**kw)]} for d in days}


def run(stock_bars, screen_map, actions=None, **params):
    params.setdefault("slippage_bps", 10.0)
    sim = engine.Simulator(
        market(stock_bars, actions), screen_map, engine.SimParams(**params), PLANNER, PAPER
    )
    return sim.run(DAYS)


BASE = [
    bar(DAYS[0], 98, 99.5, 97.5, 99),
    bar(DAYS[1], 99.5, 101, 99, 100.8),  # triggers at 100.10
    bar(DAYS[2], 101, 104, 100, 103),
]


def test_plan_prices_from_the_real_planner():
    # 96 x 0.99 is 95.0399... in floating point, so the round-down stop lands a paisa lower.
    prices = PLANNER.derive_trade_prices(100.0, 96.0)
    assert prices == (100.1, 102.0, 95.03)


def test_breakout_to_target():
    bars = BASE + [
        bar(DAYS[3], 104, 116.5, 103, 116),
        bar(DAYS[4], 116, 117, 115, 116),
        bar(DAYS[5], 116, 117, 115, 116),
    ]
    out = run(bars, screens(DAYS[0]))
    (trade,) = out["trades"]
    assert trade["entry_session"] == DAYS[1] and trade["signal_session"] == DAYS[0]
    assert trade["entry_price"] == pytest.approx(100.20)  # trigger + 10 bps
    assert trade["exit_reason"] == "target_hit"
    assert trade["exit_price"] == pytest.approx(115.94)  # 2R from the 102.00 worst entry
    assert trade["exit_session"] == DAYS[3]
    qty = trade["qty"]
    buy = PAPER.leg_charges(100.20 * qty, buy=True)
    sell = PAPER.leg_charges(115.94 * qty, buy=False)
    assert trade["charges"] == pytest.approx(buy["total"] + sell["total"], abs=0.02)
    assert trade["net_pnl"] == pytest.approx(
        (115.94 - 100.20) * qty - buy["total"] - sell["total"], abs=0.05
    )
    assert trade["r_multiple"] > 2.0  # filled below the worst entry the plan was sized on
    assert out["events"]["orders_filled"] == 1
    assert out["curve"][-1]["equity"] == pytest.approx(1_000_000 + trade["net_pnl"], abs=0.05)
    assert out["open_positions"] == []


def test_stop_exit_with_slippage():
    bars = BASE[:2] + [bar(DAYS[2], 100, 101, 94, 95)] + [bar(d, 95, 96, 94, 95) for d in DAYS[3:]]
    (trade,) = run(bars, screens(DAYS[0]))["trades"]
    assert trade["exit_reason"] == "stop_hit"
    assert trade["exit_price"] == pytest.approx(94.93)  # 95.03 stop less 10 bps
    assert trade["net_pnl"] < 0


def test_bar_touching_stop_and_target_counts_as_stop():
    bars = (
        BASE[:2] + [bar(DAYS[2], 101, 117, 94, 100)] + [bar(d, 100, 101, 99, 100) for d in DAYS[3:]]
    )
    (trade,) = run(bars, screens(DAYS[0]))["trades"]
    assert trade["exit_reason"] == "stop_hit"


def test_no_exit_on_entry_day():
    bars = [BASE[0], bar(DAYS[1], 99.5, 101, 90, 95)] + [bar(d, 96, 97, 95.5, 96) for d in DAYS[2:]]
    out = run(bars, screens(DAYS[0]))
    assert out["trades"] == []
    assert out["open_positions"][0]["symbol"] == "ACME"


def test_gap_past_limit_does_not_fill():
    bars = [BASE[0]] + [bar(d, 103, 105, 102.5, 104) for d in DAYS[1:]]
    out = run(bars, screens(DAYS[0]))
    assert out["trades"] == [] and out["open_positions"] == []
    assert out["events"]["orders_gapped_past_limit"] == 1
    assert out["events"]["orders_expired"] == 1


def test_circuit_locked_session_does_not_fill():
    bars = [BASE[0], bar(DAYS[1], 101, 101, 101, 101)] + [
        bar(d, 99, 99.5, 98, 99) for d in DAYS[2:]
    ]
    out = run(bars, screens(DAYS[0]))
    assert out["events"]["orders_locked"] == 1
    assert out["open_positions"] == []


def test_held_symbol_is_not_planned_again():
    bars = BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]]
    out = run(bars, screens(*DAYS))
    assert out["events"]["orders_filled"] == 1
    assert len(out["open_positions"]) == 1


def test_time_stop():
    bars = BASE[:2] + [bar(d, 101, 102, 100, 101) for d in DAYS[2:]]
    (trade,) = run(bars, screens(DAYS[0]), max_hold_sessions=2)["trades"]
    assert trade["exit_reason"] == "time_stop"
    assert trade["exit_session"] == DAYS[3]
    assert trade["exit_price"] == 101


def test_dividend_is_credited():
    bars = BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]]
    actions = [
        {
            "ex_date": DAYS[2],
            "action_type": "DIVIDEND",
            "purpose": "Interim Dividend - Rs 5 Per Share",
            "factor": 1.0,
        }
    ]
    out = run(bars, screens(DAYS[0]), actions=actions)
    qty = out["open_positions"][0]["qty"]
    assert out["events"]["dividends"] == pytest.approx(5 * qty)


def test_low_score_is_not_traded():
    out = run(BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]], screens(DAYS[0], score=65.0))
    assert out["events"]["actionable_plans"] == 0


def test_too_wide_stop_is_not_traded():
    out = run(BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]], screens(DAYS[0], low=90.0))
    assert out["events"]["actionable_plans"] == 0  # 11.8% risk > the planner's 8% limit


def test_slim_result_passes_planner_validation():
    full = candidate()
    full["vcp_pattern"]["contractions"] = [
        {"low_price": 90.0, "depth_pct": 12},
        {"low_price": 96.0},
    ]
    full["trend_template"] = {"score": 100}
    slim = engine.slim_result(full)
    assert PLANNER.validate_result(slim) == []
    assert slim["vcp_pattern"]["contractions"] == [{"low_price": 96.0}]
    assert "trend_template" not in slim


# --------------------------------------------------------------------------- screening


class RecordingScreener:
    """Stands in for the screener and records which bars each day may see."""

    MIN_HISTORY_BARS = 1

    def __init__(self):
        self.seen = {}

    @staticmethod
    def to_history(bars):
        return list(reversed(bars))

    def prepare_history(self, sym, bars, warnings, day):
        self.seen[(sym, day)] = (bars[-1]["date"], [w["date"] for w in warnings])
        return list(reversed(bars)), None

    def run_funnel(self, rows, histories, bench, args, outcomes):
        return [candidate()], [1], [1]


def test_screen_day_never_sees_later_bars():
    bars = [bar(d, 100, 101, 99, 100) for d in DAYS]
    data = market(bars)
    data.stocks["ACME"].warnings = [{"date": DAYS[1]}, {"date": DAYS[4]}]
    rec = RecordingScreener()
    out = engine.screen_day(DAYS[2], data, rec, argparse.Namespace(), top=20)
    assert rec.seen[("ACME", DAYS[2])] == (DAYS[2], [DAYS[1]])
    assert out["results"][0]["symbol"] == "ACME"
    assert out["funnel"]["vcp_candidates"] == 1


def test_screen_cache_resumes_and_skips_partial_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    cache = engine.ScreenCache(path)
    cache.add({"date": DAYS[0], "results": []})
    with open(path, "a") as handle:
        handle.write('{"date": "2026-03-03", "res')
    again = engine.ScreenCache(path)
    assert list(again.days) == [DAYS[0]]
    calls = []

    class Counting(RecordingScreener):
        def run_funnel(self, *a):
            calls.append(1)
            return [], [], []

    engine.screen_range(
        DAYS[:3],
        market([bar(d, 1, 1, 1, 1) for d in DAYS]),
        Counting(),
        argparse.Namespace(),
        20,
        again,
    )
    assert len(calls) == 2


def test_screen_key_tracks_settings_and_splits():
    data = market([bar(d, 1, 1, 1, 1) for d in DAYS])
    a = engine.screen_key("u", "NIFTY 500", argparse.Namespace(x=1), 20, data)
    assert a == engine.screen_key(
        "u", "NIFTY 500", argparse.Namespace(x=1, output_dir="o"), 20, data
    )
    assert a != engine.screen_key("u", "NIFTY 500", argparse.Namespace(x=2), 20, data)
    data.stocks["ACME"].actions = [{"ex_date": DAYS[1], "factor": 0.5}]
    assert a != engine.screen_key("u", "NIFTY 500", argparse.Namespace(x=1), 20, data)


# --------------------------------------------------------------------------- sweeps


def test_parse_sweep_builds_grid():
    grid = engine.parse_sweep(["target_r_multiple=1.5,2", "stop-buffer-pct=0.5,1,2"])
    assert len(grid) == 6
    assert {"target_r_multiple": 1.5, "stop_buffer_pct": 0.5} in grid
    assert engine.parse_sweep(["valid_sessions=1,2"]) == [
        {"valid_sessions": 1},
        {"valid_sessions": 2},
    ]


@pytest.mark.parametrize(
    "spec", ["nope=1", "capital=5", "target_r_multiple", "target_r_multiple=a"]
)
def test_parse_sweep_rejects(spec):
    with pytest.raises(ValueError):
        engine.parse_sweep([spec])


def test_apply_overrides():
    p = engine.apply_overrides(engine.SimParams(), {"target_r_multiple": 3.0})
    assert p.target_r_multiple == 3.0 and p.capital == 1_000_000.0
    with pytest.raises(ValueError):
        engine.apply_overrides(engine.SimParams(), {"bogus": 1})


def test_series_helpers():
    s = engine.Series([bar(d, 1, 1, 1, i) for i, d in enumerate(reversed(DAYS))])
    assert s.dates == DAYS
    assert [b["date"] for b in s.window(DAYS[1], DAYS[3])] == DAYS[1:4]
    assert s.last_on_or_before("2026-03-08")["date"] == DAYS[4]
    assert s.last_on_or_before("2026-01-01") is None
    assert json.dumps(engine.slim_result(candidate()))


# --------------------------------------------------------------------------- research rules


def test_wider_risk_cap_trades_the_wide_stop():
    bars = BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]]
    out = run(bars, screens(DAYS[0], low=90.0), max_risk_pct=13.0)  # 12.65% risk
    assert out["events"]["actionable_plans"] == 1
    assert out["events"]["orders_filled"] == 1


def test_lower_min_score_sizes_developing_band():
    bars = BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]]
    good = run(bars, screens(DAYS[0], score=72.0))["open_positions"][0]["qty"]
    dev = run(bars, screens(DAYS[0], score=65.0), min_score=65.0, developing_multiplier=0.375)
    assert dev["open_positions"][0]["qty"] == pytest.approx(good / 2, rel=0.02)
    below = run(bars, screens(DAYS[0], score=63.0), min_score=65.0)
    assert below["events"]["actionable_plans"] == 0


def test_higher_min_score_rejects_good_band():
    bars = BASE + [bar(d, 103, 104, 102, 103) for d in DAYS[3:]]
    assert (
        run(bars, screens(DAYS[0], score=75.0), min_score=80.0)["events"]["actionable_plans"] == 0
    )


def test_planner_rules_are_restored():
    before = (
        PLANNER.MAX_RISK_PCT,
        PLANNER.PLAN_BANDS,
        PLANNER.get_rating_band,
        PLANNER.get_sizing_multiplier,
    )
    with engine.planner_rules(PLANNER, engine.SimParams(max_risk_pct=12, min_score=60)):
        assert PLANNER.MAX_RISK_PCT == 12 and "developing" in PLANNER.PLAN_BANDS
    after = (
        PLANNER.MAX_RISK_PCT,
        PLANNER.PLAN_BANDS,
        PLANNER.get_rating_band,
        PLANNER.get_sizing_multiplier,
    )
    assert before == after
    with (
        pytest.raises(RuntimeError),
        engine.planner_rules(PLANNER, engine.SimParams(max_risk_pct=12)),
    ):
        raise RuntimeError("boom")
    assert PLANNER.MAX_RISK_PCT == 8.0


@pytest.mark.parametrize(
    "field, value",
    [
        ("max_risk_pct", 0),
        ("max_risk_pct", 40),
        ("min_score", 55),
        ("developing_multiplier", 2),
        ("max_hold_sessions", 0),
    ],
)
def test_validate_params(field, value):
    with pytest.raises(ValueError):
        engine.validate_params(engine.SimParams(**{field: value}))
