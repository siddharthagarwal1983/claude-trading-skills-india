"""Phase 1.1 integrity corrections (see docs/research/PHASE1_1A_BACKTESTER_FIX_REPORT.md).

F2  LIMIT entries exit on the entry session when its low reaches the stop.
F3  Open positions are valued net of estimated selling charges.
F4  Optional historical regime gate (no_gate keeps the old behaviour).
F7b Screens see prices and volumes as quoted on the screening date.
A1  Optional conservative fill for limit orders the price only touched.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

import _india_data as india
import pytest
import swing_backtest_engine as engine
from swing_backtest_siblings import load, load_all

SCREENER, PLANNER, PAPER = load_all()
MB = load("momentum")
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


def signal(day=DAYS[0], ref=100.0, stop=95.0, score=85, symbol="ACME"):
    return {
        "symbol": symbol,
        "date": day,
        "state": "ACTIONABLE_DAY1",
        "rating": "A",
        "setup_score": score,
        "entry_reference": ref,
        "stop_reference": stop,
        "risk_pct_to_stop": 5.0,
        "primary_trigger": "4pct_breakout",
        "soft_failure_tags": [],
        "day_gain_pct": 5.0,
        "turnover_cr": 10.0,
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


def market(stocks: dict, bench_days=DAYS):
    bench = engine.Series([bar(d, 1000, 1000, 1000, 1000) for d in bench_days])
    rows = [{"symbol": s, "company": f"{s} Ltd", "industry": f"Sector {s}"} for s in stocks]
    series = {s: engine.Series(b) for s, b in stocks.items()}
    return engine.MarketData(rows, series, bench, "NIFTY 500")


def simulate(stocks, screens, strategy="momentum-burst", regime=None, **params):
    params.setdefault("slippage_bps", 10.0)
    p = engine.SimParams.for_strategy(strategy, **params)
    sim = engine.Simulator(market(stocks), screens, p, PLANNER, PAPER, regime=regime)
    return sim.run(DAYS)


def mb_screens(*results_by_day):
    return {day: {"date": day, "results": results} for day, results in results_by_day}


TRIGGER = bar(DAYS[0], 95.5, 100.5, 95, 100)  # signal close 100 -> limit 102.00, stop 95.00
FLAT = [bar(d, 96, 97, 95.5, 96) for d in DAYS[2:]]  # stays above the stop afterwards


def one(stock_bars, **params):
    return simulate({"ACME": stock_bars}, mb_screens((DAYS[0], [signal()])), **params)


# --------------------------------------------------------------------------- F2


def test_limit_fill_at_open_below_stop_exits_at_open_same_session():
    out = one([TRIGGER, bar(DAYS[1], 94, 96, 93, 95)] + FLAT)
    (trade,) = out["trades"]
    assert trade["entry_price"] == 94  # LIMIT 102 filled at the open (no buy slippage)
    assert trade["entry_basis"] == "open"
    assert trade["exit_session"] == trade["entry_session"] == DAYS[1]
    assert trade["exit_reason"] == "stop_hit_entry_session"
    assert trade["exit_price"] == pytest.approx(93.91)  # the open less 10 bps
    assert trade["sessions_held"] == 0
    assert out["open_positions"] == []
    assert out["events"]["entry_session_stops"] == 1


def test_limit_fill_at_open_above_stop_then_breach_exits_at_stop():
    out = one([TRIGGER, bar(DAYS[1], 101, 102, 94, 96)] + FLAT)
    (trade,) = out["trades"]
    assert trade["entry_price"] == 101 and trade["entry_basis"] == "open"
    assert trade["exit_reason"] == "stop_hit_entry_session"
    assert trade["exit_price"] == pytest.approx(94.91)  # 95.00 less 10 bps


def test_limit_fill_on_the_way_down_then_breach_exits_at_stop():
    out = one([TRIGGER, bar(DAYS[1], 103, 104, 94, 96)] + FLAT)
    (trade,) = out["trades"]
    assert trade["entry_price"] == 102.0 and trade["entry_basis"] == "limit"
    assert trade["exit_reason"] == "stop_hit_entry_session"
    assert trade["exit_price"] == pytest.approx(94.91)
    assert trade["exit_session"] == DAYS[1]


def test_limit_fill_without_breach_keeps_position():
    out = one([TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT, max_hold_sessions=3)
    (trade,) = out["trades"]
    assert trade["exit_reason"] == "time_stop" and trade["sessions_held"] == 3
    assert trade["entry_day_stop_touched"] is False
    assert out["events"]["entry_session_stops"] == 0


def test_same_session_stop_charges_and_cash():
    out = one([TRIGGER, bar(DAYS[1], 101, 102, 94, 96)] + FLAT)
    (trade,) = out["trades"]
    qty = trade["qty"]
    buy = PAPER.leg_charges(101 * qty, buy=True)
    sell = PAPER.leg_charges(94.91 * qty, buy=False)
    assert sell["dp_charge"] == pytest.approx(15.93)
    assert trade["charges"] == pytest.approx(buy["total"] + sell["total"], abs=0.02)
    assert trade["net_pnl"] == pytest.approx(
        (94.91 - 101) * qty - buy["total"] - sell["total"], abs=0.05
    )
    assert out["curve"][-1]["equity"] == pytest.approx(1_000_000 + trade["net_pnl"], abs=0.05)
    assert out["curve"][1]["positions"] == 0  # flat at the entry session's close


def test_same_session_stop_never_duplicates_or_goes_negative():
    later = [bar(d, 94, 95, 90, 92) for d in DAYS[2:]]  # keeps trading below the stop
    out = one([TRIGGER, bar(DAYS[1], 101, 102, 94, 96)] + later)
    assert len(out["trades"]) == 1
    assert out["open_positions"] == []
    assert all(row["positions"] >= 0 for row in out["curve"])
    assert out["events"]["orders_filled"] == 1


def test_entry_session_stop_off_restores_old_behaviour():
    out = one([TRIGGER, bar(DAYS[1], 101, 102, 94, 96)] + FLAT, entry_session_stop=False)
    (trade,) = out["trades"]
    assert trade["exit_session"] != DAYS[1]
    assert trade["exit_reason"] == "time_stop"  # later bars stay above the stop
    assert trade["entry_day_stop_touched"] is True
    assert out["events"]["entry_session_stops"] == 0


def test_stop_limit_entry_keeps_documented_next_session_policy():
    """VCP buy-stops: a daily bar cannot show whether the low came before the fill."""
    bars = (
        [
            bar(DAYS[0], 98, 99.5, 97.5, 99),
            bar(DAYS[1], 99.5, 101, 94, 95),  # triggers at 100.10, low under the 95.03 stop
            bar(DAYS[2], 95.2, 96, 94.5, 95.5),  # stop hit the next session
        ]
        + [bar(d, 95, 96, 94, 95) for d in DAYS[3:]]
    )
    screens = {DAYS[0]: {"date": DAYS[0], "results": [candidate()]}}
    out = simulate({"ACME": bars}, screens, strategy="vcp")
    (trade,) = out["trades"]
    assert trade["entry_session"] == DAYS[1] and trade["exit_session"] == DAYS[2]
    assert trade["exit_reason"] == "stop_hit"
    assert trade["entry_day_stop_touched"] is True
    assert out["events"]["entry_session_stop_touch_kept"] == 1
    assert out["events"]["entry_session_stops"] == 0


# --------------------------------------------------------------------------- F3


def held_bars(close=104.0):
    return [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + [
        bar(d, close, close + 1, close - 1, close) for d in DAYS[2:]
    ]


def test_final_equity_deducts_estimated_selling_charges():
    out = one(held_bars(), max_hold_sessions=20)
    (pos,) = out["open_positions"]
    qty, last = pos["qty"], out["curve"][-1]
    market_value = qty * 104.0
    sell = PAPER.leg_charges(market_value, buy=False)["total"]
    assert pos["market_value"] == pytest.approx(market_value)
    assert pos["est_sell_charges"] == pytest.approx(sell, abs=0.01)
    assert pos["net_liquidation_value"] == pytest.approx(market_value - sell, abs=0.01)
    assert last["market_value"] == pytest.approx(market_value)
    assert last["est_liquidation_costs"] == pytest.approx(sell, abs=0.01)
    assert last["equity"] == pytest.approx(last["cash"] + market_value - sell, abs=0.02)
    gross = one(held_bars(), max_hold_sessions=20, liquidation_valuation="gross")
    assert gross["curve"][-1]["equity"] == pytest.approx(last["cash"] + market_value, abs=0.02)
    assert gross["curve"][-1]["est_liquidation_costs"] == 0


def test_open_position_net_unrealized_includes_both_legs():
    out = one(held_bars(), max_hold_sessions=20)
    (pos,) = out["open_positions"]
    qty = pos["qty"]
    buy = PAPER.leg_charges(101 * qty, buy=True)["total"]
    sell = PAPER.leg_charges(104 * qty, buy=False)["total"]
    assert pos["unrealized"] == pytest.approx((104 - 101) * qty, abs=0.01)  # gross, as before
    assert pos["buy_charges"] == pytest.approx(buy, abs=0.01)
    assert pos["unrealized_net"] == pytest.approx((104 - 101) * qty - buy - sell, abs=0.02)


def test_multiple_open_positions_each_pay_a_dp_charge():
    stocks = {
        "ACME": held_bars(104.0),
        "BETA": [bar(DAYS[0], 190, 201, 190, 200), bar(DAYS[1], 202, 205, 201, 204)]
        + [bar(d, 210, 211, 209, 210) for d in DAYS[2:]],
    }
    screens = mb_screens(
        (DAYS[0], [signal(), signal(symbol="BETA", ref=200.0, stop=190.0)]),
    )
    out = simulate(stocks, screens, max_hold_sessions=20)
    assert len(out["open_positions"]) == 2
    expected = sum(
        PAPER.leg_charges(p["market_value"], buy=False)["total"] for p in out["open_positions"]
    )
    assert out["curve"][-1]["est_liquidation_costs"] == pytest.approx(expected, abs=0.02)
    assert expected > 2 * 15.93
    final = out["final_valuation"]
    assert final["mode"] == "net"
    assert final["net_liquidation_value"] == pytest.approx(
        final["market_value"] - final["est_liquidation_costs"], abs=0.01
    )


def test_closed_trade_pnl_unchanged_by_valuation_mode():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    net = one(bars, max_hold_sessions=3)["trades"]
    gross = one(bars, max_hold_sessions=3, liquidation_valuation="gross")["trades"]
    assert net == gross


# --------------------------------------------------------------------------- F4


def test_gate_blocks_new_entries():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    regime = {d: "REDUCE_ONLY" for d in DAYS}
    out = simulate(
        {"ACME": bars},
        mb_screens((DAYS[0], [signal()])),
        regime=regime,
        regime_mode="exposure_file",
    )
    assert out["trades"] == [] and out["open_positions"] == []
    assert out["events"]["regime_blocked_sessions"] >= 1
    assert out["events"]["actionable_plans"] == 0


def test_gate_does_not_block_exits():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102), bar(DAYS[2], 99, 99.5, 94, 95)] + FLAT[1:]
    regime = {DAYS[0]: "NEW_ENTRY_ALLOWED", **{d: "CASH_PRIORITY" for d in DAYS[1:]}}
    out = simulate(
        {"ACME": bars},
        mb_screens((DAYS[0], [signal()])),
        regime=regime,
        regime_mode="exposure_file",
    )
    (trade,) = out["trades"]
    assert trade["exit_reason"] == "stop_hit" and trade["exit_session"] == DAYS[2]


def test_gate_uses_the_signal_session_regime():
    """D's posture (known after D's close) governs orders planned on D for D+1."""
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    second = [bar(DAYS[0], 190, 201, 190, 200), bar(DAYS[1], 196, 201, 195, 200)] + [
        bar(d, 201, 202, 200, 201) for d in DAYS[2:]
    ]
    screens = mb_screens(
        (DAYS[0], [signal()]),
        (DAYS[1], [signal(day=DAYS[1], symbol="BETA", ref=200.0, stop=195.0)]),
    )
    regime = {DAYS[0]: "NEW_ENTRY_ALLOWED", DAYS[1]: "REDUCE_ONLY"}
    out = simulate(
        {"ACME": bars, "BETA": second},
        screens,
        regime=regime,
        regime_mode="exposure_file",
        max_hold_sessions=20,
    )
    held = {p["symbol"] for p in out["open_positions"]}
    assert held == {"ACME"}  # planned on D0 (allowed), filled on D1 (blocked day)
    assert out["events"]["regime_blocked_sessions"] == 1  # D1's BETA signal not planned
    assert out["open_positions"][0]["regime_action_at_signal"] == "NEW_ENTRY_ALLOWED"


def test_unknown_regime_blocks_like_missing_critical_inputs():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    out = simulate(
        {"ACME": bars}, mb_screens((DAYS[0], [signal()])), regime={}, regime_mode="sma200_proxy"
    )
    assert out["trades"] == [] and out["events"]["regime_blocked_sessions"] == 1


def test_gated_mode_requires_a_regime_map():
    p = engine.SimParams.for_strategy("momentum-burst", regime_mode="sma200_proxy")
    with pytest.raises(ValueError, match="regime"):
        engine.Simulator(market({"ACME": [TRIGGER]}), {}, p, PLANNER, PAPER)
    with pytest.raises(ValueError, match="regime_mode"):
        engine.validate_params(engine.SimParams(regime_mode="vibes"))


def test_no_gate_ignores_any_regime_map():
    bars = [TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT
    base = one(bars, max_hold_sessions=3)
    ignored = simulate(
        {"ACME": bars},
        mb_screens((DAYS[0], [signal()])),
        regime={d: "CASH_PRIORITY" for d in DAYS},
        max_hold_sessions=3,
    )
    assert ignored["trades"] == base["trades"]


def test_sma200_proxy_actions():
    days = [d.isoformat() for d in weekdays(date(2025, 1, 1), date(2026, 3, 31))]
    closes = [1000 + i for i in range(len(days))]
    closes[-1] = 900  # last close drops under the 200-day average
    bench = engine.Series([bar(d, c, c, c, c) for d, c in zip(days, closes)])
    actions = engine.regime_actions("sma200_proxy", bench, days)
    assert actions[days[198]] is None  # fewer than 200 closes
    assert actions[days[199]] == "NEW_ENTRY_ALLOWED"
    assert actions[days[-1]] == "REDUCE_ONLY"
    assert engine.regime_actions("no_gate", bench, days) is None


def test_regime_file_loader(tmp_path):
    path = tmp_path / "regime.json"
    path.write_text(
        json.dumps(
            {
                "rows": [
                    {"date": DAYS[0], "action": "NEW_ENTRY_ALLOWED"},
                    {"date": DAYS[1], "action": "CASH_PRIORITY"},
                ]
            }
        )
    )
    assert engine.load_regime_file(path) == {
        DAYS[0]: "NEW_ENTRY_ALLOWED",
        DAYS[1]: "CASH_PRIORITY",
    }
    path.write_text(json.dumps({DAYS[0]: "MAYBE"}))
    with pytest.raises(ValueError, match="action"):
        engine.load_regime_file(path)


def test_market_gate_mapping_follows_exposure_coach_skill():
    assert engine.market_gate_for("NEW_ENTRY_ALLOWED") == "allowed"
    assert engine.market_gate_for("REDUCE_ONLY") == "restrictive"
    assert engine.market_gate_for("CASH_PRIORITY") == "restrictive"
    assert engine.market_gate_for(None) == "restrictive"


class GateRecorder:
    """Stands in for the momentum screener and records the market gate per session."""

    def __init__(self):
        self.gates = {}

    @staticmethod
    def normalize_bars(bars):
        return bars

    def analyze_symbol(self, symbol, bars, args):
        self.gates[args._expected_session.isoformat()] = args.market_gate
        return {"symbol": symbol, "state": "REJECTED", "date": bars[-1]["date"]}

    @staticmethod
    def sort_results(results):
        return results


def test_momentum_market_gate_is_session_specific(tmp_path):
    stock = [bar(d, 100, 101, 99, 100) for d in DAYS]
    rec = GateRecorder()
    args = argparse.Namespace(history_days=80, market_gate="neutral")
    gates = {DAYS[0]: "allowed", DAYS[1]: "restrictive", DAYS[2]: "allowed"}
    engine.screen_range(
        DAYS[:3],
        market({"ACME": stock}),
        rec,
        args,
        20,
        engine.ScreenCache(tmp_path / "s.jsonl"),
        strategy="momentum-burst",
        gates=gates,
    )
    assert rec.gates == gates
    assert args.market_gate == "neutral"  # restored after screening


def test_screen_key_tracks_gates_and_price_mode():
    data = market({"ACME": [bar(d, 1, 1, 1, 1) for d in DAYS]})
    ns = argparse.Namespace(x=1)
    legacy = engine.screen_key("u", "b", ns, 20, data, "momentum-burst")
    assert legacy == engine.screen_key("u", "b", ns, 20, data, "momentum-burst", extra=None)
    pit = engine.screen_key(
        "u", "b", ns, 20, data, "momentum-burst", extra={"screen_prices": "point_in_time"}
    )
    gated = engine.screen_key(
        "u",
        "b",
        ns,
        20,
        data,
        "momentum-burst",
        extra={"screen_prices": "point_in_time", "gates": {DAYS[0]: "allowed"}},
    )
    assert len({legacy, pit, gated}) == 3


# --------------------------------------------------------------------------- F7b


SPLIT_DAY = "2026-03-20"  # 1:10 split after every screening day used below
SPLIT = [{"ex_date": SPLIT_DAY, "action_type": "SPLIT", "purpose": "Split", "factor": 0.1}]


def split_series(raw):
    adjusted = india.adjust_bars(raw, SPLIT)
    return engine.Series(adjusted, [], SPLIT)


class BarRecorder:
    MIN_HISTORY_BARS = 1

    def __init__(self):
        self.bars = {}

    @staticmethod
    def to_history(bars):
        return list(reversed(bars))

    def prepare_history(self, sym, bars, warnings, day):
        self.bars[(sym, day)] = bars
        return list(reversed(bars)), None

    def run_funnel(self, rows, histories, bench, args, outcomes):
        return [], [], []


def test_point_in_time_screen_sees_quoted_prices_and_volumes():
    raw = [bar(d, 300, 305, 295, 300, volume=60_000) for d in DAYS]
    data = market({"ACME": raw})
    data.stocks["ACME"] = split_series(raw)
    assert data.stocks["ACME"].bars[0]["close"] == pytest.approx(30.0)  # back-adjusted
    rec = BarRecorder()
    pit = engine.screen_day(DAYS[3], data, rec, argparse.Namespace(), top=20)
    seen = rec.bars[("ACME", DAYS[3])]
    assert [b["close"] for b in seen] == pytest.approx([300.0] * 4)
    assert [b["volume"] for b in seen] == [60_000] * 4
    assert pit["pit_factors"] == {"ACME": 0.1}
    engine.screen_day(DAYS[3], data, rec, argparse.Namespace(), top=20, pit=False)
    assert rec.bars[("ACME", DAYS[3])][0]["close"] == pytest.approx(30.0)


def test_actions_on_or_before_the_screening_day_still_apply():
    raw = [bar(d, 300, 305, 295, 300) for d in DAYS[:3]] + [
        bar(d, 30, 30.5, 29.5, 30) for d in DAYS[3:]
    ]
    actions = [{"ex_date": DAYS[3], "action_type": "SPLIT", "factor": 0.1}]
    series = engine.Series(india.adjust_bars(raw, actions), [], actions)
    data = market({"ACME": raw})
    data.stocks["ACME"] = series
    rec = BarRecorder()
    out = engine.screen_day(DAYS[4], data, rec, argparse.Namespace(), top=20)
    assert [b["close"] for b in rec.bars[("ACME", DAYS[4])]] == pytest.approx([30.0] * 5)
    assert out["pit_factors"] == {}


def momentum_series(day_count=70, base=600.0, last_volume=750_000, prev_volume=300_000):
    """Quiet base then a 5% burst; prices on the paisa grid so adjustment round-trips exactly."""
    days = weekdays(date(2025, 11, 1), date(2026, 3, 31))[-day_count:]
    rows = []
    for i, d in enumerate(days[:-1]):
        close = round(base * (1 + 0.002 * ((-1) ** i)), 2)
        rows.append(
            bar(
                d.isoformat(),
                close,
                round(close * 1.005, 2),
                round(close * 0.995, 2),
                close,
                volume=prev_volume,
            )
        )
    prev = rows[-1]["close"]
    close = round(prev * 1.05, 2)
    rows.append(
        bar(
            days[-1].isoformat(),
            round(prev * 1.001, 2),
            round(close * 1.003, 2),
            round(prev * 1.001 * 0.995, 2),
            close,
            volume=last_volume,
        )
    )
    return rows, days[-1]


def momentum_data(raw, actions):
    data = market({"ACME": raw}, bench_days=[b["date"] for b in raw])
    data.stocks["ACME"] = engine.Series(india.adjust_bars(raw, actions), [], actions)
    return data


def later_split(day):
    return [
        {"ex_date": (day + timedelta(days=7)).isoformat(), "action_type": "SPLIT", "factor": 0.1}
    ]


def test_fifty_rupee_minimum_uses_the_price_as_quoted():
    raw, last = momentum_series(base=300.0)  # ₹300 then, ₹30 after a later 1:10 split
    data = momentum_data(raw, later_split(last))
    args = MB.parse_arguments(["--symbols", "ACME"])
    pit = engine.screen_day_momentum(last.isoformat(), data, MB, args)
    back = engine.screen_day_momentum(last.isoformat(), data, MB, args, pit=False)
    assert [r["symbol"] for r in pit["results"]] == ["ACME"]
    assert back["results"] == []  # ₹30.x close fails the ₹50 minimum in hindsight


def test_share_volume_minimum_uses_volume_as_quoted():
    raw, last = momentum_series(base=1000.0, last_volume=60_000, prev_volume=50_000)
    data = momentum_data(raw, later_split(last))
    args = MB.parse_arguments(["--symbols", "ACME"])
    window = data.stocks["ACME"].window("2025-01-01", last.isoformat())
    factor = engine.future_factor(data.stocks["ACME"], last.isoformat())
    assert factor == pytest.approx(0.1)
    quoted = MB.detect_triggers(
        MB.normalize_bars(engine.point_in_time_bars(window, factor)), args
    ).trigger_tags
    hindsight = MB.detect_triggers(MB.normalize_bars(window), args).trigger_tags
    assert "4pct_breakout" not in quoted  # 60,000 shares < the 1 lakh minimum
    assert "4pct_breakout" in hindsight  # 600,000 back-adjusted shares pass


def test_rupee_breakout_uses_the_price_as_quoted():
    days = weekdays(date(2025, 11, 1), date(2026, 3, 31))[-30:]
    raw = [bar(d.isoformat(), 1000, 1004, 996, 1000, volume=300_000) for d in days[:-1]]
    # +₹30 open-to-close (3%), +2% on the day: a rupee breakout only at the quoted price
    raw.append(bar(days[-1].isoformat(), 990, 1021, 989, 1020, volume=200_000))
    data = momentum_data(raw, later_split(days[-1]))
    args = MB.parse_arguments(["--symbols", "ACME"])
    series = data.stocks["ACME"]
    window = series.window("2025-01-01", days[-1].isoformat())
    factor = engine.future_factor(series, days[-1].isoformat())
    quoted = MB.detect_triggers(MB.normalize_bars(engine.point_in_time_bars(window, factor)), args)
    hindsight = MB.detect_triggers(MB.normalize_bars(window), args)
    assert "rupee_breakout" in quoted.trigger_tags
    assert "rupee_breakout" not in hindsight.trigger_tags  # ₹3 move after the 1:10 adjustment


def test_screen_is_invariant_to_a_future_corporate_action():
    raw, last = momentum_series(base=300.0)
    args = MB.parse_arguments(["--symbols", "ACME"])
    without = engine.screen_day_momentum(last.isoformat(), momentum_data(raw, []), MB, args)
    with_split = engine.screen_day_momentum(
        last.isoformat(), momentum_data(raw, later_split(last)), MB, args
    )
    assert without["results"] == with_split["results"]
    assert without["results"], "fixture should produce a burst"


def test_momentum_tick_rounding_uses_the_quoted_price_band():
    # Quoted ₹333.33 (tick ₹0.05) -> limit 339.95, stop 320.00. A later 1:10 split means the
    # simulation trades in today's units: limit 33.995, stop 32.00.
    adjusted = (
        [
            bar(DAYS[0], 33.0, 33.4, 32.0, 33.333),
            bar(DAYS[1], 34.0, 34.5, 33.99, 34.2),  # opens above the limit, dips to 33.99
        ]
        + [bar(d, 34.1, 34.3, 34.0, 34.2) for d in DAYS[2:]]
    )
    screens = mb_screens((DAYS[0], [signal(ref=333.33, stop=320.0)]))
    screens[DAYS[0]]["pit_factors"] = {"ACME": 0.1}
    out = simulate({"ACME": adjusted}, screens, max_hold_sessions=20)
    (pos,) = out["open_positions"]
    assert pos["entry_price"] == pytest.approx(33.995)  # 339.95 x 0.1, not 33.99
    assert pos["stop"] == pytest.approx(32.0)
    # After the split the stock is quoted in today's units: the ₹0.01 band applies.
    plain = mb_screens((DAYS[0], [signal(ref=33.333, stop=32.0)]))
    out = simulate({"ACME": adjusted}, plain, max_hold_sessions=20)
    assert out["open_positions"][0]["entry_price"] == pytest.approx(33.99)


def test_vcp_plan_prices_and_shares_convert_from_quoted_units():
    # Quoted pivot ₹1,000 / low ₹960 (tick ₹0.10): stop 950.40, a tenth after a later split.
    bars = [
        bar(DAYS[0], 98, 99.5, 97.5, 99),
        bar(DAYS[1], 99.5, 101, 99, 100.8),
    ] + [bar(d, 101, 104, 100, 103) for d in DAYS[2:]]
    screens = {
        DAYS[0]: {
            "date": DAYS[0],
            "results": [candidate(pivot=1000.0, low=960.0, price=990.0)],
            "pit_factors": {"ACME": 0.1},
        }
    }
    out = simulate({"ACME": bars}, screens, strategy="vcp")
    (pos,) = out["open_positions"]
    assert pos["stop"] == pytest.approx(95.04)  # 950.40 x 0.1 (back-adjusted rounding: 95.03)
    plain = simulate(
        {"ACME": bars}, {DAYS[0]: {"date": DAYS[0], "results": [candidate()]}}, strategy="vcp"
    )
    assert pos["qty"] == pytest.approx(plain["open_positions"][0]["qty"], rel=0.02)


# --------------------------------------------------------------------------- A1


def test_limit_touch_fill_is_counted_and_optionally_skipped():
    touch = [TRIGGER, bar(DAYS[1], 103, 104, 102.0, 103)] + FLAT  # low exactly at the limit
    out = one(touch, max_hold_sessions=3)
    assert out["events"]["limit_touch_only"] == 1 and len(out["trades"]) == 1
    strict = one(touch, max_hold_sessions=3, limit_fill="trade_through")
    assert strict["trades"] == [] and strict["open_positions"] == []
    assert strict["events"]["limit_touch_skipped"] == 1


def test_trade_through_fills_when_price_trades_a_tick_below():
    through = [TRIGGER, bar(DAYS[1], 103, 104, 101.99, 103)] + FLAT
    strict = one(through, max_hold_sessions=3, limit_fill="trade_through")
    assert len(strict["trades"]) == 1 and strict["events"]["limit_touch_only"] == 0


def test_open_fills_are_never_touch_fills():
    out = one([TRIGGER, bar(DAYS[1], 101, 103, 100.5, 102)] + FLAT, limit_fill="trade_through")
    assert len(out["trades"]) == 1 and out["events"]["limit_touch_only"] == 0


# --------------------------------------------------------------------------- params


@pytest.mark.parametrize(
    "field, value",
    [
        ("liquidation_valuation", "mark"),
        ("limit_fill", "maybe"),
        ("entry_session_stop", "yes"),
    ],
)
def test_validate_new_params(field, value):
    with pytest.raises(ValueError, match=field):
        engine.validate_params(engine.SimParams(**{field: value}))


@pytest.mark.parametrize(
    "name", ["entry_session_stop", "liquidation_valuation", "regime_mode", "limit_fill"]
)
def test_mode_params_cannot_be_swept(name):
    with pytest.raises(ValueError, match="cannot sweep"):
        engine.parse_sweep([f"{name}=1,2"])


# --------------------------------------------------------------------------- CLI


import math  # noqa: E402

import india_swing_backtest as cli  # noqa: E402

CLI_AS_OF = date(2026, 3, 31)


def cli_bars(n=300, start_price=200.0, drift=0.003, wiggle=0.012, phase=0.0, volume=1_000_000):
    days = weekdays(CLI_AS_OF - timedelta(days=n * 2), CLI_AS_OF)[-n:]
    out, price = [], start_price
    for i, day in enumerate(days):
        price *= 1 + drift + wiggle * math.sin(i / 3.0 + phase) * 0.5
        out.append(
            {
                "date": day.isoformat(),
                "open": round(price * 0.995, 2),
                "high": round(price * 1.012, 2),
                "low": round(price * 0.985, 2),
                "close": round(price, 2),
                "volume": volume,
            }
        )
    return out


CLI_STOCKS = {
    "LEADER": cli_bars(),
    "SECOND": cli_bars(start_price=300, drift=0.0025, phase=1.0),
    "THIRD": cli_bars(start_price=150, drift=0.0035, wiggle=0.02, phase=2.0),
}
CLI_BENCH = cli_bars(start_price=20000, drift=0.001, volume=0)


@pytest.fixture
def offline(monkeypatch):
    def fake_load(key, cache_root, **kwargs):
        raise india.IndiaDataError(f"offline: {key}")

    monkeypatch.setattr(india, "load_universe", fake_load)


def write_cache(root, bench=CLI_BENCH):
    for sym, rows in CLI_STOCKS.items():
        path = root / "stocks" / f"{sym}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bars": rows, "actions": [], "coverage_end": rows[-1]["date"]}))
    path = root / "indices" / "NIFTY_500.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bars": bench, "coverage_end": bench[-1]["date"]}))


def run_cli(tmp_path, *extra, bench=CLI_BENCH):
    root = tmp_path / "cache"
    write_cache(root, bench)
    out = tmp_path / "out"
    code = cli.main(
        [
            "--symbols",
            *sorted(CLI_STOCKS),
            "--no-fetch",
            "--cache-dir",
            str(root),
            "--data-start",
            "2025-01-01",
            "--end",
            CLI_AS_OF.isoformat(),
            "--output-dir",
            str(out),
            *extra,
        ],
        now_fn=lambda: __import__("datetime").datetime(2026, 4, 1, 12, tzinfo=india.IST),
    )
    report = None
    if code == 0:
        report = json.loads(next(out.glob("india_swing_backtest_*.json")).read_text())
    return code, report, out


def test_cli_defaults_record_the_corrected_modes(tmp_path, offline):
    code, report, out = run_cli(tmp_path)
    assert code == 0
    assert report["corrections"] == {
        "entry_session_stop": True,
        "liquidation_valuation": "net",
        "screen_prices": "point_in_time",
        "rs_alignment": "date",
        "limit_fill": "touch",
        "legacy": False,
    }
    assert report["regime"]["mode"] == "no_gate"
    assert report["base"]["summary"]["final_valuation"]["mode"] == "net"
    md = next(out.glob("*.md")).read_text()
    assert "## Simulation settings" in md and "Regime mode: `no_gate`" in md
    assert "Net liquidation value" in md


def test_cli_legacy_restores_every_earlier_mode(tmp_path, offline):
    code, report, _ = run_cli(tmp_path, "--legacy")
    assert code == 0
    assert report["corrections"] == {
        "entry_session_stop": False,
        "liquidation_valuation": "gross",
        "screen_prices": "back_adjusted",
        "rs_alignment": "offset",
        "limit_fill": "touch",
        "legacy": True,
    }
    assert "--rs-alignment offset" in report["screener_settings"]
    params = report["base"]["params"]
    assert (params["entry_session_stop"], params["liquidation_valuation"]) == (False, "gross")


def test_cli_explicit_flag_beats_legacy(tmp_path, offline):
    code, report, _ = run_cli(tmp_path, "--legacy", "--liquidation-valuation", "net")
    assert code == 0 and report["corrections"]["liquidation_valuation"] == "net"


def test_cli_report_identifies_a_proxy_regime(tmp_path, offline):
    code, report, out = run_cli(tmp_path, "--regime-mode", "sma200_proxy")
    assert code == 0
    regime = report["regime"]
    assert regime["mode"] == "sma200_proxy" and "PROXY" in regime["description"]
    assert sum(regime["sessions"].values()) == report["window"]["sessions"]
    assert set(regime["sessions"]) <= {"NEW_ENTRY_ALLOWED", "REDUCE_ONLY", "unknown"}
    assert "not the india-exposure-coach" in next(out.glob("*.md")).read_text()


def test_cli_regime_file_required_and_used(tmp_path, offline, capsys):
    code, _, _ = run_cli(tmp_path, "--regime-mode", "exposure_file")
    assert code == 1 and "--regime-file" in capsys.readouterr().err
    path = tmp_path / "regime.json"
    path.write_text(json.dumps({b["date"]: "CASH_PRIORITY" for b in CLI_BENCH}))
    code, report, _ = run_cli(
        tmp_path, "--regime-mode", "exposure_file", "--regime-file", str(path)
    )
    assert code == 0
    assert report["base"]["summary"]["trade_stats"]["trades"] == 0
    assert report["base"]["summary"]["events"]["actionable_plans"] == 0
    assert report["regime"]["sessions"] == {"CASH_PRIORITY": report["window"]["sessions"]}


def test_cli_momentum_gate_follows_the_regime(tmp_path, offline):
    code, report, _ = run_cli(
        tmp_path, "--strategy", "momentum-burst", "--regime-mode", "sma200_proxy"
    )
    assert code == 0
    assert report["regime"]["momentum_market_gate"] == "per session (allowed / restrictive)"
    code, plain, _ = run_cli(tmp_path / "plain", "--strategy", "momentum-burst")
    assert plain["regime"]["momentum_market_gate"] == "neutral (constant, screener default)"


def test_calendar_audit_lists_benchmark_gaps_and_muhurat(tmp_path, offline):
    gap = "2025-06-10"
    bench = [b for b in CLI_BENCH if b["date"] != gap]
    code, report, out = run_cli(tmp_path, bench=bench)
    assert code == 0
    cal = report["calendar"]
    assert cal["benchmark_missing_sessions"] == [gap]
    assert cal["muhurat_policy"] == "include"
    assert "2025-10-21" in cal["muhurat_sessions_in_data"]
    assert cal["muhurat_sessions_in_window"] == []
    assert set(cal["muhurat_trades"]) == {"entries", "exits"}
    md = next(out.glob("*.md")).read_text()
    assert gap in md and "Muhurat" in md


def test_calendar_audit_function():
    days = ["2026-03-02", "2026-03-03", "2026-03-04"]
    stocks = {s: [bar(d, 1, 1, 1, 1) for d in days] for s in ("A", "B", "C")}
    data = market(stocks, bench_days=[days[0], days[2]])
    audit = cli.calendar_audit(data, "2026-03-01", "2026-03-31", sessions=[days[0], days[2]])
    assert audit["benchmark_missing_sessions"] == [days[1]]
    assert audit["muhurat_sessions_in_data"] == []
