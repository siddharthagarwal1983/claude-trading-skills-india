#!/usr/bin/env python3
"""
India Dividend Screener - dividend yield, consistency and growth for NSE stocks.

Candidates come from one of:
  --screen-json  a saved DalalOS ``screen_stocks`` reply (Claude runs one
                 universe-wide screen in a session, ~1 credit, e.g. NIFTY 500 with
                 dividend_yield_min / pe_max / roe_min); its P/E, P/B, ROE and
                 debt/equity are carried into the report
  --universe     a Nifty index screened on free data only (no valuation score)
  --symbols      explicit NSE symbols

For each candidate the script fetches five years of NSE corporate actions
(free), parses dividend amounts, restates them for later bonuses/splits,
groups them by Indian fiscal year (April-March) and scores yield, consistency,
growth, valuation (when known) and an RSI pullback. Prices come from the NSE
cache.

Usage:
    python3 screen_india_dividends.py --screen-json reports/dalalos_dividend_screen.json --output-dir reports/
    python3 screen_india_dividends.py --universe nifty100 --min-yield 1.5 --output-dir reports/
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
from dividend_calculator import adjusted_dividends, dividend_metrics, rsi, score  # noqa: E402

DIVIDEND_YEARS = 5
ACTIONS_TTL_DAYS = 7
PRICE_CALENDAR_DAYS = 400
FUNDAMENTAL_FIELDS = (
    "name",
    "sector",
    "company_type",
    "market_cap",
    "pe",
    "pb",
    "roe",
    "debt_to_equity",
    "dividend_yield",
)


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="India Dividend Screener (NSE)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--screen-json", help="Saved DalalOS screen_stocks reply (path or glob)")
    src.add_argument(
        "--universe", choices=sorted(india.UNIVERSE_FILES), help="Nifty index (free data only)"
    )
    src.add_argument("--symbols", nargs="+", help="Explicit NSE symbols")
    p.add_argument(
        "--min-yield", type=float, default=2.0, help="Minimum regular TTM yield %% (default 2)"
    )
    p.add_argument(
        "--min-paying-years", type=int, default=3, help="Minimum complete FYs with a dividend"
    )
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--as-of", help="YYYY-MM-DD; default: latest completed NSE session")
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--output-dir", default="reports/")
    return p.parse_args(argv)


def load_screen(pattern: str) -> tuple[dict, str]:
    """{symbol: fundamentals} from a DalalOS screen_stocks reply."""
    matches = sorted(glob.glob(pattern)) if any(c in pattern for c in "*?[") else [pattern]
    if not matches or not Path(matches[-1]).is_file():
        raise ValueError(f"no screen file found for {pattern}")
    path = matches[-1]
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read screen file {path}: {exc}") from exc
    rows = (data.get("data") or data).get("results") if isinstance(data, dict) else data
    out = {}
    for row in rows or []:
        sym = row.get("nse_symbol") or row.get("symbol")
        if not sym:
            continue
        try:
            out[india.safe_symbol(sym)] = {k: row.get(k) for k in FUNDAMENTAL_FIELDS}
        except ValueError:
            continue
    if not out:
        raise ValueError(f"{path} has no NSE rows (expected DalalOS screen_stocks results)")
    return out, path


def cached_actions(
    client, cache_root: Path, symbol: str, start: date, end: date, now: datetime
) -> list:
    """Five years of NSE corporate actions, cached for a week (free source)."""
    path = Path(cache_root) / "dividends" / f"{symbol}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(entry["fetched_at"])
        if (
            now - fetched < timedelta(days=ACTIONS_TTL_DAYS)
            and entry.get("start") <= start.isoformat()
        ):
            return entry["actions"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    actions = india.fetch_corporate_actions(client, symbol, start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": now.isoformat(), "start": start.isoformat(), "actions": actions}),
        encoding="utf-8",
    )
    return actions


def analyze(symbol, actions, bars, as_of, history_start, fundamentals) -> dict:
    closes = [b["close"] for b in bars]
    price = closes[-1]
    events = adjusted_dividends(actions)
    metrics = dividend_metrics(events, price, as_of, history_start)
    rsi_value = rsi(closes)
    year = bars[-252:]
    high_52w = max(b["high"] for b in year)
    pe = (fundamentals or {}).get("pe")
    scored = score(metrics, rsi_value, pe)
    return {
        "symbol": symbol,
        "price": round(price, 2),
        "price_date": bars[-1]["date"],
        **metrics,
        "rsi_14": rsi_value,
        "pct_below_52w_high": round((1 - price / high_52w) * 100, 1) if high_52w else None,
        **scored,
        "fundamentals": fundamentals or {},
        "recent_dividends": events[-6:],
    }


def render_markdown(result: dict) -> str:
    lines = [
        "# India Dividend Screener (NSE)",
        "",
        f"**As of:** {result['as_of']}  |  **Source:** {result['candidate_source']}  |  "
        f"**Passed:** {len(result['results'])} of {result['candidates']}",
        f"**Filters:** regular TTM yield ≥ {result['filters']['min_yield']}%, dividend in ≥ "
        f"{result['filters']['min_paying_years']} complete fiscal years",
        "",
        "| # | Symbol | Grade | Score | Price ₹ | Yield % | Special % | FYs paid | CAGR % | Cuts | RSI | P/E | ROE |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(result["results"], 1):
        f = r["fundamentals"]
        roe = f"{f['roe'] * 100:.0f}%" if isinstance(f.get("roe"), (int, float)) else "—"
        pe = f"{f['pe']:.1f}" if isinstance(f.get("pe"), (int, float)) else "—"
        special = (r["total_yield_pct"] or 0) - (r["regular_yield_pct"] or 0)
        lines.append(
            f"| {i} | {r['symbol']} | {r['grade']} | {r['score']} | {r['price']:,.2f} | {r['regular_yield_pct']} | "
            f"{special:.2f} | {r['paying_years']}/{r['complete_fiscal_years']} | "
            f"{r['dividend_cagr_pct'] if r['dividend_cagr_pct'] is not None else '—'} | {r['dividend_cuts']} | "
            f"{r['rsi_14'] if r['rsi_14'] is not None else '—'} | {pe} | {roe} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Dividends are restated per today's share for later bonuses and splits; fiscal years run April-March.",
        "- Yield uses regular dividends over the last 12 months; special dividends are shown separately.",
        "- Dividends are taxed at your income-tax slab rate in India and attract TDS above a per-company annual "
        "threshold; see references/india_dividend_notes.md.",
    ]
    if result["skipped"]:
        lines += ["", "## Not Evaluated", ""]
        lines += [
            f"- {reason}: {', '.join(syms)}" for reason, syms in sorted(result["skipped"].items())
        ]
    lines.append("")
    return "\n".join(lines)


def main(
    argv: list | None = None, *, cache=None, client=None, now_fn=None, universe_loader=None
) -> int:  # noqa: C901
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    loader = universe_loader or india.load_universe
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    client = client or cache.client
    try:
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        as_of = latest
        if args.as_of:
            requested = date.fromisoformat(args.as_of)
            if requested > latest:
                raise ValueError(
                    f"--as-of {args.as_of} is after the latest completed NSE session {latest}"
                )
            as_of = cache._sessions_fn(requested - timedelta(days=21), requested)[-1]
        fundamentals: dict = {}
        if args.screen_json:
            fundamentals, source = load_screen(args.screen_json)
            symbols = list(fundamentals)
            source = f"DalalOS screen ({Path(source).name})"
        elif args.universe:
            symbols = [r["symbol"] for r in loader(args.universe, cache_root)]
            source = f"{args.universe} (free data)"
        else:
            symbols = list(dict.fromkeys(india.safe_symbol(s) for s in args.symbols))
            source = "explicit symbols"
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError, IndexError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    history_start = as_of.replace(year=as_of.year - DIVIDEND_YEARS)
    skipped: dict = {}
    results = []
    now = now_fn()
    for i, sym in enumerate(symbols, 1):
        if i % 50 == 0 or i == len(symbols):
            print(f"  {i}/{len(symbols)}", file=sys.stderr, flush=True)
        try:
            bars = cache.stock_bars(sym, as_of - timedelta(days=PRICE_CALENDAR_DAYS), as_of)["bars"]
            actions = cached_actions(client, cache_root, sym, history_start, as_of, now)
        except (india.IndiaDataError, ValueError) as exc:
            skipped.setdefault("data_unavailable", []).append(sym)
            print(f"  {sym}: {exc}", file=sys.stderr)
            continue
        if len(bars) < 30:
            skipped.setdefault("insufficient_price_history", []).append(sym)
            continue
        row = analyze(sym, actions, bars, as_of, history_start, fundamentals.get(sym))
        if row["regular_yield_pct"] is None or row["regular_yield_pct"] < args.min_yield:
            skipped.setdefault("below_min_yield", []).append(sym)
            continue
        if row["paying_years"] < min(args.min_paying_years, row["complete_fiscal_years"]):
            skipped.setdefault("inconsistent_payer", []).append(sym)
            continue
        results.append(row)
    results.sort(key=lambda r: (r["score"], r["regular_yield_pct"]), reverse=True)

    result = {
        "schema_version": "1.0",
        "skill": "india-dividend-screener",
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "candidate_source": source,
        "candidates": len(symbols),
        "filters": {"min_yield": args.min_yield, "min_paying_years": args.min_paying_years},
        "dividend_history_start": history_start.isoformat(),
        "skipped": {k: sorted(v) for k, v in skipped.items()},
        "results": results[: args.top],
    }
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = out / f"india_dividend_screener_{stamp}.json"
    md_path = out / f"india_dividend_screener_{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(f"{len(results)} of {len(symbols)} candidates passed (as of {as_of})")
    for r in results[:5]:
        print(
            f"  {r['symbol']:12} Grade {r['grade']} ({r['score']}) yield {r['regular_yield_pct']}% "
            f"CAGR {r['dividend_cagr_pct']}%"
        )
    print(f"  JSON report: {json_path}")
    print(f"  Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
