"""Pure dividend calculations for NSE stocks (no I/O).

NSE corporate actions describe dividends in free text, e.g.
"Dividend - Rs 6.75 Per Share /Special Dividend - Rs 2.75 Per Share". This
module parses the amounts, restates them per today's share count (bonuses and
splits after a dividend shrink its per-share amount), groups them by Indian
fiscal year (April-March, labelled by the year it ends), and scores dividend
quality.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

AMOUNT = re.compile(r"(?:rs\.?|re\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
SPECIAL = re.compile(r"special", re.IGNORECASE)


def parse_dividend_amounts(purpose: str) -> tuple:
    """(regular, special) rupees per share announced in one corporate-action line."""
    regular = special = 0.0
    for part in re.split(r"/|;|\band\b", purpose or ""):
        match = AMOUNT.search(part)
        if not match:
            continue
        value = float(match.group(1))
        if SPECIAL.search(part):
            special += value
        else:
            regular += value
    return round(regular, 4), round(special, 4)


def fiscal_year(day: date) -> int:
    """Indian fiscal year label: 1 Apr 2025 - 31 Mar 2026 is FY2026."""
    return day.year + 1 if day.month >= 4 else day.year


def adjusted_dividends(actions: list) -> list:
    """Dividend events with amounts restated per today's share (after later bonuses/splits)."""
    factors = []
    for a in actions:
        factor = a.get("factor")
        if (
            a.get("action_type") != "DIVIDEND"
            and isinstance(factor, (int, float))
            and 0 < factor != 1
        ):
            factors.append((a["ex_date"], float(factor)))
    events = []
    for a in actions:
        if a.get("action_type") != "DIVIDEND":
            continue
        regular, special = parse_dividend_amounts(a.get("purpose", ""))
        if regular == 0 and special == 0:
            continue
        scale = math.prod(f for d, f in factors if d > a["ex_date"])
        events.append(
            {
                "ex_date": a["ex_date"],
                "regular": round(regular * scale, 4),
                "special": round(special * scale, 4),
                "as_announced": {"regular": regular, "special": special},
                "adjustment": scale,
                "purpose": a.get("purpose", ""),
            }
        )
    return sorted(events, key=lambda e: e["ex_date"])


def dividend_metrics(events: list, price: float, as_of: date, history_start: date) -> dict:
    """TTM yield, fiscal-year totals, consistency and growth from adjusted events."""
    ttm_start = (as_of - timedelta(days=365)).isoformat()
    ttm = [e for e in events if ttm_start < e["ex_date"] <= as_of.isoformat()]
    ttm_regular = round(sum(e["regular"] for e in ttm), 4)
    ttm_special = round(sum(e["special"] for e in ttm), 4)
    first_full_fy = fiscal_year(history_start) + (
        0 if (history_start.month, history_start.day) == (4, 1) else 1
    )
    last_full_fy = fiscal_year(as_of) - 1
    years = list(range(first_full_fy, last_full_fy + 1))
    by_fy = {fy: 0.0 for fy in years}
    for e in events:
        fy = fiscal_year(date.fromisoformat(e["ex_date"]))
        if fy in by_fy:
            by_fy[fy] += e["regular"]
    totals = {f"FY{fy}": round(v, 4) for fy, v in by_fy.items()}
    values = [by_fy[fy] for fy in years]
    paying = sum(1 for v in values if v > 0)
    cuts = sum(1 for prev, cur in zip(values, values[1:]) if prev > 0 and cur < prev * 0.95)
    increases = 0
    for prev, cur in zip(values, values[1:]):
        increases = increases + 1 if prev > 0 and cur > prev * 1.0 else 0
    nonzero = [(i, v) for i, v in enumerate(values) if v > 0]
    cagr = None
    if len(nonzero) >= 2 and nonzero[-1][0] > nonzero[0][0]:
        (i0, v0), (i1, v1) = nonzero[0], nonzero[-1]
        cagr = round(((v1 / v0) ** (1 / (i1 - i0)) - 1) * 100, 2)
    return {
        "ttm_regular_dividend": ttm_regular,
        "ttm_special_dividend": ttm_special,
        "regular_yield_pct": round(ttm_regular / price * 100, 2) if price else None,
        "total_yield_pct": round((ttm_regular + ttm_special) / price * 100, 2) if price else None,
        "fiscal_year_totals": totals,
        "complete_fiscal_years": len(years),
        "paying_years": paying,
        "dividend_cuts": cuts,
        "consecutive_increases": increases,
        "dividend_cagr_pct": cagr,
    }


def rsi(closes: list, period: int = 14) -> float | None:
    """Wilder RSI of the last close."""
    if len(closes) <= period:
        return None
    gains = [max(0.0, b - a) for a, b in zip(closes, closes[1:])]
    losses = [max(0.0, a - b) for a, b in zip(closes, closes[1:])]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1)


def score(metrics: dict, rsi_value: float | None, pe: float | None) -> dict:
    """0-100 dividend-quality score; valuation is rescaled away when P/E is unknown."""
    y = metrics.get("regular_yield_pct") or 0
    parts = {
        "yield": 30
        if y >= 5
        else 25
        if y >= 4
        else 20
        if y >= 3
        else 12
        if y >= 2
        else 5
        if y >= 1
        else 0
    }
    years, paying = metrics["complete_fiscal_years"], metrics["paying_years"]
    parts["consistency"] = (
        25 if years and paying == years else 12 if years and paying == years - 1 else 0
    )
    cagr = metrics.get("dividend_cagr_pct")
    growth = 0 if cagr is None else 20 if cagr >= 10 else 14 if cagr >= 5 else 8 if cagr >= 0 else 0
    parts["growth"] = max(0, growth - 5 * metrics["dividend_cuts"])
    parts["pullback"] = (
        0 if rsi_value is None else 10 if rsi_value <= 40 else 5 if rsi_value <= 50 else 0
    )
    maximum = 85
    if pe is not None and pe > 0:
        parts["valuation"] = 15 if pe <= 12 else 11 if pe <= 18 else 6 if pe <= 25 else 2
        maximum = 100
    total = round(sum(parts.values()) * 100 / maximum, 1)
    grade = "A" if total >= 75 else "B" if total >= 60 else "C" if total >= 45 else "D"
    return {"score": total, "grade": grade, "components": parts, "valuation_scored": maximum == 100}
