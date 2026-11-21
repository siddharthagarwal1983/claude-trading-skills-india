#!/usr/bin/env python3
"""
India Downtrend Duration Analyzer (NSE)

Measures how long peak-to-trough declines in NSE stocks typically last, grouped
by NSE industry and by SEBI-style size tier, using the same peak/trough method
as the US downtrend-duration-analyzer:

  - Prices: free official NSE bhavcopy history, adjusted for bonuses/splits.
  - Size tiers from index membership instead of dollar cut-offs:
    Large = NIFTY 100, Mid = NIFTY Midcap 150, Small = NIFTY Smallcap 250,
    Micro = NIFTY Microcap 250, otherwise Unknown.
  - Sectors from the NSE "Industry" field of the index constituent files.
  - Default start 2024-01-01: NSE's free archive has large holes in 2023, so
    only the latest continuous stretch of history is analysed.
  - Declines that span a >20% move NSE did not adjust (e.g. a demerger) are
    dropped instead of being counted as real downtrends.

Usage:
    python3 analyze_india_downtrends.py --output-dir reports/
    python3 analyze_india_downtrends.py --universe nifty200 --sector "Information Technology"
    python3 generate_india_histogram_html.py --output-dir reports/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:  # pragma: no cover - environment guard
    print(
        f"Error: {exc.name} is required. Install this skill's dependencies: "
        f"pip install -r {Path(__file__).resolve().parents[1] / 'requirements.txt'}",
        file=sys.stderr,
    )
    sys.exit(2)

import _india_data as india

TIER_INDEXES = (
    ("Large", "nifty100"),
    ("Mid", "niftymidcap150"),
    ("Small", "niftysmallcap250"),
    ("Micro", "niftymicrocap250"),
)
TIER_ORDER = ["Large", "Mid", "Small", "Micro", "Unknown"]
DEFAULT_START = date(2024, 1, 1)
MAX_GAP_CALENDAR_DAYS = 14


def detect_peaks_troughs(
    prices: pd.DataFrame, peak_window: int = 20, trough_window: int = 20
) -> tuple[list[int], list[int]]:
    """
    Detect local peaks and troughs using rolling window.

    Returns indices of peaks and troughs in the price dataframe.
    """
    closes = prices["close"].values
    n = len(closes)
    peaks = []
    troughs = []

    for i in range(peak_window, n - peak_window):
        window_start = i - peak_window
        window_end = i + peak_window + 1
        window = closes[window_start:window_end]

        # Peak: highest in window
        if closes[i] == np.max(window):
            peaks.append(i)

        # Trough: lowest in window
        if closes[i] == np.min(window):
            troughs.append(i)

    return peaks, troughs


def find_downtrends(
    prices: pd.DataFrame,
    peaks: list[int],
    troughs: list[int],
    min_depth_pct: float = 5.0,
    min_duration_days: int = 3,
) -> list[dict]:
    """
    Identify downtrend periods from peaks to subsequent troughs.

    Returns list of downtrend dictionaries with duration and depth.
    """
    downtrends = []
    closes = prices["close"].values
    dates = prices["date"].values

    for peak_idx in peaks:
        peak_price = closes[peak_idx]
        peak_date = dates[peak_idx]

        # Find the next trough after this peak
        subsequent_troughs = [t for t in troughs if t > peak_idx]
        if not subsequent_troughs:
            continue

        # Find the lowest trough before the next peak
        next_peaks = [p for p in peaks if p > peak_idx]
        end_idx = next_peaks[0] if next_peaks else len(closes)

        valid_troughs = [t for t in subsequent_troughs if t < end_idx]
        if not valid_troughs:
            continue

        # Find the deepest trough
        trough_idx = min(valid_troughs, key=lambda t: closes[t])
        trough_price = closes[trough_idx]
        trough_date = dates[trough_idx]

        # Calculate depth and duration
        depth_pct = ((trough_price - peak_price) / peak_price) * 100
        duration_days = int(trough_idx - peak_idx)

        # Apply filters
        if abs(depth_pct) < min_depth_pct:
            continue
        if duration_days < min_duration_days:
            continue

        downtrends.append(
            {
                "peak_idx": peak_idx,
                "trough_idx": trough_idx,
                "peak_date": pd.Timestamp(peak_date).strftime("%Y-%m-%d"),
                "trough_date": pd.Timestamp(trough_date).strftime("%Y-%m-%d"),
                "peak_price": float(peak_price),
                "trough_price": float(trough_price),
                "duration_days": duration_days,
                "depth_pct": round(depth_pct, 2),
            }
        )

    return downtrends


def compute_statistics(downtrends: list[dict]) -> dict[str, Any]:
    """Compute summary statistics from downtrend list."""
    if not downtrends:
        return {
            "total_downtrends": 0,
            "median_duration_days": 0,
            "mean_duration_days": 0,
            "p25_duration_days": 0,
            "p75_duration_days": 0,
            "p90_duration_days": 0,
        }

    durations = [dt["duration_days"] for dt in downtrends]
    return {
        "total_downtrends": len(downtrends),
        "median_duration_days": int(np.median(durations)),
        "mean_duration_days": round(np.mean(durations), 1),
        "p25_duration_days": int(np.percentile(durations, 25)),
        "p75_duration_days": int(np.percentile(durations, 75)),
        "p90_duration_days": int(np.percentile(durations, 90)),
    }


def group_statistics(downtrends: list[dict], group_key: str) -> dict[str, dict[str, Any]]:
    """Compute statistics grouped by a key (sector or market_cap_tier)."""
    groups: dict[str, list[dict]] = {}

    for dt in downtrends:
        key = dt.get(group_key, "Unknown")
        if key not in groups:
            groups[key] = []
        groups[key].append(dt)

    result = {}
    for key, group_downtrends in groups.items():
        durations = [dt["duration_days"] for dt in group_downtrends]
        result[key] = {
            "count": len(group_downtrends),
            "median_days": int(np.median(durations)),
            "mean_days": round(np.mean(durations), 1),
        }

    return result


# ---------------------------------------------------------------------------
# India data helpers
# ---------------------------------------------------------------------------


def build_tier_map(cache_root: Path, loader=india.load_universe) -> tuple[dict, list]:
    """{symbol: tier} from Nifty size-index membership; missing lists are reported."""
    tiers: dict = {}
    problems: list = []
    for tier, key in TIER_INDEXES:
        try:
            rows = loader(key, cache_root)
        except (india.IndiaDataError, ValueError, OSError) as exc:
            problems.append(f"{key}: {exc}")
            continue
        for row in rows:
            tiers.setdefault(row["symbol"], tier)
    return tiers, problems


def latest_continuous_segment(bars: list[dict], max_gap_days: int = MAX_GAP_CALENDAR_DAYS) -> tuple:
    """Bars after the last calendar gap longer than ``max_gap_days``; (bars, gaps_found)."""
    gaps = 0
    start = 0
    for i in range(1, len(bars)):
        prev = date.fromisoformat(bars[i - 1]["date"])
        cur = date.fromisoformat(bars[i]["date"])
        if (cur - prev).days > max_gap_days:
            gaps += 1
            start = i
    return bars[start:], gaps


def bars_to_frame(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    frame = pd.DataFrame(
        [{k: b.get(k) for k in ("date", "open", "high", "low", "close", "volume")} for b in bars]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date").reset_index(drop=True)


def spans_unadjusted_jump(downtrend: dict, warnings: list[dict]) -> bool:
    return any(
        downtrend["peak_date"] < w["date"] <= downtrend["trough_date"] for w in warnings or []
    )


def analyze_symbol(
    symbol: str,
    data: dict,
    sector: str,
    tier: str,
    peak_window: int,
    trough_window: int,
    min_depth_pct: float,
) -> tuple[list[dict], dict]:
    """Downtrends for one symbol plus notes on what was dropped."""
    bars, gaps = latest_continuous_segment(data.get("bars") or [])
    notes = {"gaps_skipped": gaps, "corporate_action_windows_dropped": 0}
    prices = bars_to_frame(bars)
    if prices.empty or len(prices) < peak_window * 2 + 1:
        notes["skipped"] = "insufficient_history"
        return [], notes
    peaks, troughs = detect_peaks_troughs(prices, peak_window, trough_window)
    if not peaks or not troughs:
        return [], notes
    found = find_downtrends(prices, peaks, troughs, min_depth_pct)
    kept = []
    for dt in found:
        if spans_unadjusted_jump(dt, data.get("warnings") or []):
            notes["corporate_action_windows_dropped"] += 1
            continue
        dt.update({"symbol": symbol, "sector": sector or "Unknown", "market_cap_tier": tier})
        kept.append(dt)
    return kept, notes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def generate_markdown_report(result: dict[str, Any], output_path: Path) -> None:
    params = result["parameters"]
    summary = result["summary"]
    lines = [
        "# India Downtrend Duration Analysis (NSE)",
        "",
        f"**Date**: {result['analysis_date'][:10]}",
        f"**Universe**: {params['universe']}",
        f"**Period**: {params['start_date']} to {params['end_date']}",
    ]
    if params.get("sector_filter"):
        lines.append(f"**Industry**: {params['sector_filter']}")
    lines += [
        "",
        "## Summary Statistics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Downtrends | {summary['total_downtrends']:,} |",
        f"| Median Duration | {summary['median_duration_days']} sessions |",
        f"| Mean Duration | {summary['mean_duration_days']} sessions |",
        f"| 25th Percentile | {summary['p25_duration_days']} sessions |",
        f"| 75th Percentile | {summary['p75_duration_days']} sessions |",
        f"| 90th Percentile | {summary['p90_duration_days']} sessions |",
        "",
    ]
    by_tier = result.get("by_market_cap") or {}
    if by_tier:
        lines += [
            "## By Size Tier (Nifty index membership)",
            "",
            "| Tier | Count | Median | Mean |",
            "|------|-------|--------|------|",
        ]
        for tier in TIER_ORDER:
            if tier in by_tier:
                s = by_tier[tier]
                lines.append(
                    f"| {tier} | {s['count']} | {s['median_days']} sessions | {s['mean_days']} sessions |"
                )
        lines.append("")
    by_sector = result.get("by_sector") or {}
    if by_sector:
        lines += [
            "## By Industry",
            "",
            "| Industry | Count | Median | Mean |",
            "|------|-------|--------|------|",
        ]
        for sector, s in sorted(by_sector.items(), key=lambda item: item[1]["median_days"]):
            lines.append(
                f"| {sector} | {s['count']} | {s['median_days']} sessions | {s['mean_days']} sessions |"
            )
        lines.append("")
    notes = result.get("data_notes") or {}
    lines += [
        "## Data Notes",
        "",
        "- Source: NSE bhavcopy (official end-of-day), adjusted for bonuses and splits.",
        f"- Symbols analysed: {notes.get('symbols_analysed', 0)}; skipped: {len(notes.get('skipped_symbols') or {})}",
        f"- Declines dropped for unadjusted corporate actions (e.g. demergers): "
        f"{notes.get('corporate_action_windows_dropped', 0)}",
        f"- Symbols whose history had archive gaps (older part ignored): {notes.get('symbols_with_gaps', 0)}",
        "- Durations are in NSE trading sessions, peak close to trough close.",
        "",
        "## How to Use",
        "",
        "1. Use the median for typical expectations and P75-P90 for conservative planning.",
        "2. Compare tiers: smaller NSE tiers usually take longer to bottom and fall deeper.",
        "3. Treat thin groups (fewer than ~20 declines) as anecdotes, not statistics.",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _iso(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze historical downtrend durations of NSE stocks by industry and size tier"
    )
    parser.add_argument("--universe", default="nifty500", choices=sorted(india.UNIVERSE_FILES))
    parser.add_argument("--symbols", nargs="+", help="Explicit NSE symbols (overrides --universe)")
    parser.add_argument(
        "--sector", help="Filter to one NSE industry, e.g. 'Information Technology'"
    )
    parser.add_argument(
        "--start", type=_iso, default=DEFAULT_START, help="First date (default 2024-01-01)"
    )
    parser.add_argument(
        "--end", type=_iso, help="Last date (default: latest completed NSE session)"
    )
    parser.add_argument("--peak-window", type=int, default=20)
    parser.add_argument("--trough-window", type=int, default=20)
    parser.add_argument(
        "--min-depth", type=float, default=5.0, help="Minimum decline %% (default 5)"
    )
    parser.add_argument("--max-stocks", type=int, default=500)
    parser.add_argument("--cache-dir", type=Path, default=None, help="India data cache directory")
    parser.add_argument(
        "--output-dir", default="reports", help="Output directory (default reports)"
    )
    return parser.parse_args(argv)


def industry_lookup(cache_root: Path, loader) -> dict:
    """NSE industry per symbol from the NIFTY Total Market list (best effort)."""
    try:
        return {
            r["symbol"]: r.get("industry") or "Unknown"
            for r in loader("niftytotalmarket", cache_root)
        }
    except (india.IndiaDataError, ValueError, OSError):
        return {}


def select_rows(args, cache_root: Path, loader) -> list[dict]:
    if args.symbols:
        industries = industry_lookup(cache_root, loader)
        return [
            {"symbol": sym, "industry": industries.get(sym, "Unknown")}
            for sym in dict.fromkeys(india.safe_symbol(s) for s in args.symbols)
        ]
    rows = loader(args.universe, cache_root)
    if args.sector:
        wanted = args.sector.strip().lower()
        rows = [r for r in rows if (r.get("industry") or "").lower() == wanted]
        if not rows:
            raise ValueError(f"No {args.universe} constituents in industry {args.sector!r}")
    return rows[: args.max_stocks]


def main(argv: list | None = None, *, cache=None, loader=None, now_fn=None) -> int:
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    loader = loader or india.load_universe
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    try:
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        end = min(args.end, latest) if args.end else latest
        if args.start >= end:
            raise ValueError(f"--start {args.start} must be before {end}")
        rows = select_rows(args, cache_root, loader)
        tiers, tier_problems = build_tier_map(cache_root, loader)
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Analyzing {len(rows)} NSE stocks from {args.start} to {end}", file=sys.stderr)
    downtrends: list[dict] = []
    skipped: dict = {}
    dropped = 0
    with_gaps = 0
    for i, row in enumerate(rows, 1):
        sym = row["symbol"]
        if i % 50 == 0 or i == len(rows):
            print(f"  Progress: {i}/{len(rows)}", file=sys.stderr, flush=True)
        try:
            data = cache.stock_bars(sym, args.start, end)
        except (india.IndiaDataError, ValueError) as exc:
            skipped[sym] = str(exc)
            continue
        found, notes = analyze_symbol(
            sym,
            data,
            row.get("industry") or "Unknown",
            tiers.get(sym, "Unknown"),
            args.peak_window,
            args.trough_window,
            args.min_depth,
        )
        if notes.get("skipped"):
            skipped[sym] = notes["skipped"]
        dropped += notes["corporate_action_windows_dropped"]
        with_gaps += 1 if notes["gaps_skipped"] else 0
        downtrends.extend(found)

    for dt in downtrends:  # numpy ints are not JSON serialisable
        dt["peak_idx"], dt["trough_idx"] = int(dt["peak_idx"]), int(dt["trough_idx"])
    result = {
        "schema_version": "1.0",
        "skill": "india-downtrend-duration-analyzer",
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "analysis_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "parameters": {
            "universe": "custom" if args.symbols else args.universe,
            "start_date": args.start.isoformat(),
            "end_date": end.isoformat(),
            "sector_filter": args.sector,
            "peak_window": args.peak_window,
            "trough_window": args.trough_window,
            "min_depth_pct": args.min_depth,
            "max_stocks": args.max_stocks,
        },
        "summary": compute_statistics(downtrends),
        "by_sector": group_statistics(downtrends, "sector"),
        "by_market_cap": group_statistics(downtrends, "market_cap_tier"),
        "downtrends": downtrends,
        "data_notes": {
            "symbols_analysed": len(rows) - len(skipped),
            "skipped_symbols": skipped,
            "corporate_action_windows_dropped": dropped,
            "symbols_with_gaps": with_gaps,
            "tier_list_problems": tier_problems,
        },
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = output_dir / f"india_downtrend_analysis_{stamp}.json"
    json_path.write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    md_path = output_dir / f"india_downtrend_analysis_{stamp}.md"
    generate_markdown_report(result, md_path)
    print(f"Found {len(downtrends)} downtrend periods across {len(rows) - len(skipped)} stocks")
    print(f"JSON report saved to: {json_path}")
    print(f"Markdown report saved to: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
