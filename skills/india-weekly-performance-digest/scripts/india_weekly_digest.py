#!/usr/bin/env python3
"""India Weekly Performance Digest.

Summarises the paper trades closed during an NSE week from india-paper-ledger
state (``state/india-paper/``): win rate, expectancy, profit factor, R-multiple
and pattern buckets computed exactly as weekly-performance-digest does (see
``digest_core.py``), plus what an Indian delivery trader needs beside them:

* rupee amounts with Indian digit grouping, IST day boundaries, NSE sessions;
* raw fill prices, charges and gross-vs-net P&L from the ledger journal;
* dividends credited while a position was held (thesis P&L excludes them);
* NIFTY 50 change over the week and over each trade's holding sessions;
* MAE/MFE from official NSE closes when the thesis has none (it never does for
  paper trades: trader-memory-core computes them only from FMP);
* the screener that produced each trade, followed through the planner report.

Thesis files are only read. Network use is limited to free NSE end-of-day bars
through the shared bar cache; ``--no-fetch`` reads the cache only.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by the dependency smoke check
    print(
        "Error: missing dependency PyYAML. Install the runtime dependencies: "
        "pip install -r skills/india-weekly-performance-digest/requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import digest_core as core  # noqa: E402
from _india_data import (  # noqa: E402
    CURRENCY,
    EXCHANGE,
    IST,
    TIMEZONE_NAME,
    BarCache,
    CalendarDependencyMissingError,
    IndiaDataError,
    _slug,
    latest_completed_session,
    nse_sessions,
    safe_symbol,
)

SKILL_ID = "india-weekly-performance-digest"
SCHEMA_VERSION = "1.0"
REPORT_TYPE = "india_weekly_performance_digest"
DEFAULT_BENCHMARK = "NIFTY 50"
LEDGER_SKILL = "india-paper-ledger"
CHARGES_DRAG_WARN = 0.10

# Report file prefixes and the skill that writes them (longest prefix wins).
REPORT_PREFIXES = {
    "india_vcp_screener_": "india-vcp-screener",
    "india_stockbee_momentum_burst_": "india-stockbee-momentum-burst-screener",
    "india_earnings_trade_": "india-earnings-trade-analyzer",
    "india_dividend_screener_": "india-dividend-screener",
    "india_breakout_trade_plan_": "india-breakout-trade-planner",
    "india_position_sizer_": "india-position-sizer",
    "india_downtrend_analysis_": "india-downtrend-duration-analyzer",
    "india_stock_snapshot_": "india-stock-analysis",
}

logger = logging.getLogger(SKILL_ID)

__all__ = [
    "IndiaDataError",
    "build_digest",
    "default_window",
    "ist_date",
    "main",
    "render_markdown",
    "signal_source",
    "week_window",
]


# --------------------------------------------------------------------------- dates


def ist_date(value: Any) -> date | None:
    """Calendar date in IST of a YYYY-MM-DD or ISO-8601 timestamp (UTC if naive)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def week_window(day: date, sessions_fn: Callable) -> dict:
    """Monday-to-Sunday window containing ``day`` and its NSE sessions."""
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return {"from": start, "to": end, "sessions": list(sessions_fn(start, end))}


def default_window(now: datetime, sessions_fn: Callable) -> dict:
    """The week of the latest NSE session whose bhavcopy is final."""
    latest = latest_completed_session(now, sessions_fn=sessions_fn)
    return week_window(latest, sessions_fn)


# --------------------------------------------------------------------------- state


