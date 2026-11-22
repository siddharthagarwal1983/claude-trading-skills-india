"""Risk-level -> portfolio action mapping for Indian equity exposure.

NSE has no leveraged index ETFs, so the US TQQQ/QQQ policy is replaced by:

* SWING_BOOK: a portfolio of individual NSE stocks held for days to weeks.
  Single stocks fall harder than the index in distribution phases, so
  exposure is cut early and stops are tightened.
* NIFTY_ETF: a 1x index fund or ETF (e.g. a NIFTY 50 ETF). Exposure cuts are
  milder; distribution first stops new adds.
"""

from __future__ import annotations

from models import PortfolioAction

_SWING_BOOK_POLICY = {
    "NORMAL": ("HOLD_OR_FOLLOW_BASE_STRATEGY", 100, None),
    "CAUTION": ("AVOID_NEW_ADDS", 75, 8),
    "HIGH": ("REDUCE_EXPOSURE", 50, 6),
    "SEVERE": ("RAISE_CASH", 25, 4),
}

_NIFTY_ETF_POLICY = {
    "NORMAL": ("HOLD_OR_FOLLOW_BASE_STRATEGY", 100, None),
    "CAUTION": ("AVOID_NEW_ADDS", 100, 8),
    "HIGH": ("REDUCE_EXPOSURE", 75, 6),
    "SEVERE": ("REDUCE_EXPOSURE_OR_HEDGE", 50, 5),
}

_POLICIES = {"SWING_BOOK": _SWING_BOOK_POLICY, "NIFTY_ETF": _NIFTY_ETF_POLICY}

_RATIONALE = {
    "SWING_BOOK": (
        "Individual NSE stocks usually fall further than the index once institutions "
        "distribute, and smaller names can be gapped through price bands. Exposure is "
        "cut early and trailing stops tightened as distribution clusters."
    ),
    "NIFTY_ETF": (
        "A 1x NIFTY index ETF tracks the market itself. Distribution first stops new "
        "adds; exposure is reduced only when clusters deepen."
    ),
}

_ALTERNATIVES = {
    "SWING_BOOK": {
        "HIGH": "TIGHTEN_STOPS_AND_SELL_LAGGARDS",
        "SEVERE": "MOVE_PROCEEDS_TO_LIQUID_FUND_OR_CASH",
    },
    "NIFTY_ETF": {"SEVERE": "PARTIAL_HEDGE_WITH_NIFTY_PUTS_IF_PERMITTED"},
}


def supported_instruments() -> tuple:
    return tuple(sorted(_POLICIES))


def generate_portfolio_action(
    risk_level: str,
    instrument: str,
    current_exposure_pct: int,
    base_trailing_stop_pct: int,
) -> PortfolioAction:
    """Return the recommended portfolio action for the given risk level."""
    key = instrument.upper()
    if key not in _POLICIES:
        raise ValueError(
            f"Unknown instrument {instrument!r}; expected {', '.join(supported_instruments())}"
        )
    policy = _POLICIES[key]
    if risk_level not in policy:
        raise ValueError(f"Unknown risk_level: {risk_level}")
    action, target, cap = policy[risk_level]
    trailing_stop = base_trailing_stop_pct if cap is None else min(base_trailing_stop_pct, cap)
    return PortfolioAction(
        instrument=key,
        recommended_action=action,
        current_exposure_pct=current_exposure_pct,
        target_exposure_pct=target,
        exposure_delta_pct=target - current_exposure_pct,
        trailing_stop_pct=trailing_stop,
        alternative_action=_ALTERNATIVES[key].get(risk_level),
        rationale=_RATIONALE[key],
    )
