#!/usr/bin/env python3
"""India Signal Postmortem.

Scores the signals India screeners produced against what NSE prices did next,
whether or not they were traded. Two subcommands:

``record``
    Read india-vcp-screener, india-stockbee-momentum-burst-screener,
    india-earnings-trade-analyzer and india-dividend-screener JSON reports (or a
    plain signals file), measure the return 5 and 20 NSE sessions after each
    signal from official end-of-day closes, compare it with NIFTY 50 over the
    same sessions, tag the market regime from india-exposure-coach reports, mark
    whether the paper ledger took the trade, and write one
    ``pm_<signal_id>.json`` record per signal. Classification is the US
    signal-postmortem logic, copied unchanged into ``postmortem_core.py``.

``summary``
    Aggregate the records: accuracy and false-positive rates per screener (US
    metrics), plus excess return over NIFTY 50, grade and regime breakdowns,
    winners that were never taken, advisory weight feedback and an improvement
    backlog.

Uses only free NSE data via the shared bar cache; ``--no-fetch`` reads the
cache only. Records are evidence for review, not trading advice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import postmortem_core as core  # noqa: E402
from _india_data import (  # noqa: E402
    CURRENCY,
    EXCHANGE,
    IST,
    BarCache,
    CalendarDependencyMissingError,
    IndiaDataError,
    _slug,
    adjust_bars,
    nse_sessions,
    safe_symbol,
)

SKILL_ID = "india-signal-postmortem"
SCHEMA_VERSION = "1.0"
DEFAULT_BENCHMARK = "NIFTY 50"
DEFAULT_HORIZONS = (5, 20)
PRIMARY = 5
REGIME_MAX_AGE_DAYS = 7
UNKNOWN = "UNKNOWN"

# skill id -> (signal id prefix, report file prefix)
SOURCES = {
    "india-vcp-screener": ("india_vcp", "india_vcp_screener_"),
    "india-stockbee-momentum-burst-screener": ("india_mbs", "india_stockbee_momentum_burst_"),
    "india-earnings-trade-analyzer": ("india_etr", "india_earnings_trade_"),
    "india-dividend-screener": ("india_div", "india_dividend_screener_"),
}

__all__ = [
    "IndiaDataError",
    "RegimeBook",
    "cache_only_fns",
    "extract_signals",
    "forward_outcome",
    "load_orders",
    "load_postmortems",
    "main",
    "record_reports",
    "render_summary_markdown",
    "summarize",
    "taken_by",
]


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def _ist_day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def _num(value: Any) -> float | None:
    return float(value) if core.finite_number(value) else None


# --------------------------------------------------------------------------- signals


def _id_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9_.-]", "_", symbol.upper().replace("&", "AND"))


def _report_skill(path: Path, data: dict) -> str | None:
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for value in (data.get("skill"), meta.get("skill"), meta.get("generator")):
        if isinstance(value, str) and value:
            return value
    for skill, (_, prefix) in SOURCES.items():
        if path.name.startswith(prefix):
            return skill
    return None


def _signal(skill, path, symbol, day, price, grade, score, state) -> dict:
    symbol = safe_symbol(symbol)
    prefix = SOURCES[skill][0]
    return {
        "signal_id": f"{prefix}_{_id_symbol(symbol)}_{day.replace('-', '')}",
        "ticker": symbol,
        "symbol": symbol,
        "signal_date": day,
        "predicted_direction": "LONG",
        "source_skill": skill,
        "source_report": str(path),
        "entry_price": _num(price),
        "grade": grade,
        "score": _num(score),
        "setup_state": state,
    }


def extract_signals(path: Path) -> dict:
    """Signals from one India screener report or a plain signals file."""
    path = Path(path)
    data = _read_json(path)
    if isinstance(data, list):
        data = {"signals": data}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a supported report (unreadable JSON)")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    currency = meta.get("currency") or data.get("currency")
    if currency and currency != CURRENCY:
        raise ValueError(f"{path.name} is a {currency} report; only INR (NSE) reports are scored")
    skill = _report_skill(path, data)
    signals, skipped = [], 0

    if skill is None and isinstance(data.get("signals"), list):
        for row in data["signals"]:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(k), str) and row.get(k)
                for k in ("signal_id", "ticker", "signal_date", "source_skill")
            ):
                skipped += 1
                continue
            sig = dict(row)
            sig["ticker"] = sig["symbol"] = safe_symbol(row["ticker"])
            sig.setdefault("predicted_direction", "LONG")
            sig["source_report"] = str(path)
            signals.append(sig)
        return {"skill": "signals-file", "signals": signals, "skipped": skipped}

    if skill == "india-vcp-screener":
        day = meta.get("as_of")
        for row in data.get("results") or []:
            state = None
            if "entry_ready" in row:
                state = "entry_ready" if row["entry_ready"] else "not_entry_ready"
            signals.append(
                _signal(
                    skill,
                    path,
                    row["symbol"],
                    day,
                    row.get("price"),
                    row.get("rating"),
                    row.get("composite_score"),
                    state,
                )
            )
    elif skill == "india-stockbee-momentum-burst-screener":
        for row in data.get("candidates") or []:
            if row.get("state") == "REJECTED":
                skipped += 1
                continue
            day = row.get("date") or meta.get("as_of")
            signals.append(
                _signal(
                    skill,
                    path,
                    row["symbol"],
                    day,
                    row.get("close"),
                    row.get("rating"),
                    row.get("setup_score"),
                    row.get("state"),
                )
            )
    elif skill == "india-earnings-trade-analyzer":
        day = meta.get("as_of")
        for row in data.get("results") or []:
            signals.append(
                _signal(
                    skill,
                    path,
                    row["symbol"],
                    day,
                    row.get("current_price"),
                    row.get("grade"),
                    row.get("composite_score"),
                    None,
                )
            )
    elif skill == "india-dividend-screener":
        for row in data.get("results") or []:
            day = row.get("price_date") or data.get("as_of")
            signals.append(
                _signal(
                    skill,
                    path,
                    row["symbol"],
                    day,
                    row.get("price"),
                    row.get("grade"),
                    row.get("score"),
                    None,
                )
            )
    else:
        raise ValueError(
            f"{path.name} is not a supported report (expected one of: {', '.join(SOURCES)})"
        )
    for sig in signals:
        if _ist_day(sig["signal_date"]) is None:
            raise ValueError(f"{path.name}: signal date missing or invalid")
    return {"skill": skill, "signals": signals, "skipped": skipped}


# --------------------------------------------------------------------------- outcomes


def forward_outcome(bars: list, index_bars: list | None, signal_date: str, horizons) -> dict | None:
    """Returns N NSE sessions after the signal; None while the primary horizon is pending."""
    bars = sorted((b for b in bars if core.finite_number(b.get("close"))), key=lambda b: b["date"])
    upto = [b for b in bars if b["date"] <= signal_date]
    if not upto:
        raise IndiaDataError(f"no NSE bar on or before {signal_date}")
    entry = upto[-1]
    after = [b for b in bars if b["date"] > entry["date"]]
    if len(after) < PRIMARY:
        return None
    index_close = {}
    for bar in sorted(index_bars or [], key=lambda b: b["date"]):
        if core.finite_number(bar.get("close")):
            index_close[bar["date"]] = bar["close"]

    def index_on(day: str) -> float | None:
        keys = [d for d in index_close if d <= day]
        return index_close[max(keys)] if keys else None

    returns, exits, bench, excess = {}, {}, {}, {}
    for h in horizons:
        if len(after) < h:
            continue
        bar = after[h - 1]
        key = f"{h}d"
        raw = bar["close"] / entry["close"] - 1
        returns[key] = round(raw, 6)
        exits[key] = {"date": bar["date"], "close": bar["close"]}
        start, end = index_on(entry["date"]), index_on(bar["date"])
        if start and end:
            b = end / start - 1
            bench[key] = round(b, 6)
            excess[key] = round(raw - b, 6)
    window = after[:PRIMARY]
    lows = [b["low"] for b in window if core.finite_number(b.get("low"))]
    highs = [b["high"] for b in window if core.finite_number(b.get("high"))]
    return {
        "entry_session": entry["date"],
        "entry_close": entry["close"],
        "returns": returns,
        "exits": exits,
        "benchmark": bench,
        "excess": excess,
        "mae_5d_pct": round((min(lows) / entry["close"] - 1) * 100, 2) if lows else None,
        "mfe_5d_pct": round((max(highs) / entry["close"] - 1) * 100, 2) if highs else None,
        "complete": all(f"{h}d" in returns for h in horizons),
    }


class RegimeBook:
    """india-exposure-coach recommendations by as-of date."""

    def __init__(self, entries: list):
        self.entries = sorted(entries)

    @classmethod
    def from_dir(cls, directory: Path | None) -> RegimeBook:
        entries = []
        if directory and Path(directory).is_dir():
            for path in sorted(Path(directory).glob("india_exposure_posture_*.json")):
                data = _read_json(path)
                if not isinstance(data, dict):
                    continue
                day, label = _ist_day(data.get("as_of")), data.get("recommendation")
                if day and isinstance(label, str):
                    entries.append((day.isoformat(), path.name, label))
        return cls(entries)

    def regime_at(self, day: str) -> str:
        known = [e for e in self.entries if e[0] <= day]
        if not known:
            return UNKNOWN
        as_of, _, label = known[-1]
        if (date.fromisoformat(day) - date.fromisoformat(as_of)).days > REGIME_MAX_AGE_DAYS:
            return UNKNOWN
        return label


def load_orders(ledger_dir: Path | None) -> list | None:
    if ledger_dir is None:
        return None
    data = _read_json(Path(ledger_dir) / "orders.json")
    if not isinstance(data, dict) or not isinstance(data.get("orders"), list):
        return None
    return data["orders"]


def taken_by(orders: list | None, symbol: str, start: str, end: str) -> tuple:
    """(order_id, filled?) of a paper BUY placed in [start, end]; (None, None) without a ledger."""
    if orders is None:
        return None, None
    matches = []
    for o in orders:
        day = _ist_day(o.get("placed_at"))
        if o.get("side") == "BUY" and o.get("symbol") == symbol and day:
            if start <= day.isoformat() <= end:
                matches.append(o)
    if not matches:
        return None, False
    filled = [o for o in matches if o.get("status") == "FILLED"]
    chosen = (filled or matches)[0]
    return chosen.get("order_id"), bool(filled)


def build_record(signal: dict, outcome: dict, regimes: RegimeBook, orders, benchmark: str) -> dict:
    exit5 = outcome["exits"]["5d"]
    regime_signal = regimes.regime_at(outcome["entry_session"])
    # An unknown starting regime says nothing about a regime change.
    regime_exit = UNKNOWN if regime_signal == UNKNOWN else regimes.regime_at(exit5["date"])
    resolved = {**signal, "entry_price": outcome["entry_close"], "regime": regime_signal}
    record = core.create_postmortem_record(
        resolved, outcome["returns"], exit5["close"], exit5["date"], regime_exit
    )
    excess5 = outcome["excess"].get("5d")
    order_id, taken = taken_by(orders, signal["ticker"], signal["signal_date"], exit5["date"])
    record.update(
        {
            "symbol": signal["ticker"],
            "exchange": EXCHANGE,
            "currency": CURRENCY,
            "horizon_unit": "nse_sessions",
            "source_report": signal.get("source_report"),
            "report_price": signal.get("entry_price"),
            "entry_session": outcome["entry_session"],
            "grade": signal.get("grade"),
            "score": signal.get("score"),
            "setup_state": signal.get("setup_state"),
            "exits": outcome["exits"],
            "benchmark": benchmark,
            "benchmark_returns": outcome["benchmark"],
            "excess_returns": outcome["excess"],
            "excess_outcome_category": (
                core.classify_outcome(record["predicted_direction"], excess5, UNKNOWN, UNKNOWN)
                if excess5 is not None
                else None
            ),
            "mae_5d_pct": outcome["mae_5d_pct"],
            "mfe_5d_pct": outcome["mfe_5d_pct"],
            "taken": taken,
            "taken_order_id": order_id,
            "complete": outcome["complete"],
        }
    )
    return record


def record_reports(
    paths: list,
    postmortems_dir: Path,
    *,
    bars_fn: Callable,
    index_fn: Callable | None,
    horizons=DEFAULT_HORIZONS,
    since: date,
    now: datetime,
    regimes: RegimeBook,
    orders: list | None,
    benchmark: str = DEFAULT_BENCHMARK,
) -> dict:
    postmortems_dir = Path(postmortems_dir)
    batch = {
        "reports": [str(p) for p in paths],
        "unsupported": [],
        "signals": 0,
        "duplicates": 0,
        "too_old": 0,
        "skipped_in_reports": 0,
        "already_complete": 0,
        "recorded": 0,
        "pending": [],
        "errors": [],
        "notes": [],
    }
    signals: dict = {}
    for path in paths:
        try:
            out = extract_signals(path)
        except (ValueError, KeyError) as exc:
            batch["unsupported"].append(f"{Path(path).name}: {exc}")
            continue
        batch["skipped_in_reports"] += out["skipped"]
        for sig in out["signals"]:
            if sig["signal_id"] in signals:
                batch["duplicates"] += 1
                continue
            if _ist_day(sig["signal_date"]) < since:
                batch["too_old"] += 1
                signals[sig["signal_id"]] = None
                continue
            signals[sig["signal_id"]] = sig
    todo = [s for s in signals.values() if s is not None]
    batch["signals"] = len(todo)
    if not todo:
        return batch

    today = now.astimezone(IST).date()
    first = min(_ist_day(s["signal_date"]) for s in todo)
    index_bars = None
    if index_fn is not None:
        try:
            index_bars = index_fn(benchmark, first - timedelta(days=10), today)
        except (IndiaDataError, ValueError, OSError) as exc:
            batch["notes"].append(f"{benchmark} unavailable ({exc}); excess returns skipped")
    postmortems_dir.mkdir(parents=True, exist_ok=True)
    for sig in sorted(todo, key=lambda s: (s["signal_date"], s["signal_id"])):
        path = postmortems_dir / f"pm_{sig['signal_id']}.json"
        existing = _read_json(path) if path.exists() else None
        if isinstance(existing, dict) and existing.get("complete"):
            batch["already_complete"] += 1
            continue
        day = _ist_day(sig["signal_date"])
        try:
            bars = bars_fn(
                sig["ticker"], day - timedelta(days=10), min(today, day + timedelta(days=60))
            )
            outcome = forward_outcome(bars, index_bars, day.isoformat(), horizons)
        except (IndiaDataError, ValueError, OSError) as exc:
            batch["errors"].append(f"{sig['ticker']} {sig['signal_date']}: {exc}")
            continue
        if outcome is None:
            batch["pending"].append(sig["signal_id"])
            continue
        record = build_record(sig, outcome, regimes, orders, benchmark)
        if isinstance(existing, dict) and existing.get("outcome_notes"):
            record["outcome_notes"] = existing["outcome_notes"]
        path.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        batch["recorded"] += 1
    return batch


# --------------------------------------------------------------------------- summary


def load_postmortems(directory: Path, start: date, end: date) -> list:
    rows = []
    for path in sorted(Path(directory).glob("pm_*.json")):
        data = _read_json(path)
        if not isinstance(data, dict):
            print(f"Warning: skipping unreadable {path.name}", file=sys.stderr)
            continue
        day = _ist_day(data.get("signal_date"))
        if day and start <= day <= end:
            rows.append(data)
    return rows


def _avg(values: list) -> float | None:
    vals = [v for v in values if core.finite_number(v)]
    return round(mean(vals), 6) if vals else None


def _ret(pm: dict, block: str, key: str):
    data = pm.get(block)
    return data.get(key) if isinstance(data, dict) else None


def _bucket(rows: list) -> dict:
    tp = sum(1 for r in rows if r.get("outcome_category") == "TRUE_POSITIVE")
    fp = sum(1 for r in rows if str(r.get("outcome_category", "")).startswith("FALSE_POSITIVE"))
    return {
        "sample_size": len(rows),
        "true_positive": tp,
        "false_positive": fp,
        "accuracy": round(tp / (tp + fp), 4) if tp + fp else None,
        "avg_return_5d": _avg([_ret(r, "realized_returns", "5d") for r in rows]),
        "avg_excess_5d": _avg([_ret(r, "excess_returns", "5d") for r in rows]),
    }


def summarize(postmortems: list, *, min_sample: int = 20) -> dict:
    metrics = core.calculate_skill_metrics(postmortems)
    by_skill = {}
    for skill, data in metrics.items():
        rows = [p for p in postmortems if p.get("source_skill", "unknown") == skill]
        excess5 = [_ret(r, "excess_returns", "5d") for r in rows]
        excess5 = [v for v in excess5 if core.finite_number(v)]
        by_skill[skill] = {
            **data,
            "avg_return_20d": _avg([_ret(r, "realized_returns", "20d") for r in rows]),
            "avg_excess_5d": _avg(excess5),
            "avg_excess_20d": _avg([_ret(r, "excess_returns", "20d") for r in rows]),
            "beat_benchmark_rate_5d": (
                round(sum(1 for v in excess5 if v > 0) / len(excess5), 4) if excess5 else None
            ),
        }
    by_grade: dict = {}
    for skill in by_skill:
        rows = [p for p in postmortems if p.get("source_skill", "unknown") == skill]
        grades = sorted({str(r.get("grade")) for r in rows})
        by_grade[skill] = {
            g: _bucket([r for r in rows if str(r.get("grade")) == g]) for g in grades
        }
    regimes = sorted({str(p.get("regime_at_signal", UNKNOWN)) for p in postmortems})
    by_regime = {
        g: _bucket([p for p in postmortems if str(p.get("regime_at_signal", UNKNOWN)) == g])
        for g in regimes
    }
    taken = [p for p in postmortems if p.get("taken") is True]
    missed = [
        {
            "signal_id": p.get("signal_id"),
            "ticker": p.get("ticker"),
            "source_skill": p.get("source_skill"),
            "signal_date": p.get("signal_date"),
            "return_5d": _ret(p, "realized_returns", "5d"),
        }
        for p in postmortems
        if p.get("taken") is False and p.get("outcome_category") == "TRUE_POSITIVE"
    ]
    missed.sort(key=lambda m: m["return_5d"] or 0, reverse=True)
    dates = sorted(p["signal_date"] for p in postmortems if p.get("signal_date"))
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "india_signal_postmortem_summary",
        "skill": SKILL_ID,
        "exchange": EXCHANGE,
        "currency": CURRENCY,
        "horizon_unit": "nse_sessions",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signal_dates": {"first": dates[0], "last": dates[-1]} if dates else None,
        "total": len(postmortems),
        "by_skill": by_skill,
        "by_skill_grade": by_grade,
        "by_regime": by_regime,
        "taken": {
            "count": len(taken),
            **{k: v for k, v in _bucket(taken).items() if k != "sample_size"},
        },
        "missed_winners": missed[:20],
        "weight_feedback": core.generate_weight_feedback(metrics, min_sample),
        "improvement_backlog": core.generate_improvement_backlog(metrics, postmortems),
    }


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def _rate(value) -> str:
    return "—" if value is None else f"{value:.0%}"


def render_summary_markdown(summary: dict) -> str:
    lines = ["# India Signal Postmortem", ""]
    span = summary["signal_dates"]
    lines.append(
        f"Generated {summary['generated_at']} · NSE · horizons in NSE sessions · "
        + (f"signals {span['first']} to {span['last']}" if span else "no signals")
    )
    lines.append("")
    if not summary["total"]:
        lines += ["No recorded signals in this window. Run `record` first.", ""]
        return "\n".join(lines)
    lines += [
        "## By screener",
        "",
        "| Screener | Signals | Accuracy | FP rate | Avg 5-session | Avg excess 5 | Beat NIFTY 5 "
        "| Avg 20-session | Avg excess 20 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for skill, m in sorted(summary["by_skill"].items()):
        lines.append(
            f"| {skill} | {m['sample_size']} | {_rate(m['accuracy'])} | "
            f"{_rate(m['false_positive_rate'])} | {_pct(m['avg_return_5d'])} | "
            f"{_pct(m['avg_excess_5d'])} | {_rate(m['beat_benchmark_rate_5d'])} | "
            f"{_pct(m['avg_return_20d'])} | {_pct(m['avg_excess_20d'])} |"
        )
    lines += ["", "## By grade", ""]
    for skill, grades in sorted(summary["by_skill_grade"].items()):
        lines += [
            f"### {skill}",
            "",
            "| Grade | Signals | Accuracy | Avg 5-session | Avg excess 5 |",
            "|---|---:|---:|---:|---:|",
        ]
        for grade, b in grades.items():
            lines.append(
                f"| {grade} | {b['sample_size']} | {_rate(b['accuracy'])} | "
                f"{_pct(b['avg_return_5d'])} | {_pct(b['avg_excess_5d'])} |"
            )
        lines.append("")
    lines += [
        "## By market regime at signal (india-exposure-coach)",
        "",
        "| Regime | Signals | Accuracy | Avg 5-session | Avg excess 5 |",
        "|---|---:|---:|---:|---:|",
    ]
    for regime, b in summary["by_regime"].items():
        lines.append(
            f"| {regime} | {b['sample_size']} | {_rate(b['accuracy'])} | "
            f"{_pct(b['avg_return_5d'])} | {_pct(b['avg_excess_5d'])} |"
        )
    t = summary["taken"]
    lines += [
        "",
        "## Taken vs not taken",
        "",
        f"- Paper trades taken from these signals: {t['count']} (accuracy {_rate(t['accuracy'])}, "
        f"avg 5-session {_pct(t['avg_return_5d'])})",
    ]
    if summary["missed_winners"]:
        lines.append("- Winners not taken (top by 5-session return):")
        for m in summary["missed_winners"][:10]:
            lines.append(
                f"  - {m['ticker']} {m['signal_date']} ({m['source_skill']}): {_pct(m['return_5d'])}"
            )
    wf = summary["weight_feedback"]
    lines += [
        "",
        "## Advisory feedback",
        "",
        f"Weight suggestions need {wf['min_sample_threshold']}+ signals per screener "
        f"(confidence {wf['confidence']}).",
    ]
    for a in wf["skill_adjustments"]:
        lines.append(
            f"- {a['skill']}: weight {a['suggested_weight']} — {a['reason']} (n={a['sample_size']})"
        )
    for e in summary["improvement_backlog"]:
        lines.append(
            f"- Backlog [{e['severity']}] {e['skill']}: {e['issue_type']} — {e['suggested_action']}"
        )
    if not wf["skill_adjustments"] and not summary["improvement_backlog"]:
        lines.append("- Nothing flagged yet.")
    lines += [
        "",
        "## Notes",
        "",
        "- Returns are close-to-close from the signal session's official NSE close, adjusted for "
        "splits and bonuses, before charges. Excess is over NIFTY 50 (price index) for the same sessions.",
        "- Classification follows signal-postmortem: ±0.5% is NEUTRAL, a wrong-way move beyond 2% is "
        "FALSE_POSITIVE_SEVERE, and a >2% loss after the exposure-coach regime changed is REGIME_MISMATCH.",
        "- Evidence for review only; not investment advice.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- data access


def cache_only_fns(cache_dir: Path) -> tuple:
    """Adjusted stock bars and index bars from the shared cache, no network."""

    def load(path: Path, what: str) -> dict:
        data = _read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("bars"), list):
            raise IndiaDataError(f"no cached bars for {what} (run without --no-fetch)")
        return data

    def bars_fn(symbol, start, end):
        entry = load(Path(cache_dir) / "stocks" / f"{safe_symbol(symbol)}.json", symbol)
        rows = [b for b in entry["bars"] if start.isoformat() <= b["date"] <= end.isoformat()]
        cutoff = entry.get("coverage_end", "9999-12-31")
        actions = [a for a in entry.get("actions") or [] if a.get("ex_date", "") <= cutoff]
        return adjust_bars(rows, actions)

    def index_fn(name, start, end):
        entry = load(Path(cache_dir) / "indices" / f"{_slug(name)}.json", name)
        return [b for b in entry["bars"] if start.isoformat() <= b["date"] <= end.isoformat()]

    return bars_fn, index_fn


def cache_fetch_fns(cache_dir: Path, sessions_fn: Callable, now_fn: Callable) -> tuple:
    cache = BarCache(cache_dir, sessions_fn=sessions_fn, now_fn=now_fn)
    return (
        lambda symbol, start, end: cache.stock_bars(symbol, start, end)["bars"],
        lambda name, start, end: cache.index_bars(name, start, end)["bars"],
    )


# --------------------------------------------------------------------------- CLI


def _day(value: str, flag: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{flag} must be YYYY-MM-DD, got {value!r}") from exc


def _horizons(text: str) -> tuple:
    try:
        values = tuple(int(p.strip()) for p in text.split(","))
    except ValueError as exc:
        raise ValueError("--horizons must be comma-separated positive whole numbers") from exc
    if not values or any(v <= 0 for v in values):
        raise ValueError("--horizons must be comma-separated positive whole numbers")
    if PRIMARY not in values:
        raise ValueError("--horizons must include 5 (the classified horizon)")
    return tuple(sorted(set(values)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score India screener signals against later NSE prices (5/20 sessions)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="Record outcomes for screener signals")
    rec.add_argument("--reports", nargs="+", help="Report JSON files (default: scan --reports-dir)")
    rec.add_argument("--reports-dir", default="reports/", help="Default: %(default)s")
    rec.add_argument("--since", help="Ignore signals before this date (default: 90 days ago)")
    rec.add_argument("--horizons", default="5,20", help="NSE sessions (default: %(default)s)")
    rec.add_argument("--postmortems-dir", default="reports/india-postmortems")
    rec.add_argument("--regime-dir", help="india-exposure-coach reports (default: --reports-dir)")
    rec.add_argument(
        "--ledger-dir", default="state/india-paper", help="Paper ledger, to mark taken signals"
    )
    rec.add_argument("--benchmark", default=DEFAULT_BENCHMARK)
    rec.add_argument("--cache-dir", help="NSE bar cache (default: .cache/india-data)")
    rec.add_argument("--no-fetch", action="store_true", help="Cached NSE bars only")
    rec.add_argument(
        "--output-dir", default="reports/", help="Batch summary (default: %(default)s)"
    )
    summ = sub.add_parser("summary", help="Aggregate recorded outcomes")
    summ.add_argument("--postmortems-dir", default="reports/india-postmortems")
    summ.add_argument("--from-date", help="First signal date (default: 90 days before --to-date)")
    summ.add_argument("--to-date", help="Last signal date (default: today, IST)")
    summ.add_argument("--min-sample", type=int, default=20, help="For weight feedback (default 20)")
    summ.add_argument("--output-dir", default="reports/", help="Default: %(default)s")
    return parser


def _report_paths(args) -> list:
    if args.reports:
        return [Path(p) for p in args.reports]
    directory = Path(args.reports_dir)
    paths = []
    for _, prefix in SOURCES.values():
        paths += sorted(directory.glob(f"{prefix}*.json"))
    return sorted(paths)


def run_record(args, now: datetime, sessions_fn: Callable, now_fn: Callable) -> int:
    horizons = _horizons(args.horizons)
    today = now.astimezone(IST).date()
    since = _day(args.since, "--since") if args.since else today - timedelta(days=90)
    paths = _report_paths(args)
    if not paths:
        print(f"Error: no India screener reports found in {args.reports_dir}", file=sys.stderr)
        return 1
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path.cwd() / ".cache" / "india-data"
    if args.no_fetch:
        bars_fn, index_fn = cache_only_fns(cache_dir)
    else:
        bars_fn, index_fn = cache_fetch_fns(cache_dir, sessions_fn, now_fn)
    ledger = Path(args.ledger_dir) if args.ledger_dir else None
    batch = record_reports(
        paths,
        Path(args.postmortems_dir),
        bars_fn=bars_fn,
        index_fn=index_fn,
        horizons=horizons,
        since=since,
        now=now,
        regimes=RegimeBook.from_dir(Path(args.regime_dir or args.reports_dir)),
        orders=load_orders(ledger),
        benchmark=args.benchmark,
    )
    batch.update(
        {
            "schema_version": SCHEMA_VERSION,
            "skill": SKILL_ID,
            "generated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "since": since.isoformat(),
            "horizons": list(horizons),
            "postmortems_dir": str(args.postmortems_dir),
        }
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(IST).strftime("%Y-%m-%d_%H%M%S")
    batch_path = out / f"india_signal_postmortem_batch_{stamp}.json"
    batch_path.write_text(json.dumps(batch, indent=2) + "\n", encoding="utf-8")
    print(
        f"Recorded {batch['recorded']}, already complete {batch['already_complete']}, "
        f"pending {len(batch['pending'])}, errors {len(batch['errors'])} "
        f"({batch['signals']} signals since {since}; {batch['duplicates']} duplicates)"
    )
    print(f"Batch: {batch_path}")
    for line in batch["errors"][:10] + batch["notes"] + batch["unsupported"]:
        print(f"  NOTE: {line}", file=sys.stderr)
    if batch["signals"] and not (
        batch["recorded"] or batch["already_complete"] or batch["pending"]
    ):
        print("Error: no signal had usable NSE price evidence", file=sys.stderr)
        return 1
    return 0


def run_summary(args, now: datetime) -> int:
    end = _day(args.to_date, "--to-date") if args.to_date else now.astimezone(IST).date()
    start = _day(args.from_date, "--from-date") if args.from_date else end - timedelta(days=90)
    if start > end:
        raise ValueError(f"--from-date {start} is later than --to-date {end}")
    summary = summarize(
        load_postmortems(Path(args.postmortems_dir), start, end), min_sample=args.min_sample
    )
    summary["period"] = {"from": start.isoformat(), "to": end.isoformat()}
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"india_signal_postmortem_summary_{end.isoformat()}"
    stem.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(render_summary_markdown(summary), encoding="utf-8")
    print(f"Summarised {summary['total']} signal(s) from {start} to {end}")
    print(f"JSON: {stem.with_suffix('.json')}")
    print(f"Markdown: {stem.with_suffix('.md')}")
    return 0


def main(
    argv: list | None = None,
    *,
    now_fn: Callable | None = None,
    sessions_fn: Callable | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    sessions_fn = sessions_fn or nse_sessions
    try:
        if args.command == "record":
            return run_record(args, now_fn(), sessions_fn, now_fn)
        return run_summary(args, now_fn())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except CalendarDependencyMissingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
