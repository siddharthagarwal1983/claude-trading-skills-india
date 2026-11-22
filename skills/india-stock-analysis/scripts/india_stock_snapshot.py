#!/usr/bin/env python3
"""
India Stock Snapshot - free NSE fact sheet for one stock, input to india-stock-analysis.

Builds the price/technical/dividend/event facts from free official NSE data so
that paid sources (DalalOS company report, ~1 credit) are only needed for
fundamentals, ownership and filings:

  - Price, returns (1m / 3m / 6m / 1y), 20/50/200-day SMAs, 52-week range
  - RSI(14), ATR(14) and ATR %, 50-day average turnover in ₹ crore
  - Relative strength vs NIFTY 500 over 3 and 6 months
  - Trailing-12-month regular and special dividends and yield (bonus/split adjusted)
  - Next results date from the india-earnings-calendar archive, if present
  - NSE industry and Nifty size tier

Usage:
    python3 india_stock_snapshot.py TCS --output-dir reports/
    python3 india_stock_snapshot.py HDFCBANK --benchmark "NIFTY 50"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _india_data as india  # noqa: E402

HISTORY_CALENDAR_DAYS = 420
CRORE = 10_000_000
TIER_INDEXES = (
    ("Large", "nifty100"),
    ("Mid", "niftymidcap150"),
    ("Small", "niftysmallcap250"),
    ("Micro", "niftymicrocap250"),
)
AMOUNT = re.compile(r"(?:rs\.?|re\.?|₹)\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RETURN_WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "1y": 252}


def sma(values: list, window: int):
    return round(sum(values[-window:]) / window, 2) if len(values) >= window else None


def pct_return(closes: list, sessions: int):
    if len(closes) <= sessions:
        return None
    return round((closes[-1] / closes[-1 - sessions] - 1) * 100, 2)


def rsi(closes: list, period: int = 14):
    if len(closes) <= period:
        return None
    gains = [max(0.0, b - a) for a, b in zip(closes, closes[1:])]
    losses = [max(0.0, a - b) for a, b in zip(closes, closes[1:])]
    avg_g, avg_l = sum(gains[:period]) / period, sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + gain) / period
        avg_l = (avg_l * (period - 1) + loss) / period
    return 100.0 if avg_l == 0 else round(100 - 100 / (1 + avg_g / avg_l), 1)


def atr(bars: list, period: int = 14):
    if len(bars) <= period:
        return None
    trs = [
        max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
        for p, b in zip(bars, bars[1:])
    ]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return round(value, 2)


def technicals(bars: list) -> dict:
    closes = [b["close"] for b in bars]
    year = bars[-252:]
    high, low = max(b["high"] for b in year), min(b["low"] for b in year)
    recent = [b for b in bars[-50:] if isinstance(b.get("volume"), (int, float))]
    a = atr(bars)
    price = closes[-1]
    s50, s200 = sma(closes, 50), sma(closes, 200)
    trend = "unknown"
    if s50 and s200:
        trend = "uptrend" if price > s50 > s200 else "downtrend" if price < s50 < s200 else "mixed"
    return {
        "price": round(price, 2),
        "price_date": bars[-1]["date"],
        "returns_pct": {k: pct_return(closes, n) for k, n in RETURN_WINDOWS.items()},
        "sma20": sma(closes, 20),
        "sma50": s50,
        "sma200": s200,
        "trend": trend,
        "high_52w": round(high, 2),
        "low_52w": round(low, 2),
        "pct_below_52w_high": round((1 - price / high) * 100, 1),
        "pct_above_52w_low": round((price / low - 1) * 100, 1),
        "rsi14": rsi(closes),
        "atr14": a,
        "atr_pct": round(a / price * 100, 2) if a else None,
        "avg_turnover_cr_50d": round(
            sum(b["close"] * b["volume"] for b in recent) / len(recent) / CRORE, 2
        )
        if recent
        else None,
    }


def relative_strength(stock: list, bench: list) -> dict:
    """Stock return minus benchmark return over 3 and 6 months, percentage points."""
    by_date = {b["date"]: b["close"] for b in bench}
    common = [b for b in stock if b["date"] in by_date]
    out = {}
    for label, n in (("3m", 63), ("6m", 126)):
        if len(common) > n:
            s = common[-1]["close"] / common[-1 - n]["close"] - 1
            m = by_date[common[-1]["date"]] / by_date[common[-1 - n]["date"]] - 1
            out[label] = round((s - m) * 100, 2)
        else:
            out[label] = None
    return out


def ttm_dividends(actions: list, as_of: date) -> dict:
    """Trailing-12-month regular and special dividends restated for later bonuses/splits."""
    factors = [
        (a["ex_date"], a["factor"])
        for a in actions
        if a["action_type"] != "DIVIDEND"
        and isinstance(a.get("factor"), (int, float))
        and 0 < a["factor"] != 1
    ]
    start = (as_of - timedelta(days=365)).isoformat()
    regular = special = 0.0
    for a in actions:
        if a["action_type"] != "DIVIDEND" or not (start < a["ex_date"] <= as_of.isoformat()):
            continue
        scale = 1.0
        for d, f in factors:
            if d > a["ex_date"]:
                scale *= f
        for part in re.split(r"/", a.get("purpose", "")):
            m = AMOUNT.search(part)
            if m:
                value = float(m.group(1)) * scale
                if "special" in part.lower():
                    special += value
                else:
                    regular += value
    return {"ttm_regular": round(regular, 2), "ttm_special": round(special, 2)}


def next_results(cache_root: Path, symbol: str, today: date) -> dict | None:
    path = Path(cache_root) / "results_calendar.json"
    try:
        events = json.loads(path.read_text(encoding="utf-8")).get("events") or []
    except (OSError, ValueError):
        return None
    upcoming = sorted(
        (e for e in events if e.get("symbol") == symbol and e.get("date", "") >= today.isoformat()),
        key=lambda e: e["date"],
    )
    return (
        {"date": upcoming[0]["date"], "quarter": upcoming[0].get("quarter")} if upcoming else None
    )


def classification(cache_root: Path, symbol: str, loader) -> dict:
    out = {"industry": "Unknown", "company": "", "tier": "Unknown"}
    try:
        for row in loader("niftytotalmarket", cache_root):
            if row["symbol"] == symbol:
                out.update(
                    industry=row.get("industry") or "Unknown", company=row.get("company", "")
                )
                break
    except (india.IndiaDataError, ValueError, OSError):
        pass
    for tier, key in TIER_INDEXES:
        try:
            if any(r["symbol"] == symbol for r in loader(key, cache_root)):
                out["tier"] = tier
                break
        except (india.IndiaDataError, ValueError, OSError):
            continue
    return out


def render_markdown(s: dict) -> str:
    t, d = s["technicals"], s["dividends"]
    r = t["returns_pct"]
    nr = s.get("next_results")
    lines = [
        f"# {s['symbol']} — NSE Fact Sheet",
        "",
        f"**{s['classification']['company'] or s['symbol']}** | {s['classification']['industry']} | "
        f"Tier: {s['classification']['tier']} | As of {t['price_date']}",
        "",
        "## Price and Trend",
        f"- Close ₹{t['price']:,.2f}; trend: {t['trend']} (SMA50 {t['sma50']}, SMA200 {t['sma200']})",
        f"- Returns: 1m {r['1m']}%, 3m {r['3m']}%, 6m {r['6m']}%, 1y {r['1y']}%",
        f"- 52-week range ₹{t['low_52w']:,.2f} – ₹{t['high_52w']:,.2f} ({t['pct_below_52w_high']}% below the high)",
        f"- RSI(14) {t['rsi14']}; ATR(14) ₹{t['atr14']} ({t['atr_pct']}% of price)",
        f"- 50-day average turnover ₹{t['avg_turnover_cr_50d']} crore",
        f"- Relative strength vs {s['benchmark']}: 3m {s['relative_strength']['3m']} pp, 6m {s['relative_strength']['6m']} pp",
        "",
        "## Dividends (trailing 12 months, per today's share)",
        f"- Regular ₹{d['ttm_regular']} ({d['regular_yield_pct']}%); special ₹{d['ttm_special']}",
        "",
        "## Events",
        f"- Next results: {nr['date'] + (' (' + nr['quarter'] + ')' if nr.get('quarter') else '') if nr else 'not in the local calendar archive'}",
        "",
        "## Data Notes",
        "- Source: NSE bhavcopy and corporate actions (free, official), adjusted for bonuses and splits.",
    ]
    if s["warnings"]:
        lines += [
            f"- Warning: {w['change_pct']}% move on {w['date']} — {w['note']}"
            for w in s["warnings"]
        ]
    lines += [
        "- Fundamentals, ownership and filings are not in this sheet; see the india-stock-analysis workflow.",
        "",
    ]
    return "\n".join(lines)


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Free NSE fact sheet for one stock")
    p.add_argument("symbol")
    p.add_argument("--benchmark", default="NIFTY 500")
    p.add_argument("--as-of", help="YYYY-MM-DD; default: latest completed NSE session")
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--output-dir", default="reports/")
    return p.parse_args(argv)


def main(argv: list | None = None, *, cache=None, now_fn=None, loader=None) -> int:
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    loader = loader or india.load_universe
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    try:
        symbol = india.safe_symbol(args.symbol)
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        as_of = latest
        if args.as_of:
            requested = date.fromisoformat(args.as_of)
            if requested > latest:
                raise ValueError(
                    f"--as-of {args.as_of} is after the latest completed NSE session {latest}"
                )
            as_of = cache._sessions_fn(requested - timedelta(days=21), requested)[-1]
        start = as_of - timedelta(days=HISTORY_CALENDAR_DAYS)
        data = cache.stock_bars(symbol, start, as_of)
        bench = cache.index_bars(args.benchmark, start, as_of)["bars"]
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError, IndexError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    bars = [b for b in data["bars"] if isinstance(b.get("volume"), (int, float))]
    if len(bars) < 30:
        print(f"Error: only {len(bars)} sessions of history for {symbol}", file=sys.stderr)
        return 1
    tech = technicals(bars)
    divs = ttm_dividends(data.get("actions") or [], as_of)
    divs["regular_yield_pct"] = round(divs["ttm_regular"] / tech["price"] * 100, 2)
    snapshot = {
        "schema_version": "1.0",
        "skill": "india-stock-analysis",
        "symbol": symbol,
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "generated_at": now_fn().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "benchmark": args.benchmark,
        "classification": classification(cache_root, symbol, loader),
        "technicals": tech,
        "relative_strength": relative_strength(bars, bench),
        "dividends": divs,
        "next_results": next_results(cache_root, symbol, now_fn().astimezone(india.IST).date()),
        "warnings": data.get("warnings") or [],
    }
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = out / f"india_stock_snapshot_{symbol}_{stamp}.json"
    md_path = out / f"india_stock_snapshot_{symbol}_{stamp}.md"
    json_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(snapshot), encoding="utf-8")
    print(
        f"{symbol}: ₹{tech['price']:,.2f} {tech['trend']}, RSI {tech['rsi14']}, "
        f"RS vs {args.benchmark} 6m {snapshot['relative_strength']['6m']} pp"
    )
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
