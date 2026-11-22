#!/usr/bin/env python3
"""India Pre-Trade Discipline Gate: checklist gate before placing NSE orders.

Reuses the US pre-trade-discipline-gate engine (checklist rules, revenge-trade
window from trader-memory-core state, approved operating rules, journal and
thesis links) by importing it from the sibling skill, and adds India checks:

* candidates built from an india-breakout-trade-planner plan, with the risk
  about to be placed recomputed from the order (quantity, cap, stop, delivery
  charges) so a hand-edited plan cannot slip through;
* a plan whose NSE session has already opened is refused (NO_GO);
* a symbol already open in india-paper-ledger is refused (NO_GO);
* NSE results due within --results-buffer-sessions need review (REVIEW_REQUIRED)
  unless accepted with --accept-results-risk;
* the market-regime and circuit-breaker reports must come from the India skills
  and be current (REVIEW_REQUIRED otherwise).

Outputs india_pre_trade_decision_YYYY-MM-DD_HHMMSS.json / .md (IST stamp).
Exit codes: 0 decision written, 1 input/runtime error, 2 missing dependency
(or any non-GO decision with --fail-on-non-go).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _india_data import (  # noqa: E402
    DEFAULT_DP_CHARGE,
    IST,
    CalendarDependencyMissingError,
    IndiaDataError,
    delivery_leg_charges,
    latest_completed_session,
    nse_sessions,
)

SKILL_ID = "india-pre-trade-discipline-gate"
ENGINE_ENV = "INDIA_PRE_TRADE_US_GATE_DIR"
MARKET_OPEN = time(9, 15)
REQUIREMENTS_HINT = (
    "Install the runtime dependencies: pip install -r "
    "skills/india-pre-trade-discipline-gate/requirements.txt"
)
RUPEE_ALIASES = {
    "planned_risk_inr": "planned_risk_dollars",
    "actual_risk_inr": "actual_risk_dollars",
}
_ENGINE = None


class EngineMissingError(RuntimeError):
    """The US pre-trade-discipline-gate skill is not installed next to this one."""


# --------------------------------------------------------------------------- helpers


def format_inr(value: float | None) -> str:
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


def load_engine():
    """Import check_pre_trade_discipline.py from the sibling US skill (cached)."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    import yaml  # noqa: F401  (the engine needs PyYAML; fail early with a hint)

    env = os.environ.get(ENGINE_ENV)
    scripts = (
        Path(env)
        if env
        else Path(__file__).resolve().parents[2] / "pre-trade-discipline-gate" / "scripts"
    )
    path = scripts / "check_pre_trade_discipline.py"
    if not path.exists():
        raise EngineMissingError(
            f"pre-trade-discipline-gate not found at {path}. Install that skill next to "
            f"{SKILL_ID} or set {ENGINE_ENV} to its scripts directory."
        )
    name = "us_pre_trade_gate_for_india"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    _ENGINE = module
    return module


def parse_as_of(value: str | None) -> datetime:
    """Now, a date (the whole IST day: 23:59:59) or an ISO time (naive = IST)."""
    if not value:
        return datetime.now(IST)
    text = value.strip()
    if len(text) == 10:
        return datetime.combine(date.fromisoformat(text), time(23, 59, 59), tzinfo=IST)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def order_risk_inr(order: dict, *, brokerage: float, dp_charge: float) -> float | None:
    """Loss at the stop for the order as it would be placed, charges included.

    Same formula as india-breakout-trade-planner: qty x (cap - stop) plus the
    buy leg at the cap and the sell leg at the stop.
    """
    qty, stop = order.get("qty"), order.get("stop")
    entry = order.get("limit") if order.get("limit") is not None else order.get("trigger")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        return None
    if not isinstance(stop, (int, float)) or not isinstance(entry, (int, float)):
        return None
    if not 0 < stop < entry:
        return None
    buy = delivery_leg_charges(qty * entry, buy=True, brokerage=brokerage)["total"]
    sell = delivery_leg_charges(qty * stop, buy=False, brokerage=brokerage, dp_charge=dp_charge)[
        "total"
    ]
    return round(qty * (entry - stop) + buy + sell, 2)


