"""Metric calculations."""

import pytest
import vcp_backtest_metrics as m


def test_curve_metrics_return_and_drawdown():
    pts = [
        ("2025-01-01", 100.0),
        ("2025-07-01", 120.0),
        ("2025-10-01", 90.0),
        ("2026-01-01", 110.0),
    ]
    out = m.curve_metrics(pts)
    assert out["total_return_pct"] == 10.0
    assert out["max_drawdown_pct"] == -25.0
    assert (out["max_drawdown_peak"], out["max_drawdown_trough"]) == ("2025-07-01", "2025-10-01")
    assert out["cagr_pct"] == pytest.approx(10.0, abs=0.1)
    assert out["sharpe"] is not None


def test_curve_metrics_short_series():
    assert m.curve_metrics([("2025-01-01", 1.0)])["total_return_pct"] is None


def trade(
    net,
    r,
    reason="stop_hit",
    gross=None,
    charges=10.0,
    entry="2025-01-02",
    exit_="2025-02-02",
    **kw,
):
    return {
        "net_pnl": net,
        "net_pnl_pct": net / 100,
        "r_multiple": r,
        "exit_reason": reason,
        "gross_pnl": gross if gross is not None else net + charges,
        "charges": charges,
        "sessions_held": 5,
        "entry_session": entry,
        "exit_session": exit_,
        **kw,
    }


def test_trade_stats():
    t = [trade(200, 2.0, "target_hit"), trade(-100, -1.0), trade(-100, -1.0)]
    s = m.trade_stats(t)
    assert s["trades"] == 3 and s["win_rate"] == pytest.approx(0.3333)
    assert s["profit_factor"] == 1.0
    assert s["expectancy_inr"] == 0.0
    assert s["avg_r"] == 0.0 and s["median_r"] == -1.0
    assert s["exit_reasons"] == {"stop_hit": 2, "target_hit": 1}
    assert s["charges_pct_of_gross_profit"] == pytest.approx(30 / 210 * 100, abs=0.01)
    assert m.trade_stats([])["trades"] == 0


def test_summarize_run():
    curve = [
        {"date": d, "equity": e, "invested": i, "positions": p}
        for d, e, i, p in [
            ("2025-12-30", 100.0, 0, 0),
            ("2025-12-31", 101.0, 50, 1),
            ("2026-01-02", 99.0, 50, 1),
            ("2026-01-05", 103.0, 0, 0),
        ]
    ]
    run = {
        "curve": curve,
        "open_positions": [],
        "events": {"x": 1},
        "trades": [
            trade(
                4,
                2.0,
                "target_hit",
                rating_band="good",
                regime_at_entry="above_200dma",
                entry="2025-12-31",
                exit_="2026-01-05",
            )
        ],
    }
    bench = [(p["date"], v) for p, v in zip(curve, (1000, 1010, 1005, 1020))]
    s = m.summarize_run(run, bench, "NIFTY 500")
    assert s["strategy"]["total_return_pct"] == 3.0
    assert s["benchmark"]["total_return_pct"] == 2.0 and s["excess_return_pct"] == 1.0
    assert s["avg_exposure_pct"] == pytest.approx((0 + 50 / 101 + 50 / 99 + 0) / 4 * 100, abs=0.01)
    assert set(s["by_year"]) == {"2025", "2026"}
    assert s["by_year"]["2026"]["strategy_return_pct"] == pytest.approx(1.98, abs=0.01)
    assert s["by_rating_band"]["good"]["trades"] == 1
    assert s["halves"]["split_date"] == "2026-01-02"
