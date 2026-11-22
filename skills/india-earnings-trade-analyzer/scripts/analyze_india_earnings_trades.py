#!/usr/bin/env python3
"""
India Earnings Trade Analyzer - 5-factor post-results scoring for NSE stocks.

Same scoring as the US earnings-trade-analyzer (gap size 25%, pre-results trend
30%, volume trend 20%, MA200 position 15%, MA50 position 10%; grades A-D), on
Indian data:

  - Results dates: a saved JSON reply from DalalOS ``get_market_calendar``
    (types=["results"], a past window; 1 credit) or Drishti, read with --events.
    Scripts cannot call those connectors; Claude fetches once and saves the file.
  - Prices: free official NSE history (bonus/split adjusted) from the cache.
  - NSE does not publish announcement times, so the reaction is measured on
    the first session after the board-meeting date (next-session gap); the
    two-day move from the close before the meeting is reported alongside.
  - Liquidity gates in rupees (price and 50-day turnover).

Usage:
    python3 analyze_india_earnings_trades.py --events reports/results_calendar.json --output-dir reports/
    python3 analyze_india_earnings_trades.py --events "reports/dalalos_results_*.json" --lookback-days 10
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _india_data as india  # noqa: E402
from calculators.gap_size_calculator import _score_gap, calculate_gap  # noqa: E402
from calculators.ma50_calculator import calculate_ma50_position  # noqa: E402
from calculators.ma200_calculator import calculate_ma200_position  # noqa: E402
from calculators.pre_earnings_trend_calculator import calculate_pre_earnings_trend  # noqa: E402
from calculators.volume_trend_calculator import calculate_volume_trend  # noqa: E402
from report_generator import generate_json_report, generate_markdown_report  # noqa: E402
from scorer import calculate_composite_score  # noqa: E402

HISTORY_CALENDAR_DAYS = 400
MIN_BARS = 50
CRORE = 10_000_000


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="India Earnings Trade Analyzer - 5-factor post-results scoring"
    )
    p.add_argument(
        "--events",
        nargs="+",
        default=None,
        help="Results-calendar JSON file(s) or globs (default: the india-earnings-calendar archive)",
    )
    p.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Board meetings within N calendar days of as-of",
    )
    p.add_argument(
        "--universe", default="nifty500", help="Nifty universe filter, or 'all' (default nifty500)"
    )
    p.add_argument("--min-price", type=float, default=50.0, help="Minimum close in ₹ (default 50)")
    p.add_argument(
        "--min-turnover-cr", type=float, default=5.0, help="Minimum 50-day avg turnover, ₹ crore"
    )
    p.add_argument("--min-gap", type=float, default=0.0, help="Minimum |gap| %% (default 0)")
    p.add_argument(
        "--reaction",
        choices=["two_day", "next_open"],
        default="two_day",
        help="Reaction measure scored as the gap factor (default two_day; see references)",
    )
    p.add_argument(
        "--direction",
        choices=["up", "both"],
        default="up",
        help="Grade only positive reactions (default; long setups) or both directions",
    )
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--as-of", help="YYYY-MM-DD; default: latest completed NSE session")
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--output-dir", default="reports/")
    args = p.parse_args(argv)
    if args.lookback_days < 0:
        p.error("--lookback-days must be >= 0")
    if args.universe != "all" and args.universe not in india.UNIVERSE_FILES:
        p.error(f"--universe must be 'all' or one of {', '.join(sorted(india.UNIVERSE_FILES))}")
    return args


def load_events(patterns: list) -> tuple[list, list]:
    files = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) if any(c in pattern for c in "*?[") else [pattern]
        files.extend(m for m in matches if Path(m).is_file())
    if not files:
        raise ValueError(f"no events file found for {patterns}")
    payloads = []
    for f in files:
        try:
            payloads.append(json.loads(Path(f).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read events file {f}: {exc}") from exc
    return india.normalize_results_events(payloads), files


def to_history(bars: list) -> list:
    return [
        {k: b.get(k) for k in ("date", "open", "high", "low", "close", "volume")}
        for b in reversed(bars)
        if isinstance(b.get("volume"), (int, float))
    ]


def analyze_stock(daily_prices: list, meeting_date: str, reaction: str = "two_day") -> dict:
    """Score the five factors.

    NSE does not publish announcement times and many boards report during market
    hours, so by default the reaction factor uses the two-day move (close after the
    meeting vs close before it); ``next_open`` keeps the US next-session gap.
    """
    gap = calculate_gap(daily_prices, meeting_date, "unknown")
    gap["next_session_gap_pct"] = gap.get("gap_pct")
    gap["reaction_measure"] = "next_open"
    two_day = two_day_reaction(daily_prices, meeting_date)
    if reaction == "two_day" and two_day is not None:
        gap["gap_pct"] = two_day
        gap["score"] = _score_gap(abs(two_day))
        gap["reaction_measure"] = "two_day"
    trend = calculate_pre_earnings_trend(daily_prices, meeting_date)
    volume = calculate_volume_trend(daily_prices, meeting_date)
    ma200 = calculate_ma200_position(daily_prices)
    ma50 = calculate_ma50_position(daily_prices)
    composite = calculate_composite_score(
        gap_score=gap["score"],
        trend_score=trend["score"],
        volume_score=volume["score"],
        ma200_score=ma200["score"],
        ma50_score=ma50["score"],
    )
    return {
        "gap": gap,
        "pre_earnings_trend": trend,
        "volume_trend": volume,
        "ma200_position": ma200,
        "ma50_position": ma50,
        "composite": composite,
    }


def two_day_reaction(daily_prices: list, meeting_date: str) -> float | None:
    """Close of the session after the meeting vs the close before the meeting, %."""
    dates = [b["date"] for b in daily_prices]
    if meeting_date not in dates:
        return None
    i = dates.index(meeting_date)
    if i == 0 or i + 1 >= len(dates):
        return None
    return round((daily_prices[i - 1]["close"] / daily_prices[i + 1]["close"] - 1) * 100, 2)


def avg_turnover_cr(daily_prices: list, sessions: int = 50) -> float:
    recent = daily_prices[:sessions]
    if not recent:
        return 0.0
    return sum(b["close"] * b["volume"] for b in recent) / len(recent) / CRORE


def resolve_as_of(as_of: str | None, latest: date, sessions_fn) -> date:
    if as_of is None:
        return latest
    try:
        requested = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"--as-of must be YYYY-MM-DD, got {as_of!r}") from exc
    if requested > latest:
        raise ValueError(f"--as-of {as_of} is after the latest completed NSE session {latest}")
    return sessions_fn(requested - timedelta(days=21), requested)[-1]


def main(argv: list | None = None, *, cache=None, now_fn=None, universe_loader=None) -> int:  # noqa: C901
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    loader = universe_loader or india.load_universe
    try:
        patterns = args.events or [str(cache_root / "results_calendar.json")]
        events, files = load_events(patterns)
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        as_of = resolve_as_of(args.as_of, latest, cache._sessions_fn)
        universe = None
        if args.universe != "all":
            universe = {r["symbol"]: r for r in loader(args.universe, cache_root)}
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    window_start = (as_of - timedelta(days=args.lookback_days)).isoformat()
    recent_sessions = [
        d.isoformat()
        for d in cache._sessions_fn(as_of - timedelta(days=args.lookback_days + 14), as_of)
    ]
    in_window = [e for e in events if window_start <= e["date"] <= as_of.isoformat()]
    skipped: dict = {}
    candidates = []
    for event in in_window:
        sym = event["symbol"]
        if universe is not None and sym not in universe:
            skipped.setdefault("outside_universe", []).append(sym)
            continue
        reaction_sessions = [d for d in recent_sessions if d > event["date"]]
        if not reaction_sessions:
            skipped.setdefault("awaiting_reaction_session", []).append(sym)
            continue
        candidates.append(event)
    print(
        f"{len(events)} results events; {len(in_window)} in window {window_start}..{as_of}; "
        f"{len(candidates)} to analyze",
        file=sys.stderr,
    )

    start = as_of - timedelta(days=HISTORY_CALENDAR_DAYS)
    results = []
    for event in candidates:
        sym = event["symbol"]
        try:
            bars = cache.stock_bars(sym, start, as_of)["bars"]
        except (india.IndiaDataError, ValueError):
            skipped.setdefault("no_price_history", []).append(sym)
            continue
        prices = to_history(bars)
        if len(prices) < MIN_BARS:
            skipped.setdefault("insufficient_history", []).append(sym)
            continue
        price = prices[0]["close"]
        turnover = avg_turnover_cr(prices)
        if price < args.min_price or turnover < args.min_turnover_cr:
            skipped.setdefault("below_liquidity_gate", []).append(sym)
            continue
        analysis = analyze_stock(prices, event["date"], args.reaction)
        gap_pct = analysis["gap"].get("gap_pct") or 0.0
        if abs(gap_pct) < args.min_gap:
            skipped.setdefault("below_min_gap", []).append(sym)
            continue
        if args.direction == "up" and gap_pct < 0:
            skipped.setdefault("negative_reaction", []).append(sym)
            continue
        comp = analysis["composite"]
        ref = (universe or {}).get(sym, {})
        results.append(
            {
                "symbol": sym,
                "company_name": event.get("company") or ref.get("company", ""),
                "earnings_date": event["date"],
                "earnings_timing": "unknown",
                "quarter": event.get("quarter"),
                "gap_pct": gap_pct,
                "two_day_reaction_pct": two_day_reaction(prices, event["date"]),
                "next_session_gap_pct": analysis["gap"].get("next_session_gap_pct"),
                "composite_score": comp["composite_score"],
                "grade": comp["grade"],
                "grade_description": comp["grade_description"],
                "guidance": comp["guidance"],
                "weakest_component": comp["weakest_component"],
                "strongest_component": comp["strongest_component"],
                "component_breakdown": comp["component_breakdown"],
                "current_price": round(price, 2),
                "avg_turnover_cr": round(turnover, 2),
                "market_cap": None,
                "sector": ref.get("industry", "Unknown"),
                "industry": ref.get("industry", "Unknown"),
                "components": {
                    "gap_size": analysis["gap"],
                    "pre_earnings_trend": analysis["pre_earnings_trend"],
                    "volume_trend": analysis["volume_trend"],
                    "ma200_position": analysis["ma200_position"],
                    "ma50_position": analysis["ma50_position"],
                },
            }
        )

    results.sort(key=lambda r: r["composite_score"], reverse=True)
    metadata = {
        "generated_at": datetime.now().isoformat(),
        "generator": "india-earnings-trade-analyzer",
        "generator_version": "1.0.0",
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "as_of": as_of.isoformat(),
        "lookback_days": args.lookback_days,
        "reaction_measure": args.reaction,
        "direction": args.direction,
        "universe": args.universe,
        "events_files": files,
        "events_total": len(events),
        "events_in_window": len(in_window),
        "total_screened": len(results),
        "timing_unknown_count": len(results),
        "timing_candidates_total": len(results),
        "skipped": {k: sorted(set(v)) for k, v in skipped.items()},
        "liquidity": {"min_price_inr": args.min_price, "min_avg_turnover_cr": args.min_turnover_cr},
    }
    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_file = os.path.join(args.output_dir, f"india_earnings_trade_{stamp}.json")
    md_file = os.path.join(args.output_dir, f"india_earnings_trade_{stamp}.md")
    generate_json_report(results[: args.top], metadata, json_file, all_results=results)
    generate_markdown_report(results[: args.top], metadata, md_file, all_results=results)
    print(f"Scored {len(results)} NSE results reactions (as of {as_of})")
    for r in results[:5]:
        print(
            f"  {r['symbol']:12} Grade {r['grade']} ({r['composite_score']:.1f}) gap {r['gap_pct']:+.1f}%"
        )
    print(f"  JSON report: {json_file}")
    print(f"  Markdown report: {md_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