# --------------------------------------------------------------------------- inputs


def candidates_from_plan(plan: dict, symbols: list | None) -> tuple[list, dict]:
    """Checklist candidates (US answer fields) from a breakout plan's actionable orders."""
    if not isinstance(plan, dict) or plan.get("skill") != "india-breakout-trade-planner":
        raise ValueError("--plan is not an india-breakout-trade-planner JSON report")
    params = plan.get("parameters") or {}
    brokerage = float(params.get("brokerage_per_order") or 0.0)
    dp_charge = float(params.get("dp_charge", DEFAULT_DP_CHARGE))
    wanted = {s.upper() for s in symbols} if symbols else None
    candidates, meta = [], {}
    for item in plan.get("actionable_orders") or []:
        order = item.get("paper_order") or {}
        symbol = str(order.get("symbol") or item.get("symbol") or "").upper()
        if not symbol or (wanted is not None and symbol not in wanted):
            continue
        tp = item.get("trade_plan") or {}
        stop, trigger = order.get("stop"), order.get("trigger")
        stop_ok = (
            isinstance(stop, (int, float))
            and isinstance(trigger, (int, float))
            and 0 < stop < trigger
        )
        candidates.append(
            {
                "symbol": symbol,
                "order_intent": "ACTIONABLE",
                "entry_in_written_plan": True,
                "stop_predefined": stop_ok,
                "size_within_plan": order.get("qty") == tp.get("shares"),
                "planned_risk_dollars": tp.get("risk_inr"),
                "actual_risk_dollars": order_risk_inr(
                    order, brokerage=brokerage, dp_charge=dp_charge
                ),
                "notes": order.get("thesis") or "",
            }
        )
        meta[symbol] = {
            "plan_session": item.get("plan_valid_for_session")
            or plan.get("plan_valid_for_session"),
            "plan_warnings": list(item.get("warnings") or []),
            "order": order,
        }
    return candidates, meta


def candidates_from_answers(engine, path: Path) -> list:
    candidates = engine.load_candidates(path)
    for candidate in candidates:
        for alias, field in RUPEE_ALIASES.items():
            if alias in candidate and field not in candidate:
                candidate[field] = candidate[alias]
    return candidates


def open_symbols(ledger_dir: Path | None) -> dict:
    """Symbols with an open paper position or OPEN order in india-paper-ledger state."""
    found: dict = {}
    if ledger_dir is None or not ledger_dir.is_dir():
        return found
    positions = ledger_dir / "positions.json"
    orders = ledger_dir / "orders.json"
    if positions.exists():
        for pos in (_read_json(positions).get("positions") or {}).values():
            found.setdefault(str(pos.get("symbol", "")).upper(), "open paper position")
    if orders.exists():
        for order in _read_json(orders).get("orders") or []:
            if order.get("status") == "OPEN":
                found.setdefault(str(order.get("symbol", "")).upper(), "open paper order")
    return found


def results_events(path: Path | None) -> tuple[dict, list]:
    """symbol -> sorted results dates from the india-earnings-calendar archive."""
    if path is None or not path.exists():
        return {}, [
            f"results calendar not found ({path}); NSE results-date risk was not checked. "
            "Refresh it with india-earnings-calendar."
        ]
    by_symbol: dict = {}
    for event in _read_json(path).get("events") or []:
        try:
            day = date.fromisoformat(str(event["date"]))
        except (KeyError, ValueError):
            continue
        by_symbol.setdefault(str(event.get("symbol", "")).upper(), []).append(day)
    return {k: sorted(v) for k, v in by_symbol.items()}, []


# --------------------------------------------------------------------------- India checks


