"""Trade prices, risk and rupee position sizing for NSE breakout plans (no I/O).

Follows the US breakout-trade-planner rules (signal entry just above the VCP
pivot, a capped chase price, a stop under the last contraction low, rating-band
risk multipliers) with India changes: prices sit on the NSE tick grid,
round-trip delivery charges count inside the risk budget, and size is also
capped by the stock's average daily turnover and the cash available.
"""

from __future__ import annotations

import math

from _india_data import DEFAULT_DP_CHARGE, delivery_leg_charges, round_to_tick

CRORE = 10_000_000

# Same bands and multipliers as the US breakout-trade-planner.
SIZING_MULTIPLIER: dict[str, float] = {
    "textbook": 1.75,
    "strong": 1.0,
    "good": 0.75,
    "developing": 0.0,
    "weak": 0.0,
}


def derive_trade_prices(
    pivot: float,
    last_contraction_low: float,
    pivot_buffer_pct: float = 0.1,
    max_chase_pct: float = 2.0,
    stop_buffer_pct: float = 1.0,
) -> tuple[float, float, float]:
    """(signal_entry, worst_entry, stop_loss) on the NSE tick grid.

    The trigger rounds up (never below the intended buffer), the chase cap
    and the stop round down (never more chase or less room than intended).
    """
    if pivot <= 0:
        raise ValueError(f"pivot must be positive, got {pivot}")
    if last_contraction_low <= 0:
        raise ValueError(f"last_contraction_low must be positive, got {last_contraction_low}")
    if pivot_buffer_pct > max_chase_pct:
        raise ValueError(
            f"pivot_buffer_pct ({pivot_buffer_pct}) must be <= max_chase_pct ({max_chase_pct})"
        )
    signal_entry = round_to_tick(pivot * (1 + pivot_buffer_pct / 100), "up")
    worst_entry = max(signal_entry, round_to_tick(pivot * (1 + max_chase_pct / 100), "down"))
    stop_loss = round_to_tick(last_contraction_low * (1 - stop_buffer_pct / 100), "down")
    if stop_loss >= signal_entry:
        raise ValueError(f"stop_loss ({stop_loss}) must be below signal_entry ({signal_entry})")
    return signal_entry, worst_entry, stop_loss


def calculate_risks(signal_entry: float, worst_entry: float, stop_loss: float) -> tuple:
    """(risk_pct_signal, risk_pct_worst): price distance to the stop."""
    risk_pct_signal = (signal_entry - stop_loss) / signal_entry * 100
    risk_pct_worst = (worst_entry - stop_loss) / worst_entry * 100
    return round(risk_pct_signal, 2), round(risk_pct_worst, 2)


def calculate_r_multiples(
    entry: float, stop_loss: float, multiples: tuple = (1.0, 2.0, 3.0)
) -> dict:
    """nR targets (entry + n x (entry - stop)) on the tick grid."""
    r = entry - stop_loss
    return {f"{m}R": round_to_tick(entry + m * r, "nearest") for m in multiples}


def get_rating_band(composite_score: float) -> str:
    if composite_score >= 90:
        return "textbook"
    if composite_score >= 80:
        return "strong"
    if composite_score >= 70:
        return "good"
    if composite_score >= 60:
        return "developing"
    return "weak"


def get_sizing_multiplier(rating_band: str) -> float:
    return SIZING_MULTIPLIER.get(rating_band, 0.0)


def _largest(predicate, upper: int) -> int:
    """Largest n in [0, upper] with predicate(n) true (predicate monotone, true at 0)."""
    lo, hi = 0, max(0, upper)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if predicate(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def size_position(
    *,
    worst_entry: float,
    stop_loss: float,
    account_size: float,
    risk_pct: float,
    sizing_multiplier: float,
    max_position_pct: float = 10.0,
    max_sector_pct: float = 30.0,
    current_sector_exposure_pct: float = 0.0,
    avg_turnover_cr: float | None = None,
    max_adv_pct: float = 1.0,
    cash_available: float | None = None,
    brokerage: float = 0.0,
    dp_charge: float = DEFAULT_DP_CHARGE,
) -> dict:
    """Whole-share size whose loss at the stop, charges included, fits the budget.

    Sized at the worst (capped) entry. Caps: max position %, remaining sector
    room, ``max_adv_pct`` of average daily rupee turnover, and cash (position
    plus buy charges).
    """
    if not worst_entry > stop_loss > 0:
        raise ValueError(f"need worst_entry ({worst_entry}) > stop_loss ({stop_loss}) > 0")
    effective_risk_pct = risk_pct * sizing_multiplier
    if effective_risk_pct <= 0:
        return {
            "shares": 0,
            "position_value": 0.0,
            "buy_charges": 0.0,
            "price_risk_inr": 0.0,
            "risk_inr": 0.0,
            "effective_risk_pct": 0.0,
            "binding_constraint": "sizing_multiplier_zero",
            "constraints_applied": [],
        }

    def buy_cost(n: int) -> float:
        return delivery_leg_charges(n * worst_entry, buy=True, brokerage=brokerage)["total"]

    def risk_at_stop(n: int) -> float:
        sell = delivery_leg_charges(
            n * stop_loss, buy=False, brokerage=brokerage, dp_charge=dp_charge
        )["total"]
        return n * (worst_entry - stop_loss) + buy_cost(n) + sell

    budget = account_size * effective_risk_pct / 100
    upper = math.floor(budget / (worst_entry - stop_loss))
    risk_shares = _largest(lambda n: n == 0 or risk_at_stop(n) <= budget, upper)

    constraints = [
        {
            "type": "max_position_pct",
            "limit": max_position_pct,
            "max_shares": math.floor(account_size * max_position_pct / 100 / worst_entry),
        },
        {
            "type": "max_sector_pct",
            "limit": max_sector_pct,
            "current": current_sector_exposure_pct,
            "max_shares": max(
                0,
                math.floor(
                    max(0.0, max_sector_pct - current_sector_exposure_pct)
                    / 100
                    * account_size
                    / worst_entry
                ),
            ),
        },
    ]
    if avg_turnover_cr is not None and avg_turnover_cr > 0:
        constraints.append(
            {
                "type": "max_adv_pct",
                "limit": max_adv_pct,
                "avg_turnover_cr": avg_turnover_cr,
                "max_shares": math.floor(avg_turnover_cr * CRORE * max_adv_pct / 100 / worst_entry),
            }
        )
    if cash_available is not None:
        cash = max(0.0, cash_available)
        constraints.append(
            {
                "type": "cash",
                "limit": round(cash, 2),
                "max_shares": _largest(
                    lambda n: n == 0 or n * worst_entry + buy_cost(n) <= cash,
                    math.floor(cash / worst_entry),
                ),
            }
        )

    final = max(0, min([risk_shares] + [c["max_shares"] for c in constraints]))
    binding = None
    for c in constraints:
        c["binding"] = c["max_shares"] == final and final < risk_shares
        if c["binding"] and binding is None:
            binding = c["type"]
    return {
        "shares": final,
        "position_value": round(final * worst_entry, 2),
        "buy_charges": round(buy_cost(final), 2) if final else 0.0,
        "price_risk_inr": round(final * (worst_entry - stop_loss), 2),
        "risk_inr": round(risk_at_stop(final), 2) if final else 0.0,
        "effective_risk_pct": round(effective_risk_pct, 4),
        "binding_constraint": binding,
        "constraints_applied": constraints,
    }
