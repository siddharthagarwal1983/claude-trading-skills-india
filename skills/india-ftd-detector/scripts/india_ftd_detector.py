#!/usr/bin/env python3
"""
India FTD Detector - Follow-Through Day bottom confirmation on NSE indexes.

William O'Neil's rally-attempt / follow-through-day state machine with dual
index tracking, as in the US ftd-detector, on free official NSE data:

  - Primary index: NIFTY 50 (large caps).
  - Second index: NIFTY 500 by default (broad market); NIFTY MIDCAP 150 is a
    useful alternative for risk appetite.
  - NSE index volume is real traded volume, so the "volume above prior day"
    test applies directly. Diwali Muhurat sessions (one hour, tiny volume) are
    removed before analysis so they cannot fake a higher-volume FTD.

Usage:
    python3 india_ftd_detector.py --output-dir reports/
    python3 india_ftd_detector.py --secondary-index "NIFTY MIDCAP 150"
    python3 india_ftd_detector.py --as-of 2026-10-30

Output:
    india_ftd_detector_YYYY-MM-DD_HHMMSS.json / .md in --output-dir
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import _india_data as india  # noqa: E402
from post_ftd_monitor import assess_post_ftd_health  # noqa: E402
from rally_tracker import get_market_state  # noqa: E402
from report_generator import generate_json_report, generate_markdown_report  # noqa: E402

PRIMARY_INDEX = "NIFTY 50"
DEFAULT_SECONDARY = "NIFTY 500"
HISTORY_SESSIONS = 80


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="India FTD Detector - NSE follow-through days")
    parser.add_argument(
        "--secondary-index",
        default=DEFAULT_SECONDARY,
        help='Second NSE index tracked with NIFTY 50 (default "NIFTY 500")',
    )
    parser.add_argument("--as-of", help="YYYY-MM-DD; default: latest completed NSE session")
    parser.add_argument("--cache-dir", type=Path, default=None, help="India data cache directory")
    parser.add_argument(
        "--output-dir", default="reports/", help="Output directory (default reports/)"
    )
    return parser.parse_args(argv)


def load_index_history(cache, name: str, end: date) -> tuple[list[dict], list[str]]:
    """Most-recent-first OHLCV (Muhurat removed) plus the removed Muhurat dates."""
    start = end - timedelta(days=int(HISTORY_SESSIONS * 1.6) + 30)
    data = cache.index_bars(name, start, end)
    bars, removed = india.drop_muhurat_sessions(data.get("bars") or [])
    history = [
        {k: b.get(k) for k in ("date", "open", "high", "low", "close", "volume")}
        for b in reversed(bars)
    ][:HISTORY_SESSIONS]
    return history, removed


def resolve_end(as_of: str | None, latest: date, sessions_fn) -> date:
    if as_of is None:
        return latest
    try:
        requested = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"--as-of must be YYYY-MM-DD, got {as_of!r}") from exc
    if requested > latest:
        raise ValueError(f"--as-of {as_of} is after the latest completed NSE session {latest}")
    sessions = sessions_fn(requested - timedelta(days=21), requested)
    if not sessions:
        raise ValueError(f"no NSE session on or before {as_of}")
    return sessions[-1]


def _combined_label(market_state: dict) -> str:
    """Console label; a gapped run's NO_SIGNAL means "unavailable", not "uptrend"."""
    if market_state.get("data_gap"):
        return f"{market_state['combined_state']} (data gap — state unavailable)"
    return market_state["combined_state"]


def _serialize_index(idx_data: dict) -> dict:
    """Serialize index analysis for JSON output, removing large rally_days lists."""
    result = {
        "index": idx_data.get("index"),
        "state": idx_data.get("state"),
        "ftd_lifecycle": idx_data.get("ftd_lifecycle"),
        "current_price": idx_data.get("current_price"),
        "lookback_high": idx_data.get("lookback_high"),
        "correction_depth_pct": idx_data.get("correction_depth_pct"),
        "data_quality": idx_data.get("data_quality", {}),
    }
    if idx_data.get("error"):
        result["error"] = idx_data["error"]
    if idx_data.get("data_gap"):
        result["data_gap"] = True
    swing = idx_data.get("swing_low")
    if swing:
        result["swing_low"] = {
            "date": swing.get("swing_low_date"),
            "price": swing.get("swing_low_price"),
            "decline_pct": swing.get("decline_pct"),
            "down_days": swing.get("down_days"),
            "recent_high_date": swing.get("recent_high_date"),
            "recent_high_price": swing.get("recent_high_price"),
        }
    rally = idx_data.get("rally_attempt")
    if rally:
        result["rally_attempt"] = {
            "day1_date": rally.get("day1_date"),
            "day1_low": rally.get("day1_low"),
            "reset_count": rally.get("reset_count", 0),
            "prior_ftd_events": rally.get("prior_ftd_events", []),
            "current_day_count": rally.get("current_day_count"),
            "invalidated": rally.get("invalidated"),
            "invalidation_reason": rally.get("invalidation_reason"),
        }
    ftd = idx_data.get("ftd")
    if ftd:
        result["ftd"] = {
            "ftd_detected": ftd.get("ftd_detected"),
            "ftd_date": ftd.get("ftd_date"),
            "ftd_day_number": ftd.get("ftd_day_number"),
            "ftd_low": ftd.get("ftd_low"),
            "gain_pct": ftd.get("gain_pct"),
            "gain_tier": ftd.get("gain_tier"),
            "volume_above_avg": ftd.get("volume_above_avg"),
        }
    return result