def _artifact_checks(
    path: Path | None, *, expected_skill: str, date_key: str, min_date: date, label: str
) -> list:
    """REVIEW_REQUIRED reasons for a wrong-market or stale upstream artifact."""
    if path is None or not path.exists():
        return []  # the engine already reports missing artifacts
    try:
        data = _read_json(path)
    except (OSError, ValueError):
        return []  # the engine reports unreadable artifacts
    reasons = []
    if not isinstance(data, dict) or data.get("skill") != expected_skill:
        reasons.append(
            f"{label} artifact is not from {expected_skill} (NSE); rerun the India skill"
        )
        return reasons
    stamp = str(data.get(date_key) or "")[:10]
    if not stamp or stamp < min_date.isoformat():
        reasons.append(
            f"{label} artifact is stale ({stamp or 'no date'}; need {min_date} or later); "
            f"rerun {expected_skill}"
        )
    return reasons


def apply_india_checks(
    engine,
    result: dict,
    *,
    as_of: datetime,
    plan_meta: dict,
    held: dict,
    results: dict,
    buffer_sessions: int,
    accepted: set,
    exposure_path: Path | None,
    circuit_path: Path | None,
    sessions_fn: Callable,
) -> dict:
    local = as_of.astimezone(IST)
    first_session_day = local.date() if local.time() < MARKET_OPEN else local.date() + timedelta(1)
    upcoming = [d for d in sessions_fn(first_session_day, first_session_day + timedelta(45))]
    upcoming = [d for d in upcoming if d >= first_session_day]
    if not upcoming:
        raise IndiaDataError(f"No NSE session in the 45 days from {first_session_day}")
    next_session = upcoming[0]
    horizon = upcoming[min(buffer_sessions, len(upcoming)) - 1] if buffer_sessions > 0 else None
    latest = latest_completed_session(as_of, sessions_fn=sessions_fn)

    global_reasons = _artifact_checks(
        exposure_path,
        expected_skill="india-exposure-coach",
        date_key="as_of",
        min_date=latest,
        label="market_regime",
    ) + _artifact_checks(
        circuit_path,
        expected_skill="india-drawdown-circuit-breaker",
        date_key="as_of_date",
        min_date=local.date(),
        label="circuit_breaker",
    )

    rank = engine.DECISION_RANK
    checks: dict = {}

    def raise_to(cand: dict, decision: str, reason: str) -> None:
        if rank[decision] > rank[cand["decision"]]:
            cand["decision"] = decision
        cand["reasons"].append(reason)

    for cand in result["candidate_results"]:
        symbol = cand["symbol"]
        meta = plan_meta.get(symbol, {})
        dates = [d for d in results.get(symbol, []) if d >= local.date()]
        due = dates[0] if dates else None
        info = {
            "plan_session": meta.get("plan_session"),
            "next_eligible_session": next_session.isoformat(),
            "ledger_open": held.get(symbol),
            "results_date": due.isoformat() if due else None,
            "results_within_buffer": bool(due and horizon and due <= horizon),
            "results_risk_accepted": symbol in accepted,
            "planned_risk_inr": cand["checklist_answers"].get("planned_risk_dollars"),
            "actual_risk_inr": cand["checklist_answers"].get("actual_risk_dollars"),
        }
        checks[symbol] = info
        if not cand["actionable"]:
            continue
        session = meta.get("plan_session")
        if session and next_session.isoformat() > session:
            raise_to(
                cand,
                "NO_GO",
                f"plan was for the {session} NSE session, which has already opened; "
                "re-run india-breakout-trade-planner on fresh screener data",
            )
        if held.get(symbol):
            raise_to(cand, "NO_GO", f"{symbol} already has an {held[symbol]} in the paper ledger")
        if info["results_within_buffer"]:
            text = (
                f"NSE results due {due} (within {buffer_sessions} sessions): gap risk through "
                "the stop"
            )
            if symbol in accepted:
                cand["reasons"].append(f"{text}; risk accepted with --accept-results-risk")
            else:
                raise_to(cand, "REVIEW_REQUIRED", f"{text}; accept explicitly or wait")
        for reason in global_reasons:
            raise_to(cand, "REVIEW_REQUIRED", reason)
        for warning in meta.get("plan_warnings", []):
            result["warnings"].append(f"{symbol}: {warning}")

    candidates = [engine.CandidateResult(**c) for c in result["candidate_results"]]
    result["overall_decision"] = engine.aggregate_decision(candidates)
    return checks


