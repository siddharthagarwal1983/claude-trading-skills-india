"""Performance metrics copied unchanged from weekly-performance-digest.

The functions below are verbatim copies of
``skills/weekly-performance-digest/scripts/generate_weekly_digest.py`` so the
India digest computes win rate, expectancy, profit factor, R-multiples and the
pattern buckets exactly as the US skill does. ``tests/test_digest_core_parity.py``
fails if the US functions change and this copy is not refreshed.

Only ``_PATTERN_DIMENSIONS`` differs: ``by_source_skill`` reads the signal
source the India digest resolves (``_india.signal_source``) instead of
``origin.skill``, which is always ``india-paper-ledger`` for paper trades.
Amounts named ``*_dollars`` are rupees here.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

DEFAULT_TOP_N = 3

COPIED_FUNCTIONS = (
    "_parse_dt",
    "_get",
    "calculate_pnl",
    "calculate_holding_days",
    "calculate_r_multiple",
    "_pnl_value",
    "calculate_metrics",
    "analyze_patterns",
    "_lesson_row",
    "extract_lessons",
)

_PATTERN_DIMENSIONS = (
    ("by_source_skill", ("_india", "signal_source")),
    ("by_exit_reason", ("exit", "exit_reason")),
    ("by_thesis_type", ("thesis_type",)),
    ("by_sector", ("market_context", "sector")),
    ("by_mechanism_tag", ("mechanism_tag",)),
    ("by_screening_grade", ("origin", "screening_grade")),
)


def _parse_dt(value: str) -> datetime:
    """Parse an ISO 8601 / RFC 3339 string into an aware datetime (assume UTC)."""
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _get(thesis: dict, *keys: str) -> Any:
    """Safely walk nested mappings; return None if any level is missing/None."""
    node: Any = thesis
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def calculate_pnl(thesis: dict) -> tuple[float | None, float | None]:
    """Return (pnl_dollars, pnl_pct) from outcome; either may be None."""
    return _get(thesis, "outcome", "pnl_dollars"), _get(thesis, "outcome", "pnl_pct")


def calculate_holding_days(thesis: dict) -> int | None:
    """Holding period in days: outcome.holding_days, else exit - entry actual_date."""
    explicit = _get(thesis, "outcome", "holding_days")
    if explicit is not None:
        return explicit
    entry_date = _get(thesis, "entry", "actual_date")
    exit_date = _get(thesis, "exit", "actual_date")
    if not entry_date or not exit_date:
        return None
    try:
        return (_parse_dt(exit_date) - _parse_dt(entry_date)).days
    except (ValueError, TypeError):
        return None


def calculate_r_multiple(thesis: dict) -> float | None:
    """R-multiple = realized P&L / positive risk from the recorded stop.

    Risk = (entry.actual_price - exit.stop_loss) * position.shares.
    The recorded stop may have moved since entry. Gap fills or raised stops
    can imply nonpositive risk; omit R rather than reverse the P&L sign.
    Note: stop_loss lives under ``exit`` in the real schema (not ``entry``).
    """
    pnl_dollars, _ = calculate_pnl(thesis)
    entry_price = _get(thesis, "entry", "actual_price")
    stop_loss = _get(thesis, "exit", "stop_loss")
    shares = _get(thesis, "position", "shares")
    if None in (pnl_dollars, entry_price, stop_loss, shares):
        return None
    risk = (entry_price - stop_loss) * shares
    if risk <= 0:
        return None
    return round(pnl_dollars / risk, 4)


def _pnl_value(thesis: dict) -> float | None:
    dollars, pct = calculate_pnl(thesis)
    return dollars if dollars is not None else pct


def calculate_metrics(theses: list[dict]) -> dict:
    """Aggregate performance metrics over CLOSED theses."""
    summary = {
        "total_trades": len(theses),
        "winners": 0,
        "losers": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "expectancy": 0.0,
        "profit_factor": None,
        "total_realized_pnl": 0.0,
        "total_realized_pnl_pct": 0.0,
    }
    metrics = {
        "avg_winner": None,
        "avg_loser": None,
        "largest_winner": None,
        "largest_loser": None,
        "avg_holding_days_winners": None,
        "avg_holding_days_losers": None,
        "r_multiple_avg": None,
        "r_multiple_stdev": None,
        "avg_mae_pct": None,
        "avg_mfe_pct": None,
    }
    if not theses:
        return {"summary": summary, "metrics": metrics}

    winners: list[float] = []
    losers: list[float] = []
    win_hold: list[int] = []
    loss_hold: list[int] = []
    pnl_dollars_all: list[float] = []
    pnl_pct_all: list[float] = []
    r_multiples: list[float] = []
    mae_vals: list[float] = []
    mfe_vals: list[float] = []

    for thesis in theses:
        dollars, pct = calculate_pnl(thesis)
        value = dollars if dollars is not None else pct
        if value is None:
            continue
        pnl_dollars_all.append(dollars if dollars is not None else 0.0)
        if pct is not None:
            pnl_pct_all.append(pct)
        hold = calculate_holding_days(thesis)
        if value > 0:
            summary["winners"] += 1
            winners.append(value)
            if hold is not None:
                win_hold.append(hold)
        elif value < 0:
            summary["losers"] += 1
            losers.append(value)
            if hold is not None:
                loss_hold.append(hold)
        else:
            summary["breakeven"] += 1
        r = calculate_r_multiple(thesis)
        if r is not None:
            r_multiples.append(r)
        # Normalize to the documented sign convention: MAE is adverse (<= 0),
        # MFE is favorable (>= 0). TMC does not clamp, so an always-profitable
        # trade can carry mae_pct > 0 (and vice versa); clamp on read.
        mae = _get(thesis, "outcome", "mae_pct")
        if mae is not None:
            mae_vals.append(min(mae, 0.0))
        mfe = _get(thesis, "outcome", "mfe_pct")
        if mfe is not None:
            mfe_vals.append(max(mfe, 0.0))

    total = summary["total_trades"]
    summary["win_rate"] = round(summary["winners"] / total, 4) if total else 0.0
    summary["total_realized_pnl"] = round(sum(pnl_dollars_all), 2)
    summary["total_realized_pnl_pct"] = round(sum(pnl_pct_all), 4)
    summary["expectancy"] = round(statistics.mean(pnl_dollars_all), 2) if pnl_dollars_all else 0.0
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    if gross_loss > 0:
        summary["profit_factor"] = round(gross_profit / gross_loss, 4)
    elif winners:
        summary["profit_factor"] = None  # all winners, no losses -> undefined

    metrics["avg_winner"] = round(statistics.mean(winners), 2) if winners else None
    metrics["avg_loser"] = round(statistics.mean(losers), 2) if losers else None
    metrics["largest_winner"] = round(max(winners), 2) if winners else None
    metrics["largest_loser"] = round(min(losers), 2) if losers else None
    metrics["avg_holding_days_winners"] = round(statistics.mean(win_hold), 1) if win_hold else None
    metrics["avg_holding_days_losers"] = round(statistics.mean(loss_hold), 1) if loss_hold else None
    if r_multiples:
        metrics["r_multiple_avg"] = round(statistics.mean(r_multiples), 4)
        metrics["r_multiple_stdev"] = (
            round(statistics.stdev(r_multiples), 4) if len(r_multiples) > 1 else 0.0
        )
    if mae_vals:
        metrics["avg_mae_pct"] = round(statistics.mean(mae_vals), 4)
    if mfe_vals:
        metrics["avg_mfe_pct"] = round(statistics.mean(mfe_vals), 4)

    return {"summary": summary, "metrics": metrics}


def analyze_patterns(theses: list[dict]) -> dict:
    """Win/loss breakdown across source skill, exit reason, type, sector, etc."""
    result: dict[str, dict] = {name: {} for name, _ in _PATTERN_DIMENSIONS}
    for thesis in theses:
        value = _pnl_value(thesis)
        if value is None:
            continue
        is_win = value > 0
        is_loss = value < 0
        for name, keys in _PATTERN_DIMENSIONS:
            bucket_key = _get(thesis, *keys)
            if bucket_key is None:
                bucket_key = "unknown"
            bucket = result[name].setdefault(
                bucket_key, {"wins": 0, "losses": 0, "total": 0, "win_rate": 0.0}
            )
            bucket["total"] += 1
            if is_win:
                bucket["wins"] += 1
            elif is_loss:
                bucket["losses"] += 1
    for buckets in result.values():
        for bucket in buckets.values():
            decided = bucket["wins"] + bucket["losses"]
            bucket["win_rate"] = round(bucket["wins"] / decided, 4) if decided else 0.0
    return result


def _lesson_row(thesis: dict) -> dict:
    dollars, pct = calculate_pnl(thesis)
    return {
        "thesis_id": thesis.get("thesis_id"),
        "ticker": thesis.get("ticker"),
        "thesis_type": thesis.get("thesis_type"),
        "pnl_dollars": dollars,
        "pnl_pct": pct,
        "holding_days": calculate_holding_days(thesis),
        "r_multiple": calculate_r_multiple(thesis),
        "exit_reason": _get(thesis, "exit", "exit_reason"),
        "source_skill": _get(thesis, "origin", "skill"),
        "lessons_learned": _get(thesis, "outcome", "lessons_learned"),
    }


def extract_lessons(theses: list[dict], top_n: int = DEFAULT_TOP_N) -> dict:
    """Top winners/losers (by pnl) plus simple process-improvement insights."""
    decided = [t for t in theses if _pnl_value(t) is not None]
    winners = sorted(
        (t for t in decided if _pnl_value(t) > 0),
        key=lambda t: _pnl_value(t),
        reverse=True,
    )
    losers = sorted((t for t in decided if _pnl_value(t) < 0), key=lambda t: _pnl_value(t))

    improvements: list[str] = []
    patterns = analyze_patterns(theses)
    for skill, bucket in patterns["by_source_skill"].items():
        if bucket["losses"] >= 2 and bucket["win_rate"] < 0.5:
            improvements.append(
                f"Source skill '{skill}' had {bucket['losses']} losses "
                f"(win rate {bucket['win_rate']:.0%}) this week — review its setups."
            )
    for reason, bucket in patterns["by_exit_reason"].items():
        if reason == "stop_hit" and bucket["total"] >= 2:
            improvements.append(
                f"{bucket['total']} trades exited via stop_hit — check entry timing/sizing."
            )

    return {
        "top_wins": [_lesson_row(t) for t in winners[:top_n]],
        "top_losses": [_lesson_row(t) for t in losers[:top_n]],
        "process_improvements": improvements,
    }
