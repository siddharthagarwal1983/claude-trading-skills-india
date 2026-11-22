"""Risk classification for individual NSE indexes and their combination."""

from __future__ import annotations

from models import IndexResult, RiskThresholds

_RISK_ORDER = {"NORMAL": 0, "CAUTION": 1, "HIGH": 2, "SEVERE": 3}


def classify_risk(
    d5: int,
    d15: int,
    d25: int,
    market_below_ma: bool | None,
    thresholds: RiskThresholds,
) -> str:
    """Classify a single index's risk level.

    market_below_ma may be True | False | None. None means MA could not be
    computed (insufficient data); SEVERE escalation is skipped in that case.
    """
    if d25 >= thresholds.severe_d25 or d15 >= thresholds.severe_d15:
        return "SEVERE"
    if market_below_ma is True and d25 >= thresholds.severe_ma_d25:
        return "SEVERE"
    if d25 >= thresholds.high_d25 or d15 >= thresholds.high_d15 or d5 >= thresholds.high_d5:
        return "HIGH"
    if d25 >= thresholds.caution_d25:
        return "CAUTION"
    return "NORMAL"


def combine_index_risks(results: list[IndexResult]) -> str:
    """Combine per-index risks into an overall level.

    NSE policy (large-cap and broad-market indexes are peers):
      - any SEVERE -> SEVERE
      - two or more indexes at CAUTION or worse -> at least HIGH
        (distribution confirmed across large caps and the broad market)
      - otherwise: the highest single-index level
    """
    if not results:
        raise ValueError("no index results to combine")
    levels = [r.risk_level for r in results]
    if "SEVERE" in levels:
        return "SEVERE"
    highest = max(levels, key=lambda lv: _RISK_ORDER[lv])
    stressed = sum(1 for lv in levels if _RISK_ORDER[lv] >= _RISK_ORDER["CAUTION"])
    if stressed >= 2 and _RISK_ORDER[highest] < _RISK_ORDER["HIGH"]:
        return "HIGH"
    return highest