# --------------------------------------------------------------------------- output


def generate_markdown(engine, result: dict) -> str:
    body = engine.generate_markdown_report(result)
    body = body.replace(
        "# Pre-Trade Discipline Decision", "# India Pre-Trade Discipline Decision (NSE)", 1
    )
    lines = ["", "## India checks", ""]
    lines += [
        "| Symbol | Planned risk | Order risk | Plan session | Results date | Paper ledger |",
        "|--------|-------------:|-----------:|--------------|--------------|--------------|",
    ]
    for symbol, info in result["india_checks"].items():
        results = info["results_date"] or "—"
        if info["results_within_buffer"]:
            results += " ⚠" + (" (accepted)" if info["results_risk_accepted"] else "")
        planned = info["planned_risk_inr"]
        actual = info["actual_risk_inr"]
        lines.append(
            f"| {symbol} | {format_inr(float(planned)) if planned is not None else '—'} | "
            f"{format_inr(float(actual)) if actual is not None else '—'} | "
            f"{info['plan_session'] or '—'} | {results} | {info['ledger_open'] or 'none'} |"
        )
    lines.append("")
    marker = "\n## Warnings"
    if marker in body:
        head, tail = body.split(marker, 1)
        return head.rstrip("\n") + "\n" + "\n".join(lines) + marker + tail
    return body.rstrip("\n") + "\n" + "\n".join(lines)


def _write(engine, result: dict, json_only: bool) -> tuple[Path, Path | None]:
    json_path = Path(result["artifact_paths"]["json"])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    md = result["artifact_paths"].get("markdown")
    md_path = Path(md) if md else None
    if md_path is not None and not json_only:
        md_path.write_text(generate_markdown(engine, result), encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="India pre-trade discipline gate for NSE orders (reuses the US gate engine)"
    )
    src = parser.add_argument_group("candidates (one or both)")
    src.add_argument("--plan", type=Path, help="india_breakout_trade_plan_*.json")
    src.add_argument("--symbols", nargs="+", help="Only these plan symbols")
    src.add_argument("--answers-file", type=Path, help="US-style answers JSON/YAML (₹ aliases ok)")
    parser.add_argument("--state-dir", type=Path, default=Path("state/india-paper/theses"))
    parser.add_argument("--ledger-state-dir", type=Path, default=Path("state/india-paper"))
    parser.add_argument("--exposure-decision", type=Path, help="india_exposure_posture_*.json")
    parser.add_argument(
        "--circuit-breaker-decision", type=Path, help="india_circuit_breaker_decision_*.json"
    )
    parser.add_argument(
        "--results-calendar",
        type=Path,
        default=Path(".cache/india-data/results_calendar.json"),
        help="india-earnings-calendar archive",
    )
    parser.add_argument("--results-buffer-sessions", type=int, default=5)
    parser.add_argument("--accept-results-risk", nargs="+", default=[], metavar="SYMBOL")
    parser.add_argument("--revenge-window-hours", type=float, default=24.0)
    parser.add_argument("--approved-rules-ledger", type=Path)
    parser.add_argument("--as-of", help="Date (whole IST day) or ISO time; default now")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument(
        "--journal-dir", type=Path, default=Path("state/journal/india-pre-trade-discipline")
    )
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--fail-on-non-go", action="store_true")
    return parser


