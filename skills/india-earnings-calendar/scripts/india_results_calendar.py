#!/usr/bin/env python3
"""
India Earnings Calendar - upcoming NSE results with a local, credit-free archive.

Scripts cannot call the Drishti or DalalOS connectors, so Claude fetches the
results calendar inside a session and saves the reply; this script does the rest:

  ingest    Merge saved results-calendar JSON (Drishti get_upcoming_earnings,
            free; or DalalOS get_upcoming_results / get_market_calendar) into a
            de-duplicated local archive. Past dates in the archive later feed
            india-earnings-trade-analyzer at no cost.
  upcoming  Report results due in the next N days for a Nifty universe, with
            NSE industry, size tier (Nifty 100 / Midcap 150 / Smallcap 250 /
            Microcap 250), last close and 50-day turnover, and flag holdings
            that report inside the window.
  symbols   Print a universe as a comma-separated list for filtering the
            Drishti call (fewer rows, still free).

Usage:
    python3 india_results_calendar.py symbols --universe nifty500
    python3 india_results_calendar.py ingest --events reports/drishti_upcoming.json
    python3 india_results_calendar.py upcoming --days 14 --holdings TCS,INFY --output-dir reports/
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _india_data as india  # noqa: E402

TIER_INDEXES = (
    ("Large", "nifty100"),
    ("Mid", "niftymidcap150"),
    ("Small", "niftysmallcap250"),
    ("Micro", "niftymicrocap250"),
)
ARCHIVE_NAME = "results_calendar.json"
CRORE = 10_000_000


# --------------------------------------------------------------------------- archive


def archive_path(cache_root: Path) -> Path:
    return Path(cache_root) / ARCHIVE_NAME


def load_archive(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise india.IndiaDataError(f"corrupt results archive {path}: {exc}") from exc
    return list(data.get("events") or [])


def merge_events(existing: list, new: list, seen_at: str) -> tuple[list, int]:
    """Add new (symbol, date) events; keep the first sighting, fill missing quarter/company."""
    by_key = {(e["symbol"], e["date"]): dict(e) for e in existing}
    added = 0
    for event in new:
        key = (event["symbol"], event["date"])
        if key in by_key:
            current = by_key[key]
            for field in ("quarter", "company"):
                if not current.get(field) and event.get(field):
                    current[field] = event[field]
            continue
        by_key[key] = {**event, "first_seen": seen_at}
        added += 1
    merged = [by_key[k] for k in sorted(by_key, key=lambda k: (k[1], k[0]))]
    return merged, added


def save_archive(path: Path, events: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"schema_version": "1.0", "events": events}, indent=1), encoding="utf-8"
    )
    tmp.replace(path)


def read_event_files(patterns: list) -> tuple[list, list]:
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


# --------------------------------------------------------------------------- enrichment


def tier_map(cache_root: Path, loader) -> dict:
    tiers: dict = {}
    for tier, key in TIER_INDEXES:
        try:
            for row in loader(key, cache_root):
                tiers.setdefault(row["symbol"], tier)
        except (india.IndiaDataError, ValueError, OSError):
            continue
    return tiers


def price_context(cache, symbol: str, end: date) -> dict:
    """Last close and 50-session turnover from the NSE cache; empty on failure."""
    try:
        bars = cache.stock_bars(symbol, end - timedelta(days=110), end)["bars"]
    except (india.IndiaDataError, ValueError):
        return {}
    recent = [b for b in bars if isinstance(b.get("volume"), (int, float))][-50:]
    if not recent:
        return {}
    turnover = sum(b["close"] * b["volume"] for b in recent) / len(recent) / CRORE
    return {
        "last_close": round(recent[-1]["close"], 2),
        "avg_turnover_cr": round(turnover, 2),
        "price_date": recent[-1]["date"],
    }


def build_upcoming(
    events: list,
    start: date,
    days: int,
    universe: dict | None,
    tiers: dict,
    holdings: set,
    prices: dict,
) -> dict:
    end = start + timedelta(days=days)
    rows = []
    for e in events:
        if not (start.isoformat() <= e["date"] <= end.isoformat()):
            continue
        if universe is not None and e["symbol"] not in universe:
            continue
        ref = (universe or {}).get(e["symbol"], {})
        rows.append(
            {
                **{k: e.get(k) for k in ("symbol", "date", "quarter", "source")},
                "company": e.get("company") or ref.get("company", ""),
                "industry": ref.get("industry") or "Unknown",
                "tier": tiers.get(e["symbol"], "Unknown"),
                "held": e["symbol"] in holdings,
                "days_until": (date.fromisoformat(e["date"]) - start).days,
                **prices.get(e["symbol"], {}),
            }
        )
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "count": len(rows),
        "holdings_reporting": sorted(r["symbol"] for r in rows if r["held"]),
        "by_tier": {
            t: sum(1 for r in rows if r["tier"] == t)
            for t in ("Large", "Mid", "Small", "Micro", "Unknown")
        },
        "by_date": {
            d: sorted(v, key=lambda r: (r["tier"], r["symbol"])) for d, v in sorted(by_date.items())
        },
    }


def render_markdown(result: dict) -> str:
    w = result["window"]
    lines = [
        "# India Earnings Calendar (NSE)",
        "",
        f"**Window:** {w['start']} to {w['end']} ({w['days']} days)  |  **Universe:** {result['universe']}",
        f"**Results due:** {result['count']}  |  **Archive events:** {result['archive_events']}",
        "",
    ]
    if result["holdings_reporting"]:
        lines += [
            "## Holdings Reporting In This Window",
            "",
            "Results can gap a stock through a stop. Decide before the date whether to hold, trim or exit.",
            "",
            *[f"- **{s}**" for s in result["holdings_reporting"]],
            "",
        ]
    lines += ["## By Size Tier", "", "| Tier | Count |", "|---|---:|"]
    lines += [f"| {t} | {n} |" for t, n in result["by_tier"].items() if n]
    for day, rows in result["by_date"].items():
        weekday = date.fromisoformat(day).strftime("%A")
        lines += [
            "",
            f"## {day} ({weekday})",
            "",
            "| Symbol | Company | Tier | Industry | Quarter | Close ₹ | Turnover ₹cr | Held |",
            "|---|---|---|---|---|---:|---:|---|",
        ]
        for r in rows:
            lines.append(
                f"| {r['symbol']} | {r['company']} | {r['tier']} | {r['industry']} | {r.get('quarter') or '—'} | "
                f"{r.get('last_close', '—')} | {r.get('avg_turnover_cr', '—')} | {'YES' if r['held'] else ''} |"
            )
    lines += [
        "",
        "*Dates are board-meeting dates for results as disclosed to the exchanges; companies can reschedule.*",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="India Earnings Calendar (NSE results dates)")
    p.add_argument(
        "--cache-dir", type=Path, default=None, help="India data cache (archive lives here)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest", help="Merge saved results-calendar JSON into the archive")
    ing.add_argument("--events", nargs="+", required=True, help="JSON file(s) or globs")
    up = sub.add_parser("upcoming", help="Results due in the next N days")
    up.add_argument("--days", type=int, default=14)
    up.add_argument("--universe", default="nifty500", help="Nifty universe or 'all'")
    up.add_argument("--holdings", default="", help="Comma-separated NSE symbols you hold")
    up.add_argument("--events", nargs="*", default=[], help="Also ingest these files first")
    up.add_argument("--no-prices", action="store_true", help="Skip close/turnover enrichment")
    up.add_argument("--as-of", help="YYYY-MM-DD start of the window (default: today IST)")
    up.add_argument("--output-dir", default="reports/")
    sym = sub.add_parser("symbols", help="Print a Nifty universe as a comma-separated list")
    sym.add_argument("--universe", default="nifty500", choices=sorted(india.UNIVERSE_FILES))
    args = p.parse_args(argv)
    if args.command == "upcoming":
        if args.days < 0:
            p.error("--days must be >= 0")
        if args.universe != "all" and args.universe not in india.UNIVERSE_FILES:
            p.error(f"--universe must be 'all' or one of {', '.join(sorted(india.UNIVERSE_FILES))}")
    return args


def _ingest(paths: list, archive: Path, now: datetime) -> tuple[list, int, list]:
    new, files = read_event_files(paths)
    merged, added = merge_events(load_archive(archive), new, now.isoformat(timespec="seconds"))
    save_archive(archive, merged)
    return merged, added, files


def main(argv: list | None = None, *, cache=None, now_fn=None, loader=None) -> int:  # noqa: C901
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    archive = archive_path(cache_root)
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    loader = loader or india.load_universe
    try:
        if args.command == "symbols":
            print(",".join(r["symbol"] for r in loader(args.universe, cache_root)))
            return 0
        if args.command == "ingest":
            merged, added, files = _ingest(args.events, archive, now_fn())
            print(
                f"Ingested {len(files)} file(s): {added} new events; archive now {len(merged)} events ({archive})"
            )
            return 0

        events = load_archive(archive)
        if args.events:
            events, added, _files = _ingest(args.events, archive, now_fn())
            print(f"Ingested {added} new events", file=sys.stderr)
        today = now_fn().astimezone(india.IST).date()
        start = date.fromisoformat(args.as_of) if args.as_of else today
        universe = None
        if args.universe != "all":
            universe = {r["symbol"]: r for r in loader(args.universe, cache_root)}
        tiers = tier_map(cache_root, loader)
        holdings = {india.safe_symbol(s) for s in args.holdings.split(",") if s.strip()}
        window_symbols = {
            e["symbol"]
            for e in events
            if start.isoformat() <= e["date"] <= (start + timedelta(days=args.days)).isoformat()
            and (universe is None or e["symbol"] in universe)
        }
        prices = {}
        if not args.no_prices and window_symbols:
            if cache is None:
                cache = india.BarCache(cache_root, now_fn=now_fn)
            latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
            for sym in sorted(window_symbols):
                prices[sym] = price_context(cache, sym, min(latest, start))
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    result = build_upcoming(events, start, args.days, universe, tiers, holdings, prices)
    result.update(
        {
            "schema_version": "1.0",
            "skill": "india-earnings-calendar",
            "exchange": india.EXCHANGE,
            "universe": args.universe,
            "archive_events": len(events),
            "generated_at": now_fn().isoformat(timespec="seconds"),
        }
    )
    if not events:
        print(
            "Warning: the results archive is empty; ingest a Drishti or DalalOS calendar file first",
            file=sys.stderr,
        )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = out / f"india_earnings_calendar_{stamp}.json"
    md_path = out / f"india_earnings_calendar_{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(
        f"{result['count']} results due {result['window']['start']}..{result['window']['end']} "
        f"in {args.universe}; holdings reporting: {', '.join(result['holdings_reporting']) or 'none'}"
    )
    print(f"  JSON report: {json_path}")
    print(f"  Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