def build_analysis(
    market_state: dict, primary: list, secondary: list, secondary_label: str, meta: dict
) -> dict:
    quality = market_state.get("quality_score", {})
    return {
        "metadata": {
            "schema_version": 2,
            "skill": "india-ftd-detector",
            "exchange": india.EXCHANGE,
            "data_source": "nse_bhavcopy",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "instruments": {
                "primary": {"symbol": PRIMARY_INDEX, "label": PRIMARY_INDEX, "kind": "index"},
                "secondary": {"symbol": secondary_label, "label": secondary_label, "kind": "index"},
            },
            "index_prices": {
                "primary": primary[0]["close"] if primary else None,
                "secondary": secondary[0]["close"] if secondary else None,
            },
            **meta,
        },
        "market_state": {
            "combined_state": market_state["combined_state"],
            "dual_confirmation": market_state["dual_confirmation"],
            "ftd_index": market_state.get("ftd_index"),
            "data_gap": bool(market_state.get("data_gap")),
        },
        "data_gap": bool(market_state.get("data_gap")),
        "data_quality": market_state.get("data_quality", {}),
        "primary": _serialize_index(market_state["sp500"]),
        "secondary": _serialize_index(market_state["nasdaq"]),
        "quality_score": quality,
        "post_ftd_distribution": market_state.get("post_ftd_distribution", {}),
        "ftd_invalidation": market_state.get("ftd_invalidation", {}),
        "power_trend": market_state.get("power_trend", {}),
    }


def main(argv: list | None = None, *, cache=None, now_fn=None) -> int:
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    secondary_label = args.secondary_index.strip().upper()
    try:
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        end = resolve_end(args.as_of, latest, cache._sessions_fn)
        primary, removed_primary = load_index_history(cache, PRIMARY_INDEX, end)
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not primary:
        print(f"Error: no {PRIMARY_INDEX} history; cannot proceed", file=sys.stderr)
        return 1
    try:
        secondary, removed_secondary = load_index_history(cache, secondary_label, end)
    except (india.IndiaDataError, ValueError) as exc:
        print(
            f"Warning: {secondary_label} unavailable ({exc}); using {PRIMARY_INDEX} only",
            file=sys.stderr,
        )
        secondary, removed_secondary = [], []

    market_state = get_market_state(primary, secondary, nasdaq_label=secondary_label)
    market_state["nasdaq"]["index"] = secondary_label
    market_state = assess_post_ftd_health(
        market_state, list(reversed(primary)), list(reversed(secondary))
    )

    meta = {
        "expected_session": end.isoformat(),
        "muhurat_sessions_removed": sorted(set(removed_primary) | set(removed_secondary)),
    }
    analysis = build_analysis(market_state, primary, secondary, secondary_label, meta)
    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_file = os.path.join(args.output_dir, f"india_ftd_detector_{stamp}.json")
    md_file = os.path.join(args.output_dir, f"india_ftd_detector_{stamp}.md")
    generate_json_report(analysis, json_file)
    generate_markdown_report(analysis, md_file)

    quality = analysis["quality_score"]
    print(
        f"{PRIMARY_INDEX}: {market_state['sp500']['state']}; {secondary_label}: {market_state['nasdaq']['state']}"
    )
    print(f"Combined: {_combined_label(market_state)}")
    print(f"Quality score: {quality.get('total_score', 0)}/100 ({quality.get('signal', 'N/A')})")
    print(f"  JSON Report: {json_file}")
    print(f"  Markdown Report: {md_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
