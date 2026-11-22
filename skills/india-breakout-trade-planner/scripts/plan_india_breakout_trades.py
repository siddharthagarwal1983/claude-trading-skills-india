#!/usr/bin/env python3
"""India Breakout Trade Planner: Minervini breakout plans from india-vcp-screener output.

Applies the US breakout-trade-planner's Minervini gate to NSE candidates,
sizes in whole shares at the worst (capped) entry with round-trip delivery
charges inside the risk budget, caps size by turnover and cash, and emits a
ready india-paper-ledger order plus a broker-style NSE order for each plan.

Outputs india_breakout_trade_plan_YYYY-MM-DD_HHMMSS.json / .md (IST stamp).
Exit codes: 0 success, 1 invalid input, 2 missing dependency.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _india_data import (  # noqa: E402
    DEFAULT_DP_CHARGE,
    IST,
    CalendarDependencyMissingError,
    IndiaDataError,
    latest_completed_session,
    nse_sessions,
)
from india_breakout_risk import (  # noqa: E402
    calculate_r_multiples,
    calculate_risks,
    derive_trade_prices,
    get_rating_band,
    get_sizing_multiplier,
    size_position,
)

SKILL_ID = "india-breakout-trade-planner"
ACCEPTED_INPUT_VERSIONS = {"1.0"}
MAX_RISK_PCT = 8.0
PLAN_BANDS = ("textbook", "strong", "good")
MARKET_OPEN = time(9, 15)
DATE_ONLY_TIME = time(20, 0)  # a date-only --as-of means that evening, after the bhavcopy
BAND_WARN_PCT = 5.0
LEDGER_CLI = "uv run python skills/india-paper-ledger/scripts/india_paper_ledger.py"
REQUIRED_FIELDS = ["symbol", "sector", "price", "composite_score", "execution_state", "valid_vcp"]
BREAKOUT_EXTRA_FIELDS = [
    "volume_pattern.breakout_volume_detected",
    "pivot_proximity.distance_from_pivot_pct",
]


@dataclass
class PlanParams:
    account_size: float | None = None
    risk_pct: float = 0.5
    max_position_pct: float = 10.0
    max_sector_pct: float = 30.0
    max_portfolio_heat_pct: float = 6.0
    target_r_multiple: float = 2.0
    stop_buffer_pct: float = 1.0
    max_chase_pct: float = 2.0
    pivot_buffer_pct: float = 0.1
    max_adv_pct: float = 1.0
    valid_sessions: int = 1
    brokerage_per_order: float = 0.0
    dp_charge: float = DEFAULT_DP_CHARGE


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


def _get(d: dict, key: str):
    val = d
    for part in key.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def resolve_as_of(value: str | None) -> datetime:
    """Planning instant: now, a date (that evening 20:00 IST) or an aware ISO time."""
    if value is None:
        return datetime.now(timezone.utc)
    text = value.strip()
    if len(text) == 10:
        return datetime.combine(date.fromisoformat(text), DATE_ONLY_TIME, tzinfo=IST)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("--as-of needs a timezone offset, e.g. 2026-11-20T20:00:00+05:30")
    return parsed


def plan_valid_session(as_of: datetime, sessions_fn: Callable = nse_sessions) -> date:
    """The NSE session the plan is for: today if before 09:15 IST, else the next one."""
    local = as_of.astimezone(IST)
    first = local.date() if local.time() < MARKET_OPEN else local.date() + timedelta(days=1)
    upcoming = [d for d in sessions_fn(first, first + timedelta(days=21)) if d >= first]
    if not upcoming:
        raise IndiaDataError(f"No NSE session in the three weeks from {first}")
    return upcoming[0]


# --------------------------------------------------------------------------- inputs


def load_input(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    version = data.get("schema_version")
    if version not in ACCEPTED_INPUT_VERSIONS:
        raise ValueError(
            f"Unsupported schema_version {version!r} (expected {sorted(ACCEPTED_INPUT_VERSIONS)})"
        )
    meta = data.get("metadata") or {}
    if meta.get("currency") != "INR" or meta.get("exchange", "NSE") != "NSE":
        raise ValueError(
            f"{path} is not an india-vcp-screener report (metadata.currency must be INR); "
            "use breakout-trade-planner for US screener output"
        )
    if not isinstance(data.get("results"), list):
        raise ValueError("Input JSON is missing the 'results' array")
    return data


def load_ledger_report(path: str) -> dict:
    """Account, open risk and held symbols from an india-paper-ledger JSON report."""
    with open(path, encoding="utf-8") as handle:
        snap = json.load(handle)
    if snap.get("skill") != "india-paper-ledger":
        raise ValueError(f"{path} is not an india-paper-ledger report")
    account = snap.get("account") or {}
    held = {p["symbol"] for p in snap.get("positions", [])}
    pending = {
        o["symbol"]
        for o in snap.get("open_orders", [])
        if o.get("side") == "BUY" and o.get("status") == "OPEN"
    }
    return {
        "path": path,
        "as_of": snap.get("as_of"),
        "equity": account.get("equity"),
        "cash": account.get("cash"),
        "open_risk_pct": account.get("open_risk_pct") or 0.0,
        "positions": snap.get("positions", []),
        "held": held,
        "pending": pending,
    }


def validate_result(result: dict) -> list:
    problems = [f"missing required field: {f}" for f in REQUIRED_FIELDS if _get(result, f) is None]
    if _get(result, "vcp_pattern.pivot_price") is None:
        problems.append("missing vcp_pattern.pivot_price")
    contractions = _get(result, "vcp_pattern.contractions")
    if not isinstance(contractions, list) or not contractions:
        problems.append("missing or empty vcp_pattern.contractions")
    elif _get(contractions[-1], "low_price") is None:
        problems.append("missing contractions[-1].low_price")
    if result.get("execution_state") == "Breakout":
        problems += [
            f"missing Breakout field: {f}" for f in BREAKOUT_EXTRA_FIELDS if _get(result, f) is None
        ]
    return problems


# --------------------------------------------------------------------------- planning


class _Planner:
    def __init__(self, params: PlanParams, ledger: dict | None, valid_session: date, sectors: dict):
        self.p = params
        self.ledger = ledger
        self.valid_session = valid_session
        self.heat = ledger["open_risk_pct"] if ledger else 0.0
        self.cash = ledger["cash"] if ledger and ledger.get("cash") is not None else None
        self.sector_pct: dict = {}
        if ledger:
            for pos in ledger["positions"]:
                sector = sectors.get(pos["symbol"])
                if sector:
                    pct = pos.get("market_value", 0.0) / params.account_size * 100
                    self.sector_pct[sector] = self.sector_pct.get(sector, 0.0) + pct

    def classify(self, r: dict) -> tuple:
        p = self.p
        symbol = r["symbol"]
        score = r["composite_score"]
        state = r["execution_state"]
        valid = bool(r.get("valid_vcp"))
        band = get_rating_band(score)
        pivot = r["vcp_pattern"]["pivot_price"]
        last_low = r["vcp_pattern"]["contractions"][-1]["low_price"]
        try:
            signal, worst, stop = derive_trade_prices(
                pivot,
                last_low,
                pivot_buffer_pct=p.pivot_buffer_pct,
                max_chase_pct=p.max_chase_pct,
                stop_buffer_pct=p.stop_buffer_pct,
            )
        except ValueError as exc:
            return "rejected", {"symbol": symbol, "reason": f"trade price derivation failed: {exc}"}
        risk_signal, risk_worst = calculate_risks(signal, worst, stop)
        target = calculate_r_multiples(worst, stop, (p.target_r_multiple,))[
            f"{p.target_r_multiple}R"
        ]
        base = {
            "symbol": symbol,
            "company_name": r.get("company_name", ""),
            "sector": r.get("sector", "Unknown"),
            "composite_score": score,
            "rating_band": band,
            "execution_state": state,
            "price": r["price"],
            "pivot_price": pivot,
            "avg_turnover_cr": r.get("avg_turnover_cr"),
        }

        if state == "Pre-breakout":
            if valid and band in PLAN_BANDS and risk_worst <= MAX_RISK_PCT:
                return self._actionable(
                    base, signal, worst, stop, risk_signal, risk_worst, target, pivot
                )
            if valid and 60 <= score < 70:
                return "watchlist", self._watch(base, pivot, stop)
            reasons = []
            if not valid:
                reasons.append("valid_vcp=False")
            if band not in PLAN_BANDS:
                reasons.append(f"rating_band={band}")
            if risk_worst > MAX_RISK_PCT:
                reasons.append(f"risk_pct_worst={risk_worst}%>{MAX_RISK_PCT}%")
            return "rejected", {"symbol": symbol, "reason": "; ".join(reasons)}

        if state == "Breakout":
            volume = bool(_get(r, "volume_pattern.breakout_volume_detected"))
            distance = _get(r, "pivot_proximity.distance_from_pivot_pct")
            price = r["price"]
            if (
                valid
                and band in PLAN_BANDS
                and risk_worst <= MAX_RISK_PCT
                and volume
                and distance <= p.max_chase_pct
                and price <= worst
            ):
                return "revalidation", {
                    **base,
                    "plan_type": "late_breakout_revalidation",
                    "decision_code": "REVALIDATION_BREAKOUT",
                    "next_action": (
                        "already above the pivot: confirm the next session holds above "
                        f"{format_inr(pivot)} before any order; never pay above {format_inr(worst)}"
                    ),
                    "max_entry_price": worst,
                    "stop_loss_price": stop,
                    "risk_pct_worst": risk_worst,
                }
            reasons = []
            if not valid:
                reasons.append("valid_vcp=False")
            if band not in PLAN_BANDS:
                reasons.append(f"rating_band={band}")
            if not volume:
                reasons.append("no breakout volume")
            if distance > p.max_chase_pct:
                reasons.append(f"distance={distance}%>{p.max_chase_pct}%")
            if price > worst:
                reasons.append(f"price={price}>worst_entry={worst}")
            if risk_worst > MAX_RISK_PCT:
                reasons.append(f"risk_pct_worst={risk_worst}%>{MAX_RISK_PCT}%")
            return "rejected", {"symbol": symbol, "reason": "; ".join(reasons) or "ineligible"}

        if valid and state == "Early-post-breakout" and 60 <= score < 70:
            return "watchlist", self._watch(base, pivot, stop)
        return "rejected", {"symbol": symbol, "reason": f"state={state}, score={score}"}

    def _watch(self, base, pivot, stop):
        return {
            **base,
            "plan_type": "watchlist",
            "stop_loss_price": stop,
            "alert_trigger": f"Close above {format_inr(pivot)} on 1.5x average volume",
        }

    def _actionable(self, base, signal, worst, stop, risk_signal, risk_worst, target, pivot):
        p = self.p
        sector = base["sector"]
        multiplier = get_sizing_multiplier(base["rating_band"])
        sizing = size_position(
            worst_entry=worst,
            stop_loss=stop,
            account_size=p.account_size,
            risk_pct=p.risk_pct,
            sizing_multiplier=multiplier,
            max_position_pct=p.max_position_pct,
            max_sector_pct=p.max_sector_pct,
            current_sector_exposure_pct=self.sector_pct.get(sector, 0.0),
            avg_turnover_cr=base["avg_turnover_cr"],
            max_adv_pct=p.max_adv_pct,
            cash_available=self.cash,
            brokerage=p.brokerage_per_order,
            dp_charge=p.dp_charge,
        )
        if sizing["shares"] == 0:
            return "constrained", {
                "symbol": base["symbol"],
                "reason": f"0 shares: {sizing['binding_constraint']}",
            }
        risk_pct = sizing["risk_inr"] / p.account_size * 100
        if self.heat + risk_pct > p.max_portfolio_heat_pct:
            return "deferred", {
                "symbol": base["symbol"],
                "reason": (
                    f"portfolio heat {self.heat + risk_pct:.2f}% > {p.max_portfolio_heat_pct}%"
                ),
            }
        self.heat += risk_pct
        self.sector_pct[sector] = (
            self.sector_pct.get(sector, 0.0) + sizing["position_value"] / p.account_size * 100
        )
        if self.cash is not None:
            self.cash -= sizing["position_value"] + sizing["buy_charges"]

        qty = sizing["shares"]
        thesis = (
            f"VCP breakout above {format_inr(pivot)} pivot (score {base['composite_score']}, "
            f"{base['rating_band']}); stop under last contraction low"
        )
        paper_order = {
            "symbol": base["symbol"],
            "qty": qty,
            "order_type": "STOP_LIMIT",
            "trigger": signal,
            "limit": worst,
            "stop": stop,
            "target": target,
            "valid_sessions": p.valid_sessions,
            "thesis_type": "pivot_breakout",
            "thesis": thesis,
        }
        command = " ".join(
            [
                LEDGER_CLI,
                "buy",
                "--symbol",
                shlex.quote(base["symbol"]),
                "--qty",
                str(qty),
                "--type",
                "stop-limit",
                "--trigger",
                f"{signal:.2f}",
                "--limit",
                f"{worst:.2f}",
                "--stop",
                f"{stop:.2f}",
                "--target",
                f"{target:.2f}",
                "--valid-sessions",
                str(p.valid_sessions),
                "--thesis",
                shlex.quote(thesis),
            ]
        )
        warnings = []
        if signal > base["price"] * (1 + BAND_WARN_PCT / 100):
            warnings.append(
                f"trigger {format_inr(signal)} is more than {BAND_WARN_PCT:.0f}% above the last "
                f"close {format_inr(base['price'])}; a stock in a 5% price band cannot reach it "
                "in one session"
            )
        plan = {
            **base,
            "plan_type": "pending_breakout",
            "decision_code": "ACTIONABLE_PREBREAKOUT",
            "decision_reason": (
                f"valid_vcp && state=Pre-breakout && band={base['rating_band']} && "
                f"risk_worst={risk_worst}% <= {MAX_RISK_PCT}%"
            ),
            "plan_valid_for_session": self.valid_session.isoformat(),
            "trade_plan": {
                "signal_entry": signal,
                "worst_entry": worst,
                "stop_loss_price": stop,
                "risk_per_share": round(worst - stop, 2),
                "risk_pct_signal": risk_signal,
                "risk_pct_worst": risk_worst,
                "r_multiples_signal": calculate_r_multiples(signal, stop),
                "r_multiples_worst": calculate_r_multiples(worst, stop),
                "target_price": target,
                "reward_risk_ratio": p.target_r_multiple,
                "sizing_multiplier": multiplier,
                "effective_risk_pct": sizing["effective_risk_pct"],
                "shares": qty,
                "position_value": sizing["position_value"],
                "buy_charges": sizing["buy_charges"],
                "price_risk_inr": sizing["price_risk_inr"],
                "risk_inr": sizing["risk_inr"],
                "risk_pct_of_account": round(risk_pct, 3),
                "cumulative_risk_pct": round(self.heat, 2),
                "binding_constraint": sizing["binding_constraint"],
                "constraints_applied": sizing["constraints_applied"],
            },
            "paper_order": paper_order,
            "paper_command": command,
            "broker_order": {
                "exchange": "NSE",
                "tradingsymbol": base["symbol"],
                "transaction_type": "BUY",
                "product": "CNC",
                "order_type": "SL",
                "trigger_price": signal,
                "price": worst,
                "quantity": qty,
                "validity": "DAY",
                "after_fill": (
                    f"place a stop-loss sell at {format_inr(stop)} (SL-M or a GTT) and a target "
                    f"sell at {format_inr(target)}; NSE has no native bracket order for delivery"
                ),
            },
            "warnings": warnings,
        }
        return "actionable", plan


def generate_plans(
    data: dict,
    params: PlanParams,
    *,
    as_of: datetime,
    ledger: dict | None = None,
    sessions_fn: Callable = nse_sessions,
    input_path: str = "",
) -> dict:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if params.account_size is None:
        if not ledger or not ledger.get("equity"):
            raise ValueError("give --account-size or a --ledger-report with account equity")
        params.account_size = float(ledger["equity"])
    if not params.account_size > 0:
        raise ValueError("account size must be positive")

    warnings = []
    screener_as_of = _get(data, "metadata.as_of")
    latest = latest_completed_session(as_of, sessions_fn=sessions_fn)
    if screener_as_of and screener_as_of < latest.isoformat():
        warnings.append(
            {
                "symbol": None,
                "code": "STALE_SCREENER",
                "message": (
                    f"screener data is from {screener_as_of}; the latest completed NSE session "
                    f"is {latest}. Re-run india-vcp-screener before acting."
                ),
            }
        )
    valid_session = plan_valid_session(as_of, sessions_fn)
    sectors = {r.get("symbol"): r.get("sector") for r in data["results"]}
    engine = _Planner(params, ledger, valid_session, sectors)
    buckets = {
        k: []
        for k in (
            "actionable",
            "revalidation",
            "watchlist",
            "rejected",
            "deferred",
            "constrained",
            "skipped",
        )
    }
    for r in sorted(data["results"], key=lambda x: x.get("composite_score") or 0, reverse=True):
        symbol = r.get("symbol", "UNKNOWN")
        problems = validate_result(r)
        if problems:
            warnings += [
                {"symbol": symbol, "code": "MISSING_FIELD", "message": m} for m in problems
            ]
            buckets["rejected"].append(
                {"symbol": symbol, "reason": "validation: " + "; ".join(problems)}
            )
            continue
        if ledger and symbol in ledger["held"] | ledger["pending"]:
            what = "already held" if symbol in ledger["held"] else "open paper buy order"
            buckets["skipped"].append({"symbol": symbol, "reason": f"{what} in the paper ledger"})
            continue
        kind, payload = engine.classify(r)
        buckets[kind].append(payload)

    actionable = buckets["actionable"]
    total_risk = sum(a["trade_plan"]["risk_inr"] for a in actionable)
    return {
        "schema_version": "1.0",
        "skill": SKILL_ID,
        "generated_at": as_of.astimezone(IST).isoformat(timespec="seconds"),
        "currency": "INR",
        "exchange": "NSE",
        "plan_valid_for_session": valid_session.isoformat(),
        "parameters": {
            **asdict(params),
            "starting_open_risk_pct": ledger["open_risk_pct"] if ledger else 0.0,
            "ledger_report": ledger["path"] if ledger else None,
        },
        "input_metadata": {
            "source_file": input_path,
            "screener_as_of": screener_as_of,
            "screener_generated_at": _get(data, "metadata.generated_at"),
            "candidates_in_file": len(data["results"]),
        },
        "summary": {
            "actionable_count": len(actionable),
            "revalidation_count": len(buckets["revalidation"]),
            "watchlist_count": len(buckets["watchlist"]),
            "rejected_count": len(buckets["rejected"]),
            "deferred_count": len(buckets["deferred"]),
            "constrained_count": len(buckets["constrained"]),
            "skipped_count": len(buckets["skipped"]),
            "total_risk_inr": round(total_risk, 2),
            "total_risk_pct": round(total_risk / params.account_size * 100, 2),
            "total_position_value": round(
                sum(a["trade_plan"]["position_value"] for a in actionable), 2
            ),
            "portfolio_heat_after_pct": round(engine.heat, 2),
        },
        "actionable_orders": actionable,
        "revalidation": buckets["revalidation"],
        "watchlist": buckets["watchlist"],
        "rejected": buckets["rejected"],
        "deferred": buckets["deferred"],
        "constrained": buckets["constrained"],
        "skipped": buckets["skipped"],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- output


def generate_markdown(plans: dict) -> str:
    p, s = plans["parameters"], plans["summary"]
    lines = [
        "# India Breakout Trade Plan (NSE)",
        "",
        f"**Generated:** {plans['generated_at']} · **Valid for session:** "
        f"{plans['plan_valid_for_session']} · **Screener data:** "
        f"{plans['input_metadata']['screener_as_of']}",
        f"**Account:** {format_inr(p['account_size'])} · **Base risk:** {p['risk_pct']}% · "
        f"**Heat cap:** {p['max_portfolio_heat_pct']}% (starting {p['starting_open_risk_pct']}%)",
        "",
        "## Summary",
        "",
        f"- Actionable: {s['actionable_count']} · Revalidation: {s['revalidation_count']} · "
        f"Watchlist: {s['watchlist_count']} · Skipped (held): {s['skipped_count']}",
        f"- Rejected: {s['rejected_count']} · Deferred (heat): {s['deferred_count']} · "
        f"Constrained: {s['constrained_count']}",
        f"- New risk: {format_inr(s['total_risk_inr'])} ({s['total_risk_pct']:.2f}%), "
        f"heat after: {s['portfolio_heat_after_pct']:.2f}%, capital: "
        f"{format_inr(s['total_position_value'])}",
        "",
    ]
    if plans["warnings"]:
        lines += ["## Warnings", ""]
        lines += [f"- {w['symbol'] or 'plan'}: {w['message']}" for w in plans["warnings"]]
        lines.append("")
    if plans["actionable_orders"]:
        lines += ["## Actionable Orders", ""]
        for i, o in enumerate(plans["actionable_orders"], 1):
            tp = o["trade_plan"]
            lines += [
                f"### {i}. {o['symbol']} — {o.get('company_name', '')}",
                f"**Rating:** {o['rating_band']} ({o['composite_score']}) · **Sector:** "
                f"{o['sector']} · **Last close:** {format_inr(o['price'])}",
                "",
                "| Parameter | Value |",
                "|-----------|-------|",
                f"| Buy-stop trigger | {format_inr(tp['signal_entry'])} |",
                f"| Limit (max chase) | {format_inr(tp['worst_entry'])} |",
                f"| Stop loss | {format_inr(tp['stop_loss_price'])} |",
                f"| Risk (worst entry) | {tp['risk_pct_worst']:.2f}% |",
                f"| Target ({tp['reward_risk_ratio']}R) | {format_inr(tp['target_price'])} |",
                f"| Shares | {tp['shares']} |",
                f"| Position value | {format_inr(tp['position_value'])} |",
                f"| Risk incl. charges | {format_inr(tp['risk_inr'])} "
                f"({tp['risk_pct_of_account']:.2f}%) |",
                f"| Binding cap | {tp['binding_constraint'] or 'risk budget'} |",
                "",
                "Paper order:",
                "",
                "```bash",
                o["paper_command"],
                "```",
                "",
            ]
            lines += [f"- ⚠ {w}" for w in o["warnings"]]
            if o["warnings"]:
                lines.append("")
    if plans["revalidation"]:
        lines += ["## Revalidation (already broke out)", ""]
        for r in plans["revalidation"]:
            lines.append(f"- **{r['symbol']}** — {r['next_action']}")
        lines.append("")
    if plans["watchlist"]:
        lines += ["## Watchlist", "", "| Symbol | Score | Alert |", "|--------|------:|-------|"]
        lines += [
            f"| {w['symbol']} | {w['composite_score']} | {w['alert_trigger']} |"
            for w in plans["watchlist"]
        ]
        lines.append("")
    other = plans["skipped"] + plans["deferred"] + plans["constrained"] + plans["rejected"]
    if other:
        lines += ["## Not Planned", "", "| Symbol | Reason |", "|--------|--------|"]
        lines += [f"| {o['symbol']} | {o['reason']} |" for o in other]
        lines.append("")
    lines += [
        "---",
        "Risk includes round-trip delivery charges (STT, stamp duty, exchange, SEBI, GST, DP). "
        "Prices are on NSE tick bands; confirm with your broker. Not investment advice.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan Minervini breakout trades from india-vcp-screener output (NSE, rupees)"
    )
    parser.add_argument("--input", required=True, help="india-vcp-screener JSON report")
    parser.add_argument("--account-size", type=float, help="Equity in rupees (or --ledger-report)")
    parser.add_argument(
        "--ledger-report",
        help="india-paper-ledger JSON report: equity, cash, open risk and held symbols",
    )
    parser.add_argument("--risk-pct", type=float, default=0.5, help="Base risk %% per trade")
    parser.add_argument("--max-position-pct", type=float, default=10.0)
    parser.add_argument("--max-sector-pct", type=float, default=30.0)
    parser.add_argument("--max-portfolio-heat-pct", type=float, default=6.0)
    parser.add_argument("--target-r-multiple", type=float, default=2.0)
    parser.add_argument("--stop-buffer-pct", type=float, default=1.0)
    parser.add_argument("--max-chase-pct", type=float, default=2.0)
    parser.add_argument("--pivot-buffer-pct", type=float, default=0.1)
    parser.add_argument(
        "--max-adv-pct", type=float, default=1.0, help="Max %% of average daily rupee turnover"
    )
    parser.add_argument("--valid-sessions", type=int, default=1, help="Paper order validity")
    parser.add_argument("--brokerage-per-order", type=float, default=0.0)
    parser.add_argument("--dp-charge", type=float, default=DEFAULT_DP_CHARGE)
    parser.add_argument("--output-dir", default="reports/")
    parser.add_argument(
        "--as-of", help="YYYY-MM-DD (that evening, 20:00 IST) or an ISO time with offset"
    )
    return parser


def main(argv: list | None = None, *, sessions_fn: Callable = nse_sessions) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        as_of = resolve_as_of(args.as_of)
    except ValueError as exc:
        parser.error(str(exc))
    params = PlanParams(
        account_size=args.account_size,
        risk_pct=args.risk_pct,
        max_position_pct=args.max_position_pct,
        max_sector_pct=args.max_sector_pct,
        max_portfolio_heat_pct=args.max_portfolio_heat_pct,
        target_r_multiple=args.target_r_multiple,
        stop_buffer_pct=args.stop_buffer_pct,
        max_chase_pct=args.max_chase_pct,
        pivot_buffer_pct=args.pivot_buffer_pct,
        max_adv_pct=args.max_adv_pct,
        valid_sessions=args.valid_sessions,
        brokerage_per_order=args.brokerage_per_order,
        dp_charge=args.dp_charge,
    )
    try:
        data = load_input(args.input)
        ledger = load_ledger_report(args.ledger_report) if args.ledger_report else None
        plans = generate_plans(
            data, params, as_of=as_of, ledger=ledger, sessions_fn=sessions_fn, input_path=args.input
        )
    except CalendarDependencyMissingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError, json.JSONDecodeError, IndiaDataError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = as_of.astimezone(IST).strftime("%Y-%m-%d_%H%M%S")
    json_path = os.path.join(args.output_dir, f"india_breakout_trade_plan_{stamp}.json")
    md_path = os.path.join(args.output_dir, f"india_breakout_trade_plan_{stamp}.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(plans, handle, indent=2, ensure_ascii=False)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(generate_markdown(plans))
    s = plans["summary"]
    print(
        f"Actionable: {s['actionable_count']} | Revalidation: {s['revalidation_count']} | "
        f"Watchlist: {s['watchlist_count']} | New risk {format_inr(s['total_risk_inr'])} "
        f"({s['total_risk_pct']:.2f}%)"
    )
    print(f"Report: {md_path}\nJSON:   {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
