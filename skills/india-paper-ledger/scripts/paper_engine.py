"""Pure fill rules, charges and helpers for the India paper ledger (no I/O).

Fills use official NSE end-of-day bars (bhavcopy): the closing price for
market-on-close orders and the session's open/high/low for limit, stop and
exit triggers. Where one daily bar cannot say what happened first, the rule
taken is the one that is worse for the trader (stop before target, no fill
for a stop-limit that gapped past its cap).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

IST = timezone(timedelta(hours=5, minutes=30), "IST")
MARKET_OPEN = time(9, 15)
CLOSE_ORDER_CUTOFF = time(15, 0)  # NSE closing price = VWAP of 15:00-15:30

ORDER_TYPES = ("CLOSE", "LIMIT", "STOP", "STOP_LIMIT")
STOP_BASES = ("intraday", "close")
SIDES = ("BUY", "SELL")

# Statutory rates for equity delivery on NSE. Kept identical to
# india-position-sizer (a parity test guards drift). Verify against your
# broker's contract note; brokers differ on brokerage and the DP charge.
CHARGES = {
    "stt_rate": Decimal("0.001"),  # 0.1% on buy and on sell (delivery)
    "exchange_txn_rate": Decimal("0.0000297"),  # NSE cash, 0.00297% per leg
    "sebi_fee_rate": Decimal("0.000001"),  # ₹10 per crore per leg
    "stamp_duty_buy_rate": Decimal("0.00015"),  # 0.015% on the buy leg only
    "gst_rate": Decimal("0.18"),  # on brokerage + exchange charge + SEBI fee
}
DEFAULT_DP_CHARGE = 15.93  # per scrip per sell day, incl. GST (typical discount broker)

PAISA = Decimal("0.01")
AMOUNT = re.compile(r"(?:rs\.?|re\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
SPECIAL = re.compile(r"special", re.IGNORECASE)
REVIEW_PURPOSE = re.compile(
    r"demerger|arrangement|amalgamation|rights|capital reduction|consolidation|buy ?back",
    re.IGNORECASE,
)


def _d(value: float | int) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(PAISA, rounding=ROUND_HALF_UP))


def ledger_ticker(symbol: str) -> str:
    """Alphanumeric label for trader-memory-core (its thesis ids reject & and -)."""
    label = re.sub(r"[^A-Z0-9]", "", str(symbol).upper().replace("&", "AND"))
    if not label:
        raise ValueError(f"Symbol {symbol!r} has no letters or digits")
    return label


# --------------------------------------------------------------------------- fills


def entry_fill(
    order_type: str, bar: dict, *, limit: float | None = None, trigger: float | None = None
) -> dict | None:
    """Buy fill for one session bar, or None. Returns {price, basis}."""
    o, h, low, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    if order_type == "CLOSE":
        return {"price": c, "basis": "close"}
    if order_type == "LIMIT":
        if o <= limit:
            return {"price": o, "basis": "open"}
        if low <= limit:
            return {"price": float(limit), "basis": "limit"}
        return None
    if order_type in ("STOP", "STOP_LIMIT"):
        if o >= trigger:
            if order_type == "STOP_LIMIT" and o > limit:
                return None  # gapped past the cap; a daily bar cannot prove a later fill
            return {"price": o, "basis": "open"}
        if h >= trigger:
            return {"price": float(trigger), "basis": "trigger"}
        return None
    raise ValueError(f"Unknown order type {order_type!r}; expected one of {ORDER_TYPES}")


def exit_fill(
    bar: dict,
    *,
    stop: float | None,
    target: float | None,
    stop_basis: str = "intraday",
) -> dict | None:
    """Protective exit for a held long, or None. Returns {price, basis, exit_reason}."""
    if stop_basis not in STOP_BASES:
        raise ValueError(f"stop_basis must be one of {STOP_BASES}")
    o, h, low, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    if target is not None and o >= target:
        return {"price": o, "basis": "target", "exit_reason": "target_hit"}
    if stop is not None:
        if stop_basis == "close":
            if c <= stop:
                return {"price": c, "basis": "close", "exit_reason": "stop_hit"}
        elif o <= stop:
            return {"price": o, "basis": "stop", "exit_reason": "stop_hit"}
        elif low <= stop:
            return {"price": float(stop), "basis": "stop", "exit_reason": "stop_hit"}
    if target is not None and h >= target:
        return {"price": float(target), "basis": "target", "exit_reason": "target_hit"}
    return None


def is_locked(bar: dict) -> bool:
    """One-price session (high == low): treated as locked in a circuit, no fill."""
    return float(bar["high"]) == float(bar["low"])


def apply_slippage(price: float, bps: float, side: str) -> float:
    """Move a fill against the trader by ``bps`` basis points."""
    if bps < 0:
        raise ValueError("slippage must be zero or positive")
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    sign = 1 if side == "BUY" else -1
    return _money(_d(price) * (1 + sign * _d(bps) / Decimal(10_000)))


# --------------------------------------------------------------------------- timing


def first_eligible_date(order_type: str, placed_at: datetime) -> date:
    """First calendar date whose session may fill the order (IST rules).

    A close order placed before 15:00 IST can take that day's close. Any
    other order needs the whole session ahead of it: placed before 09:15 IST
    it can fill that day, otherwise from the next day.
    """
    if placed_at.tzinfo is None:
        raise ValueError("placed_at must be timezone-aware")
    local = placed_at.astimezone(IST)
    cutoff = CLOSE_ORDER_CUTOFF if order_type == "CLOSE" else MARKET_OPEN
    if local.time() < cutoff:
        return local.date()
    return local.date() + timedelta(days=1)


# --------------------------------------------------------------------------- charges


def leg_charges(
    turnover: float, *, buy: bool, brokerage: float = 0.0, dp_charge: float = DEFAULT_DP_CHARGE
) -> dict:
    """Delivery charges for one leg in rupees (DP charge on the sell leg only)."""
    t = _d(turnover)
    brk = _d(brokerage)
    stt = t * CHARGES["stt_rate"]
    txn = t * CHARGES["exchange_txn_rate"]
    sebi = t * CHARGES["sebi_fee_rate"]
    stamp = t * CHARGES["stamp_duty_buy_rate"] if buy else Decimal(0)
    gst = CHARGES["gst_rate"] * (brk + txn + sebi)
    dp = Decimal(0) if buy else _d(dp_charge)
    total = brk + stt + txn + sebi + stamp + gst + dp
    return {
        "turnover": _money(t),
        "brokerage": _money(brk),
        "stt": _money(stt),
        "exchange_txn": _money(txn),
        "sebi_fee": _money(sebi),
        "stamp_duty": _money(stamp),
        "gst": _money(gst),
        "dp_charge": _money(dp),
        "total": _money(total),
    }


def effective_price(price: float, qty: int, charges_total: float, *, side: str) -> float:
    """Per-share price with charges folded in (buy: added, sell: deducted)."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    per_share = _d(charges_total) / _d(qty)
    value = _d(price) + per_share if side == "BUY" else _d(price) - per_share
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- corporate actions


def dividend_per_share(purpose: str) -> float:
    """Regular + special rupees per share in one NSE dividend line."""
    total = 0.0
    for part in re.split(r"/|;|\band\b", purpose or ""):
        match = AMOUNT.search(part)
        if match:
            total += float(match.group(1))
    return round(total, 4)


def split_actions(actions: list) -> tuple:
    """(dividends, share-changing or review-worthy actions) from NSE actions."""
    dividends, review = [], []
    for action in actions or []:
        factor = action.get("factor")
        kind = str(action.get("action_type") or "").upper()
        purpose = str(action.get("purpose") or "")
        if factor is not None and abs(float(factor) - 1.0) > 1e-9:
            review.append(action)
        elif kind == "OTHER" and REVIEW_PURPOSE.search(purpose):
            review.append(action)
        elif kind == "DIVIDEND" and dividend_per_share(purpose) > 0:
            dividends.append(action)
    return dividends, review