def _read_yaml(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Skipping %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def resolve_state(state_dir: Path) -> tuple:
    """(theses_dir, ledger_dir or None) for a ledger dir or a plain theses dir."""
    state_dir = Path(state_dir)
    if (state_dir / "theses").is_dir():
        return state_dir / "theses", state_dir
    return state_dir, None


def load_theses(theses_dir: Path) -> list:
    out = []
    for path in sorted(Path(theses_dir).glob("th_*.yaml")):
        thesis = _read_yaml(path)
        if thesis is not None:
            out.append(thesis)
    return out


def load_journal(ledger_dir: Path | None) -> list:
    if ledger_dir is None:
        return []
    path = ledger_dir / "journal.jsonl"
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    logger.warning("Skipping unreadable journal line in %s", path)
    return entries


def _in_window(day: date | None, start: date, end: date) -> bool:
    return day is not None and start <= day <= end


def nse_symbol(thesis: dict) -> str:
    raw = core._get(thesis, "origin", "raw_provenance", "nse_symbol")
    return raw if isinstance(raw, str) and raw else str(thesis.get("ticker") or "?")


# --------------------------------------------------------------------------- signal source


def _report_skill(path: Path, data: Any) -> str | None:
    if isinstance(data, dict):
        for value in (
            data.get("skill"),
            core._get(data, "metadata", "skill"),
            core._get(data, "metadata", "generator"),
        ):
            if isinstance(value, str) and value:
                return value
    name = path.name
    for prefix in sorted(REPORT_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix):
            return REPORT_PREFIXES[prefix]
    return None


def _find(path_text: str, roots: list) -> Path | None:
    path = Path(path_text)
    candidates = [path] if path.is_absolute() else [Path.cwd() / path] + [r / path for r in roots]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def signal_source(thesis: dict, search_roots: list | None = None) -> dict:
    """Which skill produced the trade idea, following plan -> screener links.

    Paper theses always carry ``origin.skill = india-paper-ledger``; the real
    source is the report the order was placed from (``origin.output_file``).
    """
    roots = [Path(r) for r in (search_roots or [])]
    origin = thesis.get("origin")
    if not isinstance(origin, dict):
        return {"source": "unknown", "chain": []}
    skill = origin.get("skill")
    output = origin.get("output_file")
    if skill and skill != LEDGER_SKILL:
        return {"source": skill, "chain": [skill]}
    if not isinstance(output, str) or not output or Path(output).name == "orders.json":
        return {"source": "manual", "chain": []}
    chain: list = []
    current: str | None = output
    for _ in range(4):  # plan -> screener is two hops; stop on loops
        if not current:
            break
        found = _find(current, roots)
        data = _read_json(found) if found else None
        name = _report_skill(found or Path(current), data)
        if name is None or name in chain:
            break
        chain.insert(0, name)
        current = core._get(data, "input_metadata", "source_file") if data else None
    if not chain:
        return {"source": "manual", "chain": []}
    return {"source": chain[0], "chain": chain}


# --------------------------------------------------------------------------- per-trade detail


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _last_close_on_or_before(bars: list, day: date) -> tuple:
    best = None
    for bar in bars:
        if bar.get("date") and bar["date"] <= day.isoformat():
            best = bar
    return (best["date"], best["close"]) if best else (None, None)


def _excursions(entry_price: float, bars: list) -> tuple:
    closes = [b["close"] for b in bars if isinstance(b.get("close"), (int, float))]
    if not closes or not entry_price:
        return None, None
    mae = (min(closes) - entry_price) / entry_price * 100
    mfe = (max(closes) - entry_price) / entry_price * 100
    return round(mae, 2), round(mfe, 2)


class _Data:
    """Bar access with per-run memoisation and error notes."""

    def __init__(self, bars_fn, index_fn, notes: list):
        self.bars_fn = bars_fn
        self.index_fn = index_fn
        self.notes = notes
        self._index: dict = {}

    def stock(self, symbol: str, start: date, end: date) -> list | None:
        if self.bars_fn is None:
            return None
        try:
            return list(self.bars_fn(symbol, start, end))
        except (IndiaDataError, ValueError, OSError) as exc:
            self.notes.append(f"{symbol}: NSE bars unavailable ({exc}); MAE/MFE skipped")
            return None

    def index(self, name: str, start: date, end: date) -> list | None:
        key = (name, start, end)
        if key in self._index:
            return self._index[key]
        bars = None
        if self.index_fn is not None:
            try:
                bars = list(self.index_fn(name, start, end))
            except (IndiaDataError, ValueError, OSError) as exc:
                self.notes.append(f"{name}: index bars unavailable ({exc}); benchmark skipped")
        self._index[key] = bars
        return bars


def trade_detail(
    thesis: dict,
    fills: list,
    dividends: list,
    *,
    source: dict,
    data: _Data,
    benchmark: str,
    benchmark_bars: list | None,
    sessions_fn: Callable,
) -> dict:
    buys = [f for f in fills if f.get("side") == "BUY"]
    sells = [f for f in fills if f.get("side") == "SELL"]

    def vwap(rows):
        qty = sum(r["qty"] for r in rows)
        return round(sum(r["price"] * r["qty"] for r in rows) / qty, 4) if qty else None

    entry_session = (
        min(date.fromisoformat(f["session"]) for f in buys)
        if buys
        else ist_date(core._get(thesis, "entry", "actual_date"))
    )
    exit_session = (
        max(date.fromisoformat(f["session"]) for f in sells)
        if sells
        else ist_date(core._get(thesis, "exit", "actual_date"))
    )
    qty = sum(f["qty"] for f in buys) if buys else core._get(thesis, "position", "shares")
    entry_price = vwap(buys) if buys else core._get(thesis, "entry", "actual_price")
    exit_price = vwap(sells) if sells else core._get(thesis, "exit", "actual_price")
    charges = gross = None
    if fills:
        charges = round(sum((f.get("charges") or {}).get("total", 0.0) for f in fills), 2)
        gross = round(
            sum(f["price"] * f["qty"] for f in sells) - sum(f["price"] * f["qty"] for f in buys), 2
        )
    net, pnl_pct = core.calculate_pnl(thesis)
    divs = round(sum(d.get("amount", 0.0) for d in dividends), 2)

    holding_sessions = None
    if entry_session and exit_session:
        try:
            holding_sessions = len(
                [d for d in sessions_fn(entry_session, exit_session) if d > entry_session]
            )
        except (CalendarDependencyMissingError, IndiaDataError):
            holding_sessions = None

    bench_pct = None
    if benchmark_bars and entry_session and exit_session:
        _, start_close = _last_close_on_or_before(benchmark_bars, entry_session)
        _, end_close = _last_close_on_or_before(benchmark_bars, exit_session)
        if start_close and end_close:
            bench_pct = round((end_close / start_close - 1) * 100, 2)
    excess = (
        round(pnl_pct - bench_pct, 2) if pnl_pct is not None and bench_pct is not None else None
    )

    outcome = thesis.get("outcome") or {}
    mae, mfe, mae_src = (
        outcome.get("mae_pct"),
        outcome.get("mfe_pct"),
        outcome.get("mae_mfe_source"),
    )
    if mae is None and mfe is None:
        ref = core._get(thesis, "entry", "actual_price")
        bars = (
            data.stock(nse_symbol(thesis), entry_session, exit_session)
            if entry_session and exit_session and ref
            else None
        )
        if bars:
            mae, mfe = _excursions(ref, bars)
            mae_src = "nse_eod_close" if mae is not None else None

    return {
        "thesis_id": thesis.get("thesis_id"),
        "symbol": nse_symbol(thesis),
        "ticker_label": thesis.get("ticker"),
        "thesis_type": thesis.get("thesis_type"),
        "signal_source": source["source"],
        "source_chain": source["chain"],
        "entry_session": entry_session.isoformat() if entry_session else None,
        "exit_session": exit_session.isoformat() if exit_session else None,
        "holding_sessions": holding_sessions,
        "holding_days": core.calculate_holding_days(thesis),
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "charges": charges,
        "gross_pnl": gross,
        "net_pnl": net,
        "pnl_pct": pnl_pct,
        "r_multiple": core.calculate_r_multiple(thesis),
        "exit_reason": core._get(thesis, "exit", "exit_reason"),
        "dividends": divs,
        "net_pnl_incl_dividends": _round(net + divs) if net is not None else None,
        "benchmark": benchmark,
        "benchmark_pct": bench_pct,
        "excess_pct": excess,
        "mae_pct": mae,
        "mfe_pct": mfe,
        "mae_mfe_source": mae_src,
    }


# --------------------------------------------------------------------------- week level


def week_activity(journal: list, start: date, end: date) -> dict:
    fills = [e for e in journal if e.get("event") == "FILL"]
    week = [e for e in fills if _in_window(ist_date(e.get("session")), start, end)]
    divs = [
        e
        for e in journal
        if e.get("event") == "DIVIDEND" and _in_window(ist_date(e.get("session")), start, end)
    ]
    return {
        "buy_fills": sum(1 for e in week if e.get("side") == "BUY"),
        "sell_fills": sum(1 for e in week if e.get("side") == "SELL"),
        "turnover": round(sum((e.get("charges") or {}).get("turnover", 0.0) for e in week), 2),
        "charges_paid": round(sum((e.get("charges") or {}).get("total", 0.0) for e in week), 2),
        "realized_pnl": round(
            sum(e.get("realized_pnl") or 0.0 for e in week if e.get("side") == "SELL"), 2
        ),
        "dividends": round(sum(e.get("amount", 0.0) for e in divs), 2),
    }


def realized_equity(
    account: dict | None, journal: list, start: date, activity: dict
) -> dict | None:
    """Starting capital plus realized P&L and dividends: no marking to market."""
    if not isinstance(account, dict) or not isinstance(
        account.get("starting_capital"), (int, float)
    ):
        return None
    capital = float(account["starting_capital"])
    before = 0.0
    for e in journal:
        day = ist_date(e.get("session"))
        if day is None or day >= start:
            continue
        if e.get("event") == "FILL" and e.get("side") == "SELL":
            before += e.get("realized_pnl") or 0.0
        elif e.get("event") == "DIVIDEND":
            before += e.get("amount", 0.0)
    start_eq = round(capital + before, 2)
    end_eq = round(start_eq + activity["realized_pnl"] + activity["dividends"], 2)
    return {
        "starting_capital": capital,
        "realized_equity_start": start_eq,
        "realized_equity_end": end_eq,
        "realized_return_pct": round((end_eq / start_eq - 1) * 100, 2) if start_eq > 0 else None,
    }


def benchmark_week(name: str, bars: list | None, start: date, end: date) -> dict:
    result = {
        "index": name,
        "start_session": None,
        "end_session": None,
        "start_close": None,
        "end_close": None,
        "change_pct": None,
    }
    if not bars:
        return result
    before = [b for b in bars if b["date"] < start.isoformat()]
    inside = [b for b in bars if start.isoformat() <= b["date"] <= end.isoformat()]
    if before and inside:
        a, b = before[-1], inside[-1]
        result.update(
            start_session=a["date"],
            end_session=b["date"],
            start_close=a["close"],
            end_close=b["close"],
            change_pct=round((b["close"] / a["close"] - 1) * 100, 2),
        )
    return result


def gather_partial_trims(theses: list, start: date, end: date) -> list:
    """In-window trims of still-open (PARTIALLY_CLOSED) theses only; see digest_core."""
    trims = []
    for thesis in theses:
        if thesis.get("status") != "PARTIALLY_CLOSED":
            continue
        for entry in thesis.get("status_history") or []:
            realized = entry.get("realized_pnl")
            day = ist_date(entry.get("at"))
            if realized is None or not _in_window(day, start, end):
                continue
            trims.append(
                {
                    "thesis_id": thesis.get("thesis_id"),
                    "ticker": thesis.get("ticker"),
                    "symbol": nse_symbol(thesis),
                    "date": day.isoformat(),
                    "realized_pnl": realized,
                    "shares_sold": entry.get("shares_sold"),
                    "price": entry.get("price"),
                }
            )
    return trims


def india_lessons(trades: list, drag: dict, benchmark: str) -> list:
    tips = []
    compared = [t for t in trades if t["excess_pct"] is not None]
    beat = sum(1 for t in compared if t["excess_pct"] > 0)
    if len(compared) >= 2 and beat * 2 < len(compared):
        tips.append(
            f"Only {beat} of {len(compared)} closed trades beat {benchmark} over their holding "
            f"sessions; holding a {benchmark} ETF would have done better on the rest."
        )
    gross, charges = drag.get("gross_pnl"), drag.get("charges")
    if gross and charges and gross > 0 and charges / gross >= CHARGES_DRAG_WARN:
        tips.append(
            f"Charges took {charges / gross:.0%} of gross profit ({format_inr(charges)} of "
            f"{format_inr(gross)}); STT is 0.1% on each delivery leg, so small or quick trades "
            "pay proportionally more."
        )
    return tips


# --------------------------------------------------------------------------- digest


def build_digest(
    state_dir: Path,
    from_date: date,
    to_date: date,
    *,
    sessions_fn: Callable = nse_sessions,
    bars_fn: Callable | None = None,
    index_fn: Callable | None = None,
    benchmark: str = DEFAULT_BENCHMARK,
    now: datetime | None = None,
    search_roots: list | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    theses_dir, ledger_dir = resolve_state(Path(state_dir))
    theses = load_theses(theses_dir)
    journal = load_journal(ledger_dir)
    account = _read_json(ledger_dir / "account.json") if ledger_dir else None
    roots = list(search_roots or [])
    if ledger_dir is not None:
        roots.append(ledger_dir.resolve().parent.parent)  # state/india-paper -> repo root
    notes: list = []
    data = _Data(bars_fn, index_fn, notes)

    closed = [
        t
        for t in theses
        if t.get("status") == "CLOSED"
        and _in_window(ist_date(core._get(t, "exit", "actual_date")), from_date, to_date)
    ]
    entries = [ist_date(core._get(t, "entry", "actual_date")) for t in closed]
    first_entry = min([d for d in entries if d] or [from_date])
    bench_bars = data.index(benchmark, min(first_entry, from_date) - timedelta(days=14), to_date)
    if bench_bars is None and index_fn is None:
        notes.append(f"{benchmark} benchmark skipped (no index data source)")

    trades, enriched = [], []
    for thesis in closed:
        tid = thesis.get("thesis_id")
        fills = [e for e in journal if e.get("event") == "FILL" and e.get("thesis_id") == tid]
        divs = [e for e in journal if e.get("event") == "DIVIDEND" and e.get("thesis_id") == tid]
        source = signal_source(thesis, roots)
        row = trade_detail(
            thesis,
            fills,
            divs,
            source=source,
            data=data,
            benchmark=benchmark,
            benchmark_bars=bench_bars,
            sessions_fn=sessions_fn,
        )
        trades.append(row)
        copy = json.loads(json.dumps(thesis, default=str))
        copy["_india"] = {"signal_source": source["source"]}
        copy.setdefault("outcome", {})
        if copy["outcome"].get("mae_pct") is None and row["mae_pct"] is not None:
            copy["outcome"]["mae_pct"] = row["mae_pct"]
            copy["outcome"]["mfe_pct"] = row["mfe_pct"]
        enriched.append(copy)
    trades.sort(key=lambda r: (r["exit_session"] or "", r["symbol"]))

    metrics = core.calculate_metrics(enriched)
    lessons = core.extract_lessons(enriched)
    symbols = {r["thesis_id"]: r["symbol"] for r in trades}
    for key in ("top_wins", "top_losses"):
        for row in lessons[key]:
            row["symbol"] = symbols.get(row["thesis_id"], row.get("ticker"))
    costed = [t for t in trades if t["charges"] is not None and t["net_pnl"] is not None]
    drag = {
        "trades": len(costed),
        "charges": round(sum(t["charges"] for t in costed), 2),
        "net_pnl": round(sum(t["net_pnl"] for t in costed), 2),
    }
    drag["gross_pnl"] = round(drag["net_pnl"] + drag["charges"], 2)
    lessons["process_improvements"].extend(india_lessons(trades, drag, benchmark))

    trims = gather_partial_trims(theses, from_date, to_date)
    activity = week_activity(journal, from_date, to_date)
    try:
        latest = latest_completed_session(now, sessions_fn=sessions_fn)
        window_sessions = list(sessions_fn(from_date, to_date))
    except (CalendarDependencyMissingError, IndiaDataError) as exc:
        notes.append(f"NSE session calendar unavailable ({exc})")
        latest, window_sessions = to_date, []
    done = [d for d in window_sessions if d <= latest]
    bench_end = min(to_date, latest)

    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "skill": SKILL_ID,
        "exchange": EXCHANGE,
        "currency": CURRENCY,
        "timezone": TIMEZONE_NAME,
        "period": {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "sessions": [d.isoformat() for d in window_sessions],
            "session_count": len(window_sessions),
            "last_session": done[-1].isoformat() if done else None,
            "week_complete": len(done) == len(window_sessions),
        },
        "generated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state_dir": str(state_dir),
        "summary": metrics["summary"],
        "metrics": metrics["metrics"],
        "pattern_analysis": core.analyze_patterns(enriched),
        "partial_trims": {
            "count": len(trims),
            "total_realized_pnl": round(sum(t["realized_pnl"] for t in trims), 2),
            "trims": trims,
        },
        "lessons": lessons,
        "india": {
            "trades": trades,
            "week_activity": activity,
            "account": realized_equity(account, journal, from_date, activity),
            "benchmark": benchmark_week(benchmark, bench_bars, from_date, bench_end),
            "charges_drag": drag,
            "dividends_on_closed_trades": round(sum(t["dividends"] for t in trades), 2),
            "data_notes": notes,
        },
    }


# --------------------------------------------------------------------------- markdown


def format_inr(value: float | int | None) -> str:
    """Rupees with Indian digit grouping: ₹12,34,567.50 (as india-paper-ledger)."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(float(value)):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{frac}"


def _pct(value: float | None, signed: bool = True) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def render_markdown(digest: dict) -> str:
    s, m, india = digest["summary"], digest["metrics"], digest["india"]
    period, bench = digest["period"], india["benchmark"]
    acct, act, drag = india["account"], india["week_activity"], india["charges_drag"]
    lines = [
        "# India Weekly Performance Digest",
        "",
        f"**Period:** {period['from']} to {period['to']} (IST) · "
        f"{period['session_count']} NSE session(s)"
        + (
            ""
            if period["week_complete"]
            else f" · week in progress (last session {period['last_session']})"
        ),
        f"**Generated:** {digest['generated_at']} · NSE delivery · INR · paper trading",
        "",
        "## Executive Summary",
        "",
        f"- Closed trades: **{s['total_trades']}** "
        f"({s['winners']}W / {s['losers']}L / {s['breakeven']}BE), "
        f"win rate **{s['win_rate']:.1%}**",
        f"- Realized P&L on closed trades (net of charges): **{format_inr(s['total_realized_pnl'])}**"
        f" · expectancy {format_inr(s['expectancy'])} per trade",
    ]
    if india["dividends_on_closed_trades"]:
        lines.append(
            f"- Dividends received while these were held: {format_inr(india['dividends_on_closed_trades'])}"
            " (not in thesis P&L)"
        )
    lines.append(
        f"- {bench['index']} this week: **{_pct(bench['change_pct'])}**"
        + (
            f" ({bench['start_session']} → {bench['end_session']})"
            if bench["start_session"]
            else ""
        )
    )
    if acct:
        lines.append(
            f"- Realized equity {format_inr(acct['realized_equity_start'])} → "
            f"{format_inr(acct['realized_equity_end'])} ({_pct(acct['realized_return_pct'])}); "
            "open positions are not marked to market"
        )
    if digest["partial_trims"]["count"]:
        lines.append(
            f"- Partial trims of open positions: {digest['partial_trims']['count']}, "
            f"{format_inr(digest['partial_trims']['total_realized_pnl'])} (not in the headline totals)"
        )
    lines += ["", "## Closed Trades", ""]
    if india["trades"]:
        with_divs = any(t["dividends"] for t in india["trades"])
        lines += [
            "| Symbol | Source | Entry → Exit | Sessions | Qty | Entry | Exit | Net P&L | P&L % | R "
            f"| {bench['index']} | Excess | MAE % | MFE % | Exit reason |"
            + (" Dividends |" if with_divs else ""),
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
            + ("---:|" if with_divs else ""),
        ]
        for t in india["trades"]:
            div_cell = f" {format_inr(t['dividends'])} |" if with_divs else ""
            lines.append(
                f"| {t['symbol']} | {t['signal_source']} | {t['entry_session']} → {t['exit_session']} "
                f"| {t['holding_sessions'] if t['holding_sessions'] is not None else '—'} "
                f"| {t['qty'] if t['qty'] is not None else '—'} "
                f"| {format_inr(t['entry_price'])} | {format_inr(t['exit_price'])} "
                f"| {format_inr(t['net_pnl'])} | {_pct(t['pnl_pct'])} | {_num(t['r_multiple'])} "
                f"| {_pct(t['benchmark_pct'])} | {_pct(t['excess_pct'])} "
                f"| {_pct(t['mae_pct'])} | {_pct(t['mfe_pct'])} | {t['exit_reason'] or '—'} |"
                + div_cell
            )
    else:
        lines.append("No closed trades this week.")
    lines += [
        "",
        "## Performance Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Profit factor | {_num(s['profit_factor'])} |",
        f"| Avg winner | {format_inr(m['avg_winner'])} |",
        f"| Avg loser | {format_inr(m['avg_loser'])} |",
        f"| Largest winner | {format_inr(m['largest_winner'])} |",
        f"| Largest loser | {format_inr(m['largest_loser'])} |",
        f"| Avg R-multiple | {_num(m['r_multiple_avg'])} |",
        f"| R-multiple stdev | {_num(m['r_multiple_stdev'])} |",
        f"| Avg MAE % | {_num(m['avg_mae_pct'])} |",
        f"| Avg MFE % | {_num(m['avg_mfe_pct'])} |",
        f"| Avg hold W / L (calendar days) | {_num(m['avg_holding_days_winners'])} / "
        f"{_num(m['avg_holding_days_losers'])} |",
        "",
        "## Costs and Income",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Gross P&L of closed trades (before charges) | {format_inr(drag['gross_pnl'] if drag['trades'] else None)} |",
        f"| Charges on closed trades (both legs) | {format_inr(drag['charges'] if drag['trades'] else None)} |",
        f"| Charges paid on this week's fills | {format_inr(act['charges_paid'])} |",
        f"| Dividends credited this week | {format_inr(act['dividends'])} |",
        "",
        "## Week Activity",
        "",
        f"- Fills: {act['buy_fills']} buy, {act['sell_fills']} sell; turnover {format_inr(act['turnover'])}",
        f"- Realized from this week's sells (incl. partial trims): {format_inr(act['realized_pnl'])}",
        "",
        "## Pattern Analysis",
        "",
    ]
    labels = (
        ("by_source_skill", "By signal source"),
        ("by_exit_reason", "By exit reason"),
        ("by_thesis_type", "By thesis type"),
        ("by_sector", "By sector"),
        ("by_mechanism_tag", "By mechanism tag"),
        ("by_screening_grade", "By screening grade"),
    )
    shown = False
    for key, label in labels:
        buckets = {k: v for k, v in (digest["pattern_analysis"].get(key) or {}).items()}
        if not buckets or set(buckets) == {"unknown"}:
            continue
        shown = True
        lines += [f"### {label}", "", "| Bucket | Wins | Losses | Win rate |", "|---|---|---|---|"]
        for name, b in sorted(buckets.items()):
            lines.append(f"| {name} | {b['wins']} | {b['losses']} | {b['win_rate']:.0%} |")
        lines.append("")
    if not shown:
        lines += ["No pattern data this week.", ""]
    lessons = digest["lessons"]
    lines += ["## Lessons Learned", "", "### Top Winners", ""]
    for key, title in (("top_wins", None), ("top_losses", "### Top Losers")):
        if title:
            lines += ["", title, ""]
        rows = lessons[key]
        if not rows:
            lines.append("- None")
        for row in rows:
            lines.append(
                f"- **{row.get('symbol') or row['ticker']}** ({row['thesis_type']}): "
                f"{format_inr(row['pnl_dollars'])} — {row.get('lessons_learned') or 'no lesson recorded'}"
            )
    lines += ["", "### Process Improvements", ""]
    lines += [f"- {tip}" for tip in lessons["process_improvements"]] or [
        "- No systematic issues flagged this week."
    ]
    if india["data_notes"]:
        lines += ["", "## Data Notes", ""] + [f"- {n}" for n in india["data_notes"]]
    lines += [
        "",
        "## Assumptions",
        "",
        "- P&L comes from trader-memory-core theses; for paper trades it is net of delivery "
        "charges (STT, exchange, SEBI, stamp duty, GST, DP). Raw prices and charges come from "
        "the paper ledger journal.",
        "- Dividends are credited by the paper ledger on the ex-date and are not part of thesis P&L.",
        f"- {bench['index']} is the price index (no dividends). MAE/MFE use official NSE closes "
        "from entry to exit, like trader-memory-core.",
        "- Days are IST; a trade counts in the week its exit date falls in. Descriptive only, "
        "not investment advice.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def cache_only_fns(cache_dir: Path) -> tuple:
    """Read raw bars straight from the shared cache without any network use."""

    def load(path: Path, what: str) -> list:
        data = _read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("bars"), list):
            raise IndiaDataError(f"no cached bars for {what} (run without --no-fetch)")
        return data["bars"]

    def bars_fn(symbol, start, end):
        rows = load(Path(cache_dir) / "stocks" / f"{safe_symbol(symbol)}.json", symbol)
        return [b for b in rows if start.isoformat() <= b["date"] <= end.isoformat()]

    def index_fn(name, start, end):
        rows = load(Path(cache_dir) / "indices" / f"{_slug(name)}.json", name)
        return [b for b in rows if start.isoformat() <= b["date"] <= end.isoformat()]

    return bars_fn, index_fn


def cache_fetch_fns(cache_dir: Path, sessions_fn: Callable, now_fn: Callable) -> tuple:
    cache = BarCache(cache_dir, sessions_fn=sessions_fn, now_fn=now_fn)

    def bars_fn(symbol, start, end):
        return cache.stock_bars(symbol, start, end, adjusted=False)["bars"]

    def index_fn(name, start, end):
        return cache.index_bars(name, start, end)["bars"]

    return bars_fn, index_fn


def _parse_day(value: str, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{flag} must be YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Weekly performance digest of closed NSE paper trades (INR, IST, NSE sessions)."
    )
    parser.add_argument(
        "--state-dir",
        default="state/india-paper",
        help="india-paper-ledger state dir, or a trader-memory-core theses dir (default: %(default)s)",
    )
    parser.add_argument(
        "--week-ending", help="Any date in the week to digest (default: week of the latest session)"
    )
    parser.add_argument("--from-date", help="Explicit inclusive start YYYY-MM-DD (with --to-date)")
    parser.add_argument("--to-date", help="Explicit inclusive end YYYY-MM-DD (with --from-date)")
    parser.add_argument(
        "--benchmark",
        default=DEFAULT_BENCHMARK,
        help="NSE index for comparison (default: %(default)s)",
    )
    parser.add_argument("--cache-dir", help="NSE bar cache (default: .cache/india-data)")
    parser.add_argument(
        "--no-fetch", action="store_true", help="Use cached NSE bars only; never touch the network"
    )
    parser.add_argument("--output-dir", default="reports/", help="Default: %(default)s")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(
    argv: list | None = None,
    *,
    sessions_fn: Callable | None = None,
    now_fn: Callable | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )
    sessions_fn = sessions_fn or nse_sessions
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    state_dir = Path(args.state_dir)
    if not state_dir.is_dir():
        print(f"Error: state dir not found: {state_dir}", file=sys.stderr)
        return 1
    now = now_fn()
    try:
        if args.week_ending and (args.from_date or args.to_date):
            raise ValueError("use either --week-ending or --from-date/--to-date, not both")
        if bool(args.from_date) != bool(args.to_date):
            raise ValueError("--from-date and --to-date must be given together")
        if args.from_date:
            start = _parse_day(args.from_date, "--from-date")
            end = _parse_day(args.to_date, "--to-date")
            if start > end:
                raise ValueError(f"--from-date {start} is later than --to-date {end}")
        elif args.week_ending:
            window = week_window(_parse_day(args.week_ending, "--week-ending"), sessions_fn)
            start, end = window["from"], window["to"]
        else:
            window = default_window(now, sessions_fn)
            start, end = window["from"], window["to"]
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except CalendarDependencyMissingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir) if args.cache_dir else Path.cwd() / ".cache" / "india-data"
    if args.no_fetch:
        bars_fn, index_fn = cache_only_fns(cache_dir)
    else:
        bars_fn, index_fn = cache_fetch_fns(cache_dir, sessions_fn, now_fn)
    try:
        digest = build_digest(
            state_dir,
            start,
            end,
            sessions_fn=sessions_fn,
            bars_fn=bars_fn,
            index_fn=index_fn,
            benchmark=args.benchmark,
            now=now,
        )
    except CalendarDependencyMissingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"india_weekly_digest_{end.isoformat()}"
    json_path, md_path = out / f"{stem}.json", out / f"{stem}.md"
    json_path.write_text(
        json.dumps(digest, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(digest), encoding="utf-8")
    s = digest["summary"]
    print(
        f"India weekly digest {start} to {end}: {s['total_trades']} closed trade(s), "
        f"{s['winners']}W/{s['losers']}L, realized {format_inr(s['total_realized_pnl'])}"
    )
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    for note in digest["india"]["data_notes"]:
        print(f"  NOTE: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
