"""Performance metrics for a VCP backtest run (pure functions)."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date

TRADING_DAYS = 252


def _r(value, digits=2):
    return None if value is None else round(value, digits)


def curve_metrics(points: list) -> dict:
    """Return, CAGR, max drawdown, volatility and Sharpe of a [(date, value)] series."""
    if len(points) < 2:
        return {
            "start_value": None,
            "end_value": None,
            "total_return_pct": None,
            "cagr_pct": None,
            "max_drawdown_pct": None,
            "max_drawdown_peak": None,
            "max_drawdown_trough": None,
            "volatility_pct": None,
            "sharpe": None,
        }
    values = [v for _, v in points]
    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]
    years = (date.fromisoformat(points[-1][0]) - date.fromisoformat(points[0][0])).days / 365.25
    total = values[-1] / values[0] - 1
    peak, peak_day, worst, worst_peak, worst_day = values[0], points[0][0], 0.0, None, None
    for day, value in points:
        if value > peak:
            peak, peak_day = value, day
        dd = value / peak - 1
        if dd < worst:
            worst, worst_peak, worst_day = dd, peak_day, day
    vol = statistics.pstdev(rets) * math.sqrt(TRADING_DAYS) if len(rets) > 1 else None
    mean = statistics.mean(rets) if rets else 0.0
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    return {
        "start_value": _r(values[0]),
        "end_value": _r(values[-1]),
        "total_return_pct": _r(total * 100),
        "cagr_pct": _r(((1 + total) ** (1 / years) - 1) * 100)
        if years > 0 and total > -1
        else None,
        "max_drawdown_pct": _r(worst * 100),
        "max_drawdown_peak": worst_peak,
        "max_drawdown_trough": worst_day,
        "volatility_pct": _r(vol * 100) if vol is not None else None,
        "sharpe": _r(mean / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None,
    }


def trade_stats(trades: list) -> dict:
    n = len(trades)
    if not n:
        return {
            "trades": 0,
            "win_rate": None,
            "avg_win_pct": None,
            "avg_loss_pct": None,
            "profit_factor": None,
            "expectancy_inr": None,
            "avg_r": None,
            "median_r": None,
            "total_net_pnl": 0.0,
            "total_charges": 0.0,
            "charges_pct_of_gross_profit": None,
            "avg_sessions_held": None,
            "exit_reasons": {},
        }
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    gross_profit = sum(t["gross_pnl"] for t in trades if t["gross_pnl"] > 0)
    charges = sum(t["charges"] for t in trades)
    reasons: dict = defaultdict(int)
    for t in trades:
        reasons[t["exit_reason"]] += 1
    return {
        "trades": n,
        "win_rate": _r(len(wins) / n, 4),
        "avg_win_pct": _r(statistics.mean(t["net_pnl_pct"] for t in wins)) if wins else None,
        "avg_loss_pct": _r(statistics.mean(t["net_pnl_pct"] for t in losses)) if losses else None,
        "profit_factor": _r(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy_inr": _r(sum(t["net_pnl"] for t in trades) / n),
        "avg_r": _r(statistics.mean(rs), 3) if rs else None,
        "median_r": _r(statistics.median(rs), 3) if rs else None,
        "total_net_pnl": _r(sum(t["net_pnl"] for t in trades)),
        "total_charges": _r(charges),
        "charges_pct_of_gross_profit": _r(charges / gross_profit * 100)
        if gross_profit > 0
        else None,
        "avg_sessions_held": _r(statistics.mean(t["sessions_held"] for t in trades), 1),
        "exit_reasons": dict(sorted(reasons.items())),
    }


def grouped(trades: list, key) -> dict:
    groups: dict = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: trade_stats(v) for k, v in sorted(groups.items())}


def summarize_run(run: dict, benchmark_points: list, benchmark_name: str) -> dict:
    curve = [(p["date"], p["equity"]) for p in run["curve"]]
    strategy = curve_metrics(curve)
    bench = curve_metrics(benchmark_points)
    exposure = [p["invested"] / p["equity"] for p in run["curve"] if p["equity"] > 0]
    trades = run["trades"]
    half = None
    if curve:
        mid = curve[len(curve) // 2][0]
        first = [p for p in curve if p[0] <= mid]
        second = [p for p in curve if p[0] >= mid]
        bfirst = [p for p in benchmark_points if p[0] <= mid]
        bsecond = [p for p in benchmark_points if p[0] >= mid]
        half = {
            "split_date": mid,
            "first": {
                "strategy": curve_metrics(first),
                "benchmark": curve_metrics(bfirst),
                "trades": trade_stats([t for t in trades if t["entry_session"] <= mid]),
            },
            "second": {
                "strategy": curve_metrics(second),
                "benchmark": curve_metrics(bsecond),
                "trades": trade_stats([t for t in trades if t["entry_session"] > mid]),
            },
        }
    years = sorted({p[0][:4] for p in curve})
    by_year = {}
    for y in years:
        pts = [p for p in curve if p[0][:4] == y]
        bpts = [p for p in benchmark_points if p[0][:4] == y]
        prev = [p for p in curve if p[0][:4] < y]
        bprev = [p for p in benchmark_points if p[0][:4] < y]
        by_year[y] = {
            "strategy_return_pct": curve_metrics(prev[-1:] + pts)["total_return_pct"],
            "benchmark_return_pct": curve_metrics(bprev[-1:] + bpts)["total_return_pct"],
            "trades": trade_stats([t for t in trades if t["exit_session"][:4] == y])["trades"],
        }
    return {
        "strategy": strategy,
        "benchmark": {"name": benchmark_name, **bench},
        "excess_return_pct": (
            _r(strategy["total_return_pct"] - bench["total_return_pct"])
            if strategy["total_return_pct"] is not None and bench["total_return_pct"] is not None
            else None
        ),
        "avg_exposure_pct": _r(statistics.mean(exposure) * 100) if exposure else None,
        "max_positions": max((p["positions"] for p in run["curve"]), default=0),
        "trade_stats": trade_stats(trades),
        "by_rating_band": grouped(trades, lambda t: t.get("rating_band") or "unknown"),
        "by_regime_at_entry": grouped(trades, lambda t: t.get("regime_at_entry") or "unknown"),
        "by_year": by_year,
        "halves": half,
        "open_positions": len(run["open_positions"]),
        "final_valuation": run.get("final_valuation"),
        "events": run["events"],
    }
