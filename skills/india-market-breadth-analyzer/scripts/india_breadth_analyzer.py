#!/usr/bin/env python3
"""
India Market Breadth Analyzer (NSE)

Scores how broadly NSE stocks are participating in the trend, from the daily
bars of a Nifty universe (default NIFTY 500) on free official NSE data:

  - % of stocks above their 20 / 50 / 200-day moving averages
  - 8-session vs 50-session average of %>200DMA (breadth trend)
  - Net new 52-week highs minus lows
  - McClellan oscillator and summation index from advances/declines
  - NSE-wide advance/decline for the latest session (context only)

Output: a 0-100 composite (Strong / Healthy / Neutral / Weak / Critical) that
india-exposure-coach reads, plus the recent breadth history.

Usage:
    python3 india_breadth_analyzer.py --output-dir reports/
    python3 india_breadth_analyzer.py --universe nifty200 --as-of 2026-10-30
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _india_data as india  # noqa: E402
from breadth_calculator import (  # noqa: E402
    add_derived_series,
    daily_breadth,
    percentile_rank,
    score_latest,
    stock_flags,
)

HISTORY_CALENDAR_DAYS = 560  # ~380 sessions: 252 for 52-week highs + smoothing room
REPORT_SESSIONS = 60
MIN_COVERAGE = 0.90


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="India Market Breadth Analyzer (NSE)")
    parser.add_argument("--universe", default="nifty500", choices=sorted(india.UNIVERSE_FILES))
    parser.add_argument(
        "--calendar-index", default="NIFTY 500", help="Index whose sessions define the calendar"
    )
    parser.add_argument("--as-of", help="YYYY-MM-DD; default: latest completed NSE session")
    parser.add_argument("--no-market-ad", action="store_true", help="Skip the NSE-wide A/D lookup")
    parser.add_argument("--cache-dir", type=Path, default=None, help="India data cache directory")
    parser.add_argument("--output-dir", default="reports/")
    return parser.parse_args(argv)


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


def nse_market_breadth(client, day: date) -> dict | None:
    """NSE-wide advances/declines for one session (all listed securities)."""
    try:
        payload = client.call_tool("get_market_breadth", {"date": day.isoformat()})
    except (india.IndiaDataError, ValueError):
        return None
    if not isinstance(payload, dict) or "advances" not in payload:
        return None
    return {
        k: payload.get(k)
        for k in ("date", "total_stocks", "advances", "declines", "unchanged", "ad_ratio")
    }


def load_universe_bars(cache, symbols: list, start: date, end: date) -> tuple[dict, dict]:
    bars, outcomes = {}, {}
    for i, sym in enumerate(symbols, 1):
        if i % 100 == 0 or i == len(symbols):
            print(f"  Loading history {i}/{len(symbols)}", file=sys.stderr, flush=True)
        try:
            data = cache.stock_bars(sym, start, end)
        except india.SymbolNotFoundError:
            outcomes[sym] = "missing_data"
            continue
        except (india.IndiaDataError, ValueError) as exc:
            outcomes[sym] = "provider_error"
            print(f"  {sym}: {exc}", file=sys.stderr)
            continue
        if not data["bars"]:
            outcomes[sym] = "missing_data"
            continue
        bars[sym] = data["bars"]
        outcomes[sym] = None
    return bars, outcomes


def build_result(rows: list, scored: dict, meta: dict, market_ad: dict | None) -> dict:
    latest = rows[-1]
    history_200 = [r["pct_above_200"] for r in rows]
    return {
        "schema_version": "1.0",
        "skill": "india-market-breadth-analyzer",
        "exchange": india.EXCHANGE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": latest["date"],
        **meta,
        "latest": latest,
        "composite": {
            "composite_score": scored["composite_score"],
            "zone": scored["zone"],
            "guidance": scored["guidance"],
            "missing_components": scored["missing_components"],
        },
        "components": scored["components"],
        "flags": scored["flags"],
        "historical_context": {
            "sessions_with_data": sum(1 for v in history_200 if v is not None),
            "pct_above_200_percentile": percentile_rank(history_200, latest["pct_above_200"]),
            "pct_above_200_max": max((v for v in history_200 if v is not None), default=None),
            "pct_above_200_min": min((v for v in history_200 if v is not None), default=None),
        },
        "nse_market_breadth": market_ad,
        "history": rows[-REPORT_SESSIONS:],
    }


def render_markdown(result: dict) -> str:
    c, latest, flags = result["composite"], result["latest"], result["flags"]
    lines = [
        "# India Market Breadth Report (NSE)",
        "",
        f"**As of:** {result['as_of']}  |  **Universe:** {result['universe']} "
        f"({result['coverage']['loaded']}/{result['coverage']['universe_size']} stocks loaded)",
        "",
        f"## Breadth Score: {c['composite_score']} / 100 — {c['zone']}",
        "",
        c["guidance"],
        "",
        "| Component | Input | Score | Weight |",
        "|---|---:|---:|---:|",
    ]
    for _name, comp in result["components"].items():
        lines.append(
            f"| {comp['description']} | {comp['input']} | {comp['score']} | {comp['weight']:.0%} |"
        )
    lines += [
        "",
        "## Latest Session",
        "",
        f"- Above 20 / 50 / 200-day average: {latest['pct_above_20']}% / {latest['pct_above_50']}% / "
        f"{latest['pct_above_200']}%",
        f"- Advances / declines / unchanged: {latest['advances']} / {latest['declines']} / {latest['unchanged']}",
        f"- New 52-week highs / lows: {latest['new_highs']} / {latest['new_lows']}",
        f"- McClellan oscillator: {latest['mcclellan_oscillator']}; summation: {latest['mcclellan_summation']}",
        f"- %>200DMA percentile within {result['historical_context']['sessions_with_data']} sessions: "
        f"{result['historical_context']['pct_above_200_percentile']}",
    ]
    ad = result.get("nse_market_breadth")
    if ad:
        lines.append(
            f"- NSE-wide ({ad.get('total_stocks')} securities): {ad.get('advances')} advances, "
            f"{ad.get('declines')} declines (A/D ratio {ad.get('ad_ratio')})"
        )
    lines += ["", "## Flags", ""]
    lines.append(
        f"- Deteriorating (short-term breadth below a falling baseline): {'YES' if flags['deteriorating'] else 'no'}"
    )
    lines.append(
        f"- Washout (under 15% of stocks above the 50-day average): {'YES' if flags['washout'] else 'no'}"
    )
    cov = result["coverage"]
    lines += [
        "",
        "## Data Notes",
        "",
        "- Source: NSE bhavcopy (official end-of-day), adjusted for bonuses and splits.",
        f"- Coverage: {cov['loaded']} of {cov['universe_size']} stocks ({cov['verdict']}).",
        "- Universe is today's index membership applied to past sessions (survivorship bias).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list | None = None, *, cache=None, now_fn=None, market_ad_fn=None) -> int:  # noqa: C901
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    try:
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        end = resolve_end(args.as_of, latest, cache._sessions_fn)
        start = end - timedelta(days=HISTORY_CALENDAR_DAYS)
        rows_universe = india.load_universe(args.universe, cache_root)
        calendar_bars = cache.index_bars(args.calendar_index, start, end)["bars"]
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    calendar, _removed = india.drop_muhurat_sessions(calendar_bars)
    calendar_dates = [b["date"] for b in calendar]
    if not calendar_dates or calendar_dates[-1] != end.isoformat():
        print(f"Error: {args.calendar_index} has no bar for {end}", file=sys.stderr)
        return 1

    symbols = [r["symbol"] for r in rows_universe]
    bars, outcomes = load_universe_bars(cache, symbols, start, end)
    loaded = len(bars)
    coverage = {
        "universe_size": len(symbols),
        "loaded": loaded,
        "not_loaded": {k: v for k, v in outcomes.items() if v},
        "verdict": "complete"
        if loaded == len(symbols)
        else ("partial" if loaded else "not_evaluable"),
    }
    if loaded < MIN_COVERAGE * len(symbols):
        print(
            f"Error: only {loaded} of {len(symbols)} stocks loaded; breadth would be unrepresentative",
            file=sys.stderr,
        )
        return 1

    muhurat = {d.isoformat() for d in india.MUHURAT_SESSIONS}
    flags = {
        sym: stock_flags([b for b in series if b["date"] not in muhurat])
        for sym, series in bars.items()
    }
    rows = add_derived_series(daily_breadth(flags, calendar_dates))
    scored = score_latest(rows)

    market_ad = None
    if not args.no_market_ad:
        fetch = market_ad_fn or (lambda day: nse_market_breadth(cache.client, day))
        market_ad = fetch(end)
    result = build_result(
        rows,
        scored,
        {"universe": args.universe, "calendar_index": args.calendar_index, "coverage": coverage},
        market_ad,
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = out / f"india_market_breadth_{stamp}.json"
    md_path = out / f"india_market_breadth_{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    comp = result["composite"]
    print(f"Breadth score {comp['composite_score']} ({comp['zone']}) as of {result['as_of']}")
    print(
        f"  Above 50/200DMA: {result['latest']['pct_above_50']}% / {result['latest']['pct_above_200']}%; "
        f"coverage {loaded}/{len(symbols)}"
    )
    print(f"  JSON report: {json_path}")
    print(f"  Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
