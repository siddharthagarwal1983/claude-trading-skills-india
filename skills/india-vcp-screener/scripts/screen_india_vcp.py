#!/usr/bin/env python3
"""
India VCP Screener - Mark Minervini's Volatility Contraction Pattern on NSE.

Same scoring engine as the US vcp-screener (trend template, contraction
pattern, volume dry-up, pivot proximity, relative strength, execution state),
fed with free official NSE end-of-day data:

  - Universe: a Nifty index (default NIFTY 500) from NSE's constituent CSVs,
    or explicit --symbols.
  - Prices: NSE bhavcopy history, adjusted for bonuses and splits.
  - Benchmark for relative strength: an NSE index (default NIFTY 500).
  - Liquidity is judged on average daily rupee turnover, not share count.

No API key is needed. Data is cached under ./.cache/india-data/ (override with
--cache-dir or INDIA_DATA_CACHE_DIR); the first run over the full Nifty 500
downloads about three years of history per stock and is slow, later runs only
fetch each new session.

Usage:
    python3 screen_india_vcp.py --output-dir reports/
    python3 screen_india_vcp.py --universe nifty200 --top 20
    python3 screen_india_vcp.py --symbols RELIANCE TCS INFY --mode prebreakout

Output:
    india_vcp_screener_YYYY-MM-DD_HHMMSS.json / .md in --output-dir
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _india_data as india
from calculators.execution_state import compute_execution_state
from calculators.pattern_classifier import classify_pattern
from calculators.pivot_proximity_calculator import calculate_pivot_proximity
from calculators.relative_strength_calculator import (
    calculate_relative_strength,
    rank_relative_strength_universe,
)
from calculators.trend_template_calculator import calculate_trend_template
from calculators.vcp_pattern_calculator import calculate_vcp_pattern
from calculators.volume_pattern_calculator import calculate_volume_pattern
from evaluation_coverage import build_evaluation_coverage
from report_generator import generate_json_report, generate_markdown_report
from scorer import calculate_composite_score

HISTORY_CALENDAR_DAYS = 430  # ~290 NSE sessions: 200-day SMA + 22-day slope + slack
MIN_HISTORY_BARS = 230
YEAR_BARS = 252
CRORE = 10_000_000
JUMP_LOOKBACK_BARS = 260


def passes_trend_filter(tt_result: dict, trend_min_score: float = 85.0) -> bool:
    """Check if a stock passes Phase 2 trend template filter.

    Uses raw_score (before extended penalty) so that the CLI --trend-min-score
    flag can override the calculator's hardcoded 85-point gate.
    """
    return tt_result.get("raw_score", 0) >= trend_min_score


def analyze_stock(
    symbol: str,
    historical: list[dict],
    quote: dict,
    benchmark_history: list[dict],
    sector: str = "Unknown",
    company_name: str = "",
    ext_threshold: float = 8.0,
    min_contractions: int = 2,
    t1_depth_min: float = 10.0,
    contraction_ratio: float = 0.70,
    atr_multiplier: float = 1.5,
    min_contraction_days: int = 5,
    lookback_days: int = 120,
    breakout_volume_ratio: float = 1.5,
    max_sma200_extension: float = 50.0,
    wide_and_loose_threshold: float = 15.0,
    as_of_offset: int = 0,
) -> dict | None:
    """
    Full VCP analysis for a single stock (Phase 3).
    No additional API calls needed - uses pre-fetched data.

    Args:
        as_of_offset: Index into ``historical`` (most-recent-first) that the
            analysis should treat as "today". Default 0 = analyze right edge,
            matching cross-sectional screening. Positive values shift the
            as-of cursor into the past for historical/walk-forward analysis;
            the caller is responsible for synthesizing ``quote`` (price /
            yearHigh / yearLow) from the OHLCV slice at that offset.
            ``benchmark_history`` is sliced identically so RS comparisons stay
            aligned. Bars more recent than ``historical[as_of_offset]`` are
            ignored.
    """
    # Historical mode: shift the as-of cursor by slicing both arrays so
    # historical[as_of_offset] becomes index 0 for every downstream calculator.
    if as_of_offset > 0:
        historical = historical[as_of_offset:]
        if benchmark_history:
            benchmark_history = benchmark_history[as_of_offset:]

    # Validate pattern observations before RS/TT can consume invalid closes.
    vcp_result = calculate_vcp_pattern(
        historical,
        lookback_days=lookback_days,
        atr_multiplier=atr_multiplier,
        min_contraction_days=min_contraction_days,
        min_contractions=min_contractions,
        t1_depth_min=t1_depth_min,
        contraction_ratio=contraction_ratio,
        wide_and_loose_threshold=wide_and_loose_threshold,
    )

    if vcp_result.get("data_available") is False:
        print(f"{symbol}: {vcp_result['error']}", file=sys.stderr)
        return None

    price = quote.get("price", 0)
    market_cap = quote.get("marketCap", 0)

    # 1. Relative Strength (needed for Trend Template criterion 7)
    rs_result = calculate_relative_strength(historical, benchmark_history)
    rs_rank = rs_result.get("rs_rank_estimate", 0)

    # 2. Trend Template
    tt_result = calculate_trend_template(
        historical,
        quote,
        rs_rank=rs_rank,
        ext_threshold=ext_threshold,
        max_sma200_extension=max_sma200_extension,
    )

    # 4. Volume Pattern
    # Slice to the same lookback window the VCP calculator used. Contraction
    # high_idx/low_idx are chronological indices over historical[:lookback_days];
    # _zone_volume_analysis maps them back via n - 1 - chrono_idx, so n must
    # equal that slice length or the reverse mapping reads the wrong bars.
    pivot_price = vcp_result.get("pivot_price")
    historical_lookback = historical[:lookback_days]
    vol_result = calculate_volume_pattern(
        historical_lookback,
        pivot_price=pivot_price,
        contractions=vcp_result.get("contractions"),
        breakout_volume_ratio=breakout_volume_ratio,
    )

    # 5. Pivot Proximity
    last_low = None
    contractions = vcp_result.get("contractions", [])
    if contractions:
        last_low = contractions[-1].get("low_price")

    piv_result = calculate_pivot_proximity(
        current_price=price,
        pivot_price=pivot_price,
        last_contraction_low=last_low,
        breakout_volume=vol_result.get("breakout_volume_detected", False),
    )

    # 6. Execution State — separates "strong pattern" from "buyable now"
    sma200_tt = tt_result.get("sma200")
    sma200_distance_pct: float | None = None
    if sma200_tt and sma200_tt > 0:
        sma200_distance_pct = (price - sma200_tt) / sma200_tt * 100

    exec_state_result = compute_execution_state(
        distance_from_pivot_pct=piv_result.get("distance_from_pivot_pct"),
        price=price,
        sma50=tt_result.get("sma50"),
        sma200=sma200_tt,
        sma200_distance_pct=sma200_distance_pct,
        last_contraction_low=last_low,
        breakout_volume=vol_result.get("breakout_volume_detected", False),
        max_sma200_extension=max_sma200_extension,
    )
    execution_state = exec_state_result["state"]

    # 7. Pattern Classifier
    valid_vcp = vcp_result.get("valid_vcp", False)
    wide_and_loose = vcp_result.get("wide_and_loose", False)
    final_depth = contractions[-1].get("depth_pct") if contractions else None

    pattern_type = classify_pattern(
        valid_vcp=valid_vcp,
        num_contractions=vcp_result.get("num_contractions", 0),
        final_contraction_depth=final_depth,
        execution_state=execution_state,
        dry_up_ratio=vol_result.get("dry_up_ratio"),
        wide_and_loose=wide_and_loose,
    )

    # 8. Composite Score (with State Caps)
    composite = calculate_composite_score(
        trend_score=tt_result.get("score", 0),
        contraction_score=vcp_result.get("score", 0),
        volume_score=vol_result.get("score", 0),
        pivot_score=piv_result.get("score", 0),
        rs_score=rs_result.get("score", 0),
        valid_vcp=valid_vcp,
        execution_state=execution_state,
        pattern_type=pattern_type,
        wide_and_loose=wide_and_loose,
        sma200_extension_pct=sma200_distance_pct,
    )

    return {
        "symbol": symbol,
        "company_name": company_name,
        "sector": sector,
        "price": price,
        "market_cap": market_cap,
        "composite_score": composite["composite_score"],
        "quality_rating": composite.get("quality_rating", composite["rating"]),
        "rating": composite["rating"],
        "rating_description": composite["rating_description"],
        "guidance": composite["guidance"],
        "valid_vcp": valid_vcp,
        "execution_state": execution_state,
        "execution_state_reasons": exec_state_result.get("reasons", []),
        "pattern_type": pattern_type,
        "wide_and_loose": wide_and_loose,
        "state_cap_applied": composite.get("state_cap_applied", False),
        "cap_reason": composite.get("cap_reason"),
        "sma200_distance_pct": round(sma200_distance_pct, 1)
        if sma200_distance_pct is not None
        else None,
        "distance_from_pivot_pct": piv_result.get("distance_from_pivot_pct"),
        "weakest_component": composite["weakest_component"],
        "weakest_score": composite["weakest_score"],
        "strongest_component": composite["strongest_component"],
        "strongest_score": composite["strongest_score"],
        "trend_template": tt_result,
        "vcp_pattern": vcp_result,
        "volume_pattern": vol_result,
        "pivot_proximity": piv_result,
        "relative_strength": rs_result,
    }


def is_stale_price(
    historical: list[dict],
    lookback: int = 10,
    threshold: float = 1.0,
) -> bool:
    """Detect acquired/pinned stocks with abnormally flat price action.

    Args:
        historical: Price data (most-recent-first)
        lookback: Number of recent days to check
        threshold: Max average daily range % to be considered stale

    Returns:
        True if the stock's price action is stale (likely acquired/pinned)
    """
    if len(historical) < lookback:
        return False

    recent = historical[:lookback]
    ranges = []
    for bar in recent:
        high, low, close = bar["high"], bar["low"], bar["close"]
        if close > 0:
            ranges.append((high - low) / close * 100)

    if not ranges:
        return False

    avg_range_pct = sum(ranges) / len(ranges)
    return avg_range_pct < threshold


def compute_entry_ready(
    result: dict,
    max_above_pivot: float = 3.0,
    max_risk: float = 15.0,
    require_valid_vcp: bool = True,
) -> bool:
    """Determine if a stock is entry-ready based on configurable thresholds.

    Args:
        result: Analysis result dict from analyze_stock()
        max_above_pivot: Max % above pivot for entry readiness
        max_risk: Max risk % for entry readiness
        require_valid_vcp: Whether valid_vcp=True is required
    """
    # State-based immediate rejection
    state = result.get("execution_state")
    if state in ("Invalid", "Damaged", "Overextended", "Extended", "Early-post-breakout"):
        return False

    valid_vcp = result.get("valid_vcp", False)
    distance = result.get("distance_from_pivot_pct")
    dry_up_ratio = result.get("volume_pattern", {}).get("dry_up_ratio")
    risk_pct = result.get("pivot_proximity", {}).get("risk_pct")

    if require_valid_vcp and not valid_vcp:
        return False
    if distance is None:
        return False
    if not (-8.0 <= distance <= max_above_pivot):
        return False
    if dry_up_ratio is None or dry_up_ratio > 1.0:
        return False
    # Reject if price is below stop level (risk_pct == 0 means price < stop)
    trade_status = result.get("pivot_proximity", {}).get("trade_status")
    if trade_status == "BELOW STOP LEVEL":
        return False
    if risk_pct is None or risk_pct <= 0 or risk_pct > max_risk:
        return False
    return True


# ---------------------------------------------------------------------------
# India data adapters
# ---------------------------------------------------------------------------


def to_history(bars: list[dict]) -> list[dict]:
    """Oldest-first NSE bars -> most-recent-first OHLCV dicts the calculators expect.

    Bars without a usable volume are dropped: the calculators read OHLCV directly
    and must never see a missing value turned into zero.
    """
    out = []
    for bar in reversed(bars):
        if not isinstance(bar.get("volume"), (int, float)):
            continue
        out.append(
            {
                "date": bar["date"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar.get("volume"),
            }
        )
    return out


def synthesize_quote(history: list[dict], name: str = "", sector: str = "Unknown") -> dict:
    """Build the quote fields the calculators read from most-recent-first bars."""
    year = history[:YEAR_BARS]
    recent = history[:50]
    volumes = [b["volume"] for b in recent if isinstance(b.get("volume"), (int, float))]
    turnovers = [
        b["close"] * b["volume"] for b in recent if isinstance(b.get("volume"), (int, float))
    ]
    return {
        "price": history[0]["close"],
        "yearHigh": max(b["high"] for b in year),
        "yearLow": min(b["low"] for b in year),
        "avgVolume": sum(volumes) / len(volumes) if volumes else 0,
        "avgTurnover": sum(turnovers) / len(turnovers) if turnovers else 0,
        "marketCap": None,
        "name": name,
        "sector": sector,
    }


def pre_filter_stock(quote: dict, min_price: float = 50.0, min_turnover_cr: float = 5.0) -> tuple:
    """Cheap Stage 2 pre-filter on synthesized quote data.

    Criteria (India defaults):
    - Price >= ₹50
    - 50-day average turnover >= ₹5 crore (liquidity in rupees, not shares)
    - At least 20% above the 52-week low
    - Within 30% of the 52-week high

    Returns (passed, stage2_likelihood_score).
    """
    price = quote.get("price") or 0
    year_high = quote.get("yearHigh") or 0
    year_low = quote.get("yearLow") or 0
    if price < min_price:
        return False, 0
    if (quote.get("avgTurnover") or 0) < min_turnover_cr * CRORE:
        return False, 0
    if year_low <= 0 or year_high <= 0:
        return False, 0
    pct_above_low = (price - year_low) / year_low
    pct_below_high = (year_high - price) / year_high
    if pct_above_low < 0.20 or pct_below_high > 0.30:
        return False, 0
    return True, min(pct_above_low, 1.0) * 50 + (1 - pct_below_high) * 50


def recent_unadjusted_jump(warnings: list[dict], history: list[dict]) -> dict | None:
    """A >20% jump NSE did not adjust (e.g. a demerger) inside the analysis window."""
    if not warnings or not history:
        return None
    window_start = history[min(len(history), JUMP_LOOKBACK_BARS) - 1]["date"]
    for warning in warnings:
        if warning["date"] >= window_start:
            return warning
    return None


def load_universe_rows(args, cache_root: Path) -> tuple[list[dict], str]:
    if args.symbols:
        try:
            known = {r["symbol"]: r for r in india.load_universe("niftytotalmarket", cache_root)}
        except (india.IndiaDataError, ValueError, OSError):
            known = {}  # names and industries are cosmetic; screening still works
        unique = []
        for sym in dict.fromkeys(india.safe_symbol(s) for s in args.symbols):
            ref = known.get(sym, {})
            unique.append(
                {
                    "symbol": sym,
                    "company": ref.get("company", ""),
                    "industry": ref.get("industry") or "Unknown",
                }
            )
        return unique, f"Custom ({len(unique)} NSE stocks)"
    rows = india.load_universe(args.universe, cache_root)
    return rows, f"{args.universe.upper()} ({len(rows)} NSE stocks)"


def rescore(r: dict) -> None:
    """Recompute the composite after universe-wide RS ranking (keeps state caps)."""
    composite = calculate_composite_score(
        trend_score=r["trend_template"].get("score", 0),
        contraction_score=r["vcp_pattern"].get("score", 0),
        volume_score=r["volume_pattern"].get("score", 0),
        pivot_score=r["pivot_proximity"].get("score", 0),
        rs_score=r["relative_strength"].get("score", 0),
        valid_vcp=r.get("valid_vcp", False),
        execution_state=r.get("execution_state"),
        pattern_type=r.get("pattern_type"),
        wide_and_loose=r.get("wide_and_loose", False),
        sma200_extension_pct=r.get("sma200_distance_pct"),
    )
    r["composite_score"] = composite["composite_score"]
    r["quality_rating"] = composite.get("quality_rating", composite["rating"])
    for key in (
        "rating",
        "rating_description",
        "guidance",
        "weakest_component",
        "weakest_score",
        "strongest_component",
        "strongest_score",
    ):
        r[key] = composite[key]
    r["state_cap_applied"] = composite.get("state_cap_applied", False)
    r["cap_reason"] = composite.get("cap_reason")


def prepare_history(
    sym: str, bars: list[dict], warnings: list[dict], as_of: str
) -> tuple[list[dict] | None, str | None]:
    """Most-recent-first history for one symbol, or (None, outcome) when unusable.

    ``bars`` are the oldest-first adjusted NSE bars of the screening window ending
    at ``as_of``; ``warnings`` are the unexplained price jumps of those bars.
    """
    history = to_history(bars)
    if len(history) < MIN_HISTORY_BARS:
        return None, "insufficient_history"
    if history[0]["date"] != as_of:
        return None, "stale_price_data"
    jump = recent_unadjusted_jump(warnings, history)
    if jump:
        _log(f"  {sym}: skipped, {jump['change_pct']}% move on {jump['date']} ({jump['note']})")
        return None, "unadjusted_corporate_action"
    return history, None


def run_funnel(
    rows: list[dict],
    histories: dict,
    benchmark_history: list[dict],
    args: argparse.Namespace,
    outcomes: dict,
) -> tuple[list[dict], list, list]:
    """Pre-filter, trend template, VCP analysis, universe RS rank and entry readiness.

    Shared by ``main`` and india-vcp-backtester so a backtest screens each past
    session exactly as the live screener does. ``outcomes`` is updated in place.
    Returns (results sorted by composite score, pre_filtered, trend_passed).
    """
    pre_filtered = []
    for row in rows:
        sym = row["symbol"]
        history = histories.get(sym)
        if history is None:
            continue
        quote = synthesize_quote(history, row.get("company", ""), row.get("industry") or "Unknown")
        passed, likelihood = pre_filter_stock(quote, args.min_price, args.min_turnover_cr)
        outcomes[sym] = None
        if passed:
            pre_filtered.append((sym, likelihood, quote))

    pre_filtered.sort(key=lambda item: item[1], reverse=True)
    candidates = pre_filtered[: args.max_candidates]
    for sym, _score, _quote in pre_filtered[args.max_candidates :]:
        outcomes[sym] = "candidate_limit"

    trend_passed = []
    for sym, _likelihood, quote in candidates:
        hist = histories[sym]
        rs_result = calculate_relative_strength(hist, benchmark_history)
        tt_result = calculate_trend_template(
            hist,
            quote,
            rs_rank=rs_result.get("rs_rank_estimate", 0),
            ext_threshold=args.ext_threshold,
        )
        if rs_result.get("error") or tt_result.get("error"):
            outcomes[sym] = "analysis_failed"
            continue
        if passes_trend_filter(tt_result, args.trend_min_score):
            trend_passed.append((sym, quote))

    results = []
    for sym, quote in trend_passed:
        hist = histories[sym]
        if is_stale_price(hist, threshold=args.min_atr_pct):
            _log(f"  Skipping {sym} (flat price action)")
            continue
        analysis = analyze_stock(
            sym,
            hist,
            quote,
            benchmark_history,
            quote.get("sector", "Unknown"),
            quote.get("name") or sym,
            ext_threshold=args.ext_threshold,
            min_contractions=args.min_contractions,
            t1_depth_min=args.t1_depth_min,
            contraction_ratio=args.contraction_ratio,
            atr_multiplier=args.atr_multiplier,
            min_contraction_days=args.min_contraction_days,
            lookback_days=args.lookback_days,
            breakout_volume_ratio=args.breakout_volume_ratio,
            max_sma200_extension=args.max_sma200_extension,
            wide_and_loose_threshold=args.wide_and_loose_threshold,
        )
        if analysis:
            analysis["avg_turnover_cr"] = round(quote["avgTurnover"] / CRORE, 2)
            results.append(analysis)
        else:
            outcomes[sym] = "analysis_failed"

    if results:
        ranked = rank_relative_strength_universe(
            {r["symbol"]: r["relative_strength"] for r in results}
        )
        for r in results:
            r["relative_strength"] = ranked[r["symbol"]]
            rescore(r)
    for r in results:
        r["entry_ready"] = compute_entry_ready(
            r,
            max_above_pivot=args.max_above_pivot,
            max_risk=args.max_risk,
            require_valid_vcp=not args.no_require_valid_vcp,
        )
    results.sort(key=lambda r: r["composite_score"], reverse=True)
    if args.mode == "prebreakout":
        results = [r for r in results if r.get("entry_ready")]
    if args.strict:
        results = [
            r
            for r in results
            if r.get("valid_vcp") and r.get("execution_state") in ("Pre-breakout", "Breakout")
        ]

    return results, pre_filtered, trend_passed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="India VCP Screener - Minervini Volatility Contraction Pattern on NSE"
    )
    parser.add_argument(
        "--universe",
        default="nifty500",
        choices=sorted(india.UNIVERSE_FILES),
        help="Nifty index to screen (default: nifty500)",
    )
    parser.add_argument("--symbols", nargs="+", help="Explicit NSE symbols (overrides --universe)")
    parser.add_argument(
        "--benchmark",
        default="NIFTY 500",
        help="NSE index for relative strength (default: NIFTY 500)",
    )
    parser.add_argument("--cache-dir", type=Path, default=None, help="India data cache directory")
    parser.add_argument(
        "--min-price", type=float, default=50.0, help="Minimum price in ₹ (default: 50)"
    )
    parser.add_argument(
        "--min-turnover-cr",
        type=float,
        default=5.0,
        help="Minimum 50-day average turnover in ₹ crore (default: 5)",
    )
    parser.add_argument(
        "--max-candidates", type=int, default=150, help="Pre-filtered stocks sent to full analysis"
    )
    parser.add_argument(
        "--top", type=int, default=20, help="Top results in the report (default: 20)"
    )
    parser.add_argument("--output-dir", default="reports/", help="Output directory for reports")
    parser.add_argument("--mode", choices=["all", "prebreakout"], default="all")
    parser.add_argument("--max-above-pivot", type=float, default=3.0)
    parser.add_argument("--max-risk", type=float, default=15.0)
    parser.add_argument("--no-require-valid-vcp", action="store_true")
    parser.add_argument("--min-atr-pct", type=float, default=1.0)
    parser.add_argument("--ext-threshold", type=float, default=8.0)
    parser.add_argument("--min-contractions", type=int, default=2)
    parser.add_argument("--t1-depth-min", type=float, default=10.0)
    parser.add_argument("--breakout-volume-ratio", type=float, default=1.5)
    parser.add_argument("--trend-min-score", type=float, default=85.0)
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--contraction-ratio", type=float, default=0.70)
    parser.add_argument("--min-contraction-days", type=int, default=5)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--max-sma200-extension", type=float, default=50.0)
    parser.add_argument("--wide-and-loose-threshold", type=float, default=15.0)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(  # noqa: C901 - one linear screening funnel, mirrors the US screener
    argv: list | None = None,
    *,
    cache=None,
    now_fn=None,
) -> int:
    args = parse_arguments(argv)
    if not (0 < args.ext_threshold < 50):
        print("Error: --ext-threshold must be between 0 and 50 (exclusive)", file=sys.stderr)
        return 1
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)

    try:
        as_of = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        rows, universe_desc = load_universe_rows(args, cache_root)
        start = as_of - timedelta(days=HISTORY_CALENDAR_DAYS)
        benchmark = cache.index_bars(args.benchmark, start, as_of)
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    benchmark_history = to_history(benchmark["bars"])
    if len(benchmark_history) < MIN_HISTORY_BARS:
        print(
            f"Error: benchmark {args.benchmark} has only {len(benchmark_history)} bars",
            file=sys.stderr,
        )
        return 1

    _log(f"India VCP screener: {universe_desc}, as of {as_of}, benchmark {args.benchmark}")
    outcomes: dict = {}
    histories: dict = {}
    for position, row in enumerate(rows, 1):
        sym = row["symbol"]
        if position % 50 == 0 or position == len(rows):
            _log(f"  Loading history {position}/{len(rows)}")
        try:
            data = cache.stock_bars(sym, start, as_of)
        except india.SymbolNotFoundError:
            outcomes[sym] = "missing_data"
            continue
        except (india.IndiaDataError, ValueError) as exc:
            _log(f"  {sym}: {exc}")
            outcomes[sym] = "provider_error"
            continue
        history, outcome = prepare_history(
            sym, data["bars"], data.get("warnings") or [], as_of.isoformat()
        )
        if history is None:
            outcomes[sym] = outcome
            continue
        histories[sym] = history

    results, pre_filtered, trend_passed = run_funnel(
        rows, histories, benchmark_history, args, outcomes
    )

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_file = os.path.join(args.output_dir, f"india_vcp_screener_{stamp}.json")
    md_file = os.path.join(args.output_dir, f"india_vcp_screener_{stamp}.md")
    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "skill": "india-vcp-screener",
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "as_of": as_of.isoformat(),
        "benchmark": args.benchmark,
        "universe_description": universe_desc,
        "max_candidates": args.max_candidates,
        "ext_threshold": args.ext_threshold,
        "liquidity": {"min_price_inr": args.min_price, "min_avg_turnover_cr": args.min_turnover_cr},
        "tuning_params": {
            "min_contractions": args.min_contractions,
            "t1_depth_min": args.t1_depth_min,
            "breakout_volume_ratio": args.breakout_volume_ratio,
            "trend_min_score": args.trend_min_score,
            "atr_multiplier": args.atr_multiplier,
            "contraction_ratio": args.contraction_ratio,
            "min_contraction_days": args.min_contraction_days,
            "lookback_days": args.lookback_days,
            "max_sma200_extension": args.max_sma200_extension,
            "wide_and_loose_threshold": args.wide_and_loose_threshold,
            "strict": args.strict,
        },
        "funnel": {
            "universe": len(rows),
            "pre_filter_passed": len(pre_filtered),
            "trend_template_passed": len(trend_passed),
            "vcp_candidates": len(results),
        },
        "data_source": "NSE bhavcopy (official, end-of-day), adjusted for bonuses/splits",
    }
    for row in rows:
        outcomes.setdefault(row["symbol"], "missing_data")
    coverage = build_evaluation_coverage(outcomes)
    top = results[: args.top]
    generate_json_report(
        top, metadata, json_file, all_results=results, evaluation_coverage=coverage
    )
    generate_markdown_report(
        top, metadata, md_file, all_results=results, evaluation_coverage=coverage
    )

    print(f"India VCP screening complete (as of {as_of})")
    print(
        f"Evaluation coverage: {coverage['evaluated']}/{coverage['universe_size']} "
        f"evaluated ({coverage['verdict']})"
    )
    for i, r in enumerate(results[:5], 1):
        pivot = r.get("vcp_pattern", {}).get("pivot_price")
        pivot_str = f"Pivot: ₹{pivot:,.2f}" if pivot else ""
        print(
            f"  {i}. {r['symbol']:12} Score: {r['composite_score']:5.1f} ({r['rating']}) {pivot_str}"
        )
    if not results:
        print(
            "  No VCP candidates found."
            if coverage["verdict"] == "complete"
            else "  Result undetermined: incomplete evaluation coverage."
        )
    print(f"  JSON report:     {json_file}")
    print(f"  Markdown report: {md_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