def run(args, sessions_fn: Callable) -> tuple[dict, Path, Path | None, Path | None]:
    engine = load_engine()
    if args.plan is None and args.answers_file is None:
        raise ValueError("give --plan (india-breakout-trade-planner JSON) and/or --answers-file")
    if args.revenge_window_hours <= 0:
        raise ValueError("--revenge-window-hours must be positive")
    if args.results_buffer_sessions < 0:
        raise ValueError("--results-buffer-sessions must be zero or positive")
    as_of = parse_as_of(args.as_of)
    candidates, plan_meta = [], {}
    if args.plan is not None:
        candidates, plan_meta = candidates_from_plan(_read_json(args.plan), args.symbols)
    if args.answers_file is not None:
        candidates += candidates_from_answers(engine, args.answers_file)

    result = engine.evaluate_pre_trade_gate(
        candidates,
        as_of=as_of,
        state_dir=args.state_dir,
        revenge_window_hours=args.revenge_window_hours,
        market_regime_decision=args.exposure_decision,
        circuit_breaker_decision=args.circuit_breaker_decision,
        output_dir=args.output_dir,
        json_only=args.json_only,
        approved_rules_ledger=args.approved_rules_ledger,
    )
    results, result_warnings = results_events(args.results_calendar)
    result["warnings"].extend(result_warnings)
    result["india_checks"] = apply_india_checks(
        engine,
        result,
        as_of=as_of,
        plan_meta=plan_meta,
        held=open_symbols(args.ledger_state_dir),
        results=results,
        buffer_sessions=args.results_buffer_sessions,
        accepted={s.upper() for s in args.accept_results_risk},
        exposure_path=args.exposure_decision,
        circuit_path=args.circuit_breaker_decision,
        sessions_fn=sessions_fn,
    )
    result.update(
        {"skill": SKILL_ID, "exchange": "NSE", "currency": "INR", "timezone": "Asia/Kolkata"}
    )
    result["inputs"].update(
        {
            "plan": str(args.plan) if args.plan else None,
            "answers_file": str(args.answers_file) if args.answers_file else None,
            "ledger_state_dir": str(args.ledger_state_dir) if args.ledger_state_dir else None,
            "results_calendar": str(args.results_calendar) if args.results_calendar else None,
            "results_buffer_sessions": args.results_buffer_sessions,
        }
    )
    stamp = datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d_%H%M%S")
    stem = args.output_dir / f"india_pre_trade_decision_{stamp}"
    result["artifact_paths"] = {
        "json": str(stem.with_suffix(".json")),
        "markdown": None if args.json_only else str(stem.with_suffix(".md")),
    }
    result["rationale"] = engine.build_rationale(result)
    _write(engine, result, args.json_only)
    engine.finalize_links(result, state_dir=args.state_dir, as_of=as_of)
    journal_path = engine.write_journal(result, args.journal_dir)
    json_path, md_path = _write(engine, result, args.json_only)
    return result, json_path, md_path, journal_path


def main(argv: list | None = None, *, sessions_fn: Callable = nse_sessions) -> int:
    args = build_parser().parse_args(argv)
    try:
        result, json_path, md_path, journal_path = run(args, sessions_fn)
    except (EngineMissingError, CalendarDependencyMissingError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"Error: missing dependency ({exc}). {REQUIREMENTS_HINT}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI returns a clear message
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"JSON report: {json_path}")
    if md_path is not None:
        print(f"Markdown report: {md_path}")
    if journal_path is not None:
        print(f"Journal: {journal_path}")
    print(f"\nDecision: {result['overall_decision']}")
    print(result["rationale"])
    for cand in result["candidate_results"]:
        if cand["actionable"]:
            print(f"  {cand['symbol']}: {cand['decision']}")
            for reason in cand["reasons"]:
                print(f"    - {reason}")
    if args.fail_on_non_go and result["overall_decision"] != "GO":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
