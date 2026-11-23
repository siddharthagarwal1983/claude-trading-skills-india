"""Outcome classification and feedback copied unchanged from signal-postmortem.

``RECORDER_FUNCTIONS`` are verbatim copies from
``skills/signal-postmortem/scripts/postmortem_recorder.py`` and
``ANALYZER_FUNCTIONS`` from ``postmortem_analyzer.py``, so India signals are
classified (TRUE_POSITIVE / FALSE_POSITIVE / FALSE_POSITIVE_SEVERE /
REGIME_MISMATCH / NEUTRAL) and scored exactly as US signals are.
``tests/test_postmortem_core_parity.py`` fails if the US code changes and this
copy is not refreshed.

In India records the ``5d``/``20d`` return keys count NSE sessions, not
calendar days (``horizon_unit: nse_sessions``).
"""

import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Optional

RECORDER_FUNCTIONS = (
    "positive_finite_price",
    "calculate_return",
    "classify_outcome",
    "create_postmortem_record",
)
ANALYZER_FUNCTIONS = (
    "finite_number",
    "calculate_skill_metrics",
    "generate_weight_feedback",
    "generate_improvement_backlog",
    "analyze_regime_correlation",
)


def positive_finite_price(value) -> bool:
    """Prices must be real finite positive numbers, never booleans/coerced strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def calculate_return(entry_price: float, exit_price: float) -> float:
    """Calculate percentage return."""
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price


def classify_outcome(
    predicted_direction: str,
    return_pct: float,
    regime_at_signal: str,
    regime_at_exit: str,
    threshold: float = 0.005,
) -> str:
    """
    Classify signal outcome based on realized return and regime context.

    Returns one of: TRUE_POSITIVE, FALSE_POSITIVE, FALSE_POSITIVE_SEVERE,
                    REGIME_MISMATCH, NEUTRAL
    """
    # Check for regime mismatch
    if regime_at_signal != regime_at_exit and regime_at_exit != "UNKNOWN":
        if predicted_direction == "LONG" and return_pct < -0.02:
            return "REGIME_MISMATCH"
        if predicted_direction == "SHORT" and return_pct > 0.02:
            return "REGIME_MISMATCH"

    # Check for neutral (flat) outcome
    if abs(return_pct) < threshold:
        return "NEUTRAL"

    # Check direction match
    direction_match = (predicted_direction == "LONG" and return_pct > 0) or (
        predicted_direction == "SHORT" and return_pct < 0
    )

    if direction_match:
        return "TRUE_POSITIVE"

    # False positive - check severity
    if abs(return_pct) > 0.02:
        return "FALSE_POSITIVE_SEVERE"
    return "FALSE_POSITIVE"


def create_postmortem_record(
    signal: dict,
    realized_returns: dict,
    exit_price: float,
    exit_date: str,
    regime_at_exit: str = "UNKNOWN",
    outcome_notes: str = "",
    *,
    classification_return: Optional[float] = None,
) -> dict:
    """
    Create a postmortem record for a signal.
    """
    signal_id = signal.get("signal_id", "unknown")
    ticker = signal.get("ticker", "")
    signal_date = signal.get("signal_date", "")
    predicted_direction = signal.get("predicted_direction", "LONG")
    source_skill = signal.get("source_skill", "unknown")
    entry_price = signal.get("entry_price", 0.0)
    regime_at_signal = signal.get("regime", "UNKNOWN")

    # Batch classification retains its 5d basis. Manual exits explicitly pass
    # their actual price return without claiming a benchmark observation.
    primary_return = (
        realized_returns.get("5d", 0.0) if classification_return is None else classification_return
    )

    outcome_category = classify_outcome(
        predicted_direction, primary_return, regime_at_signal, regime_at_exit
    )

    # Calculate holding days
    holding_days = 0
    if signal_date and exit_date:
        try:
            sig_dt = datetime.strptime(signal_date, "%Y-%m-%d")
            exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
            holding_days = (exit_dt - sig_dt).days
        except ValueError:
            pass

    return {
        "schema_version": "1.0",
        "postmortem_id": f"pm_{signal_id}",
        "signal_id": signal_id,
        "ticker": ticker,
        "signal_date": signal_date,
        "source_skill": source_skill,
        "predicted_direction": predicted_direction,
        "entry_price": entry_price,
        "realized_returns": realized_returns,
        "exit_price": exit_price,
        "exit_date": exit_date,
        "holding_days": holding_days,
        "outcome_category": outcome_category,
        "regime_at_signal": regime_at_signal,
        "regime_at_exit": regime_at_exit,
        "outcome_notes": outcome_notes,
        "recorded_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }


def finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def calculate_skill_metrics(postmortems: list) -> dict:
    """
    Calculate accuracy and other metrics by source skill.

    Returns dict mapping skill name to metrics.
    """
    skill_data = defaultdict(
        lambda: {
            "true_positive": 0,
            "false_positive": 0,
            "false_positive_severe": 0,
            "regime_mismatch": 0,
            "neutral": 0,
            "total": 0,
            "returns": [],
        }
    )

    for pm in postmortems:
        skill = pm.get("source_skill", "unknown")
        outcome = pm.get("outcome_category", "UNKNOWN")
        returns = pm.get("realized_returns")
        returns_5d = returns.get("5d") if isinstance(returns, dict) else None

        skill_data[skill]["total"] += 1
        if finite_number(returns_5d):
            skill_data[skill]["returns"].append(returns_5d)

        if outcome == "TRUE_POSITIVE":
            skill_data[skill]["true_positive"] += 1
        elif outcome in ("FALSE_POSITIVE", "FALSE_POSITIVE_SEVERE"):
            skill_data[skill]["false_positive"] += 1
            if outcome == "FALSE_POSITIVE_SEVERE":
                skill_data[skill]["false_positive_severe"] += 1
        elif outcome == "REGIME_MISMATCH":
            skill_data[skill]["regime_mismatch"] += 1
        elif outcome == "NEUTRAL":
            skill_data[skill]["neutral"] += 1

    # Calculate derived metrics
    metrics = {}
    for skill, data in skill_data.items():
        total_decisions = data["true_positive"] + data["false_positive"]
        accuracy = data["true_positive"] / total_decisions if total_decisions > 0 else 0.0
        fp_rate = data["false_positive"] / data["total"] if data["total"] > 0 else 0.0
        # statistics.mean avoids an overflowing float sum before division.
        avg_return = mean(data["returns"]) if data["returns"] else None
        if not finite_number(avg_return):
            avg_return = None

        metrics[skill] = {
            "sample_size": data["total"],
            "true_positive": data["true_positive"],
            "false_positive": data["false_positive"],
            "false_positive_severe": data["false_positive_severe"],
            "regime_mismatch": data["regime_mismatch"],
            "neutral": data["neutral"],
            "accuracy": accuracy,
            "false_positive_rate": fp_rate,
            "avg_return_5d": avg_return,
            "return_samples_5d": len(data["returns"]),
        }

    return metrics


def generate_weight_feedback(
    metrics: dict,
    min_sample_size: int = 20,
    baseline_accuracy: float = 0.55,
    sensitivity: float = 0.5,
) -> dict:
    """
    Generate weight adjustment suggestions for edge-signal-aggregator.
    """
    adjustments = []

    for skill, data in metrics.items():
        if data["sample_size"] < min_sample_size:
            continue

        accuracy = data["accuracy"]
        adjustment_factor = (accuracy - baseline_accuracy) * sensitivity

        # Clamp to reasonable range
        new_weight = max(0.3, min(2.0, 1.0 + adjustment_factor))

        # Only suggest if meaningful change
        if abs(1.0 - new_weight) >= 0.05:
            reason = []
            if data["false_positive_rate"] > 0.15:
                reason.append(f"{data['false_positive_rate']:.0%} false positive rate")
            if accuracy < baseline_accuracy:
                reason.append(
                    f"below baseline accuracy ({accuracy:.0%} vs {baseline_accuracy:.0%})"
                )
            if accuracy > 0.65:
                reason.append(f"strong accuracy ({accuracy:.0%})")

            adjustments.append(
                {
                    "skill": skill,
                    "current_weight": 1.0,
                    "suggested_weight": round(new_weight, 2),
                    "reason": "; ".join(reason) if reason else "accuracy-based adjustment",
                    "sample_size": data["sample_size"],
                    "accuracy": round(accuracy, 3),
                    "false_positive_rate": round(data["false_positive_rate"], 3),
                }
            )

    # Determine confidence based on total sample size
    total_samples = sum(m["sample_size"] for m in metrics.values())
    if total_samples >= 100:
        confidence = "HIGH"
    elif total_samples >= 50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "analysis_period": {"days_back": 90, "total_postmortems": total_samples},
        "skill_adjustments": adjustments,
        "confidence": confidence,
        "min_sample_threshold": min_sample_size,
    }


def generate_improvement_backlog(
    metrics: dict, postmortems: list, min_sample_size: int = 15
) -> list:
    """
    Generate skill improvement backlog entries.
    """
    entries = []
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    for skill, data in metrics.items():
        if data["sample_size"] < min_sample_size:
            continue

        # Check for false positive cluster
        if data["false_positive_rate"] > 0.15:
            # Analyze regime correlation
            regime_correlation = analyze_regime_correlation(postmortems, skill)

            severity = "high" if data["false_positive_rate"] > 0.25 else "medium"
            priority_score = int(data["false_positive_rate"] * data["sample_size"] * 100)

            entries.append(
                {
                    "skill": skill,
                    "issue_type": "false_positive_cluster",
                    "severity": severity,
                    "evidence": {
                        "false_positive_rate": round(data["false_positive_rate"], 3),
                        "sample_size": data["sample_size"],
                        "regime_correlation": regime_correlation,
                    },
                    "suggested_action": f"Add regime filter or reduce confidence in {regime_correlation} regime"
                    if regime_correlation != "NONE"
                    else "Review signal generation logic for systematic errors",
                    "priority_score": priority_score,
                    "generated_by": "signal-postmortem",
                    "generated_at": now,
                    "status": "pending",
                }
            )

        # Check for regime sensitivity
        regime_mismatch_rate = (
            data["regime_mismatch"] / data["sample_size"] if data["sample_size"] > 0 else 0
        )
        if regime_mismatch_rate > 0.25:
            entries.append(
                {
                    "skill": skill,
                    "issue_type": "regime_sensitivity",
                    "severity": "medium",
                    "evidence": {
                        "regime_mismatch_rate": round(regime_mismatch_rate, 3),
                        "sample_size": data["sample_size"],
                    },
                    "suggested_action": "Incorporate market regime detection into signal generation",
                    "priority_score": int(regime_mismatch_rate * data["sample_size"] * 80),
                    "generated_by": "signal-postmortem",
                    "generated_at": now,
                    "status": "pending",
                }
            )

        # Check for severe false positives (overconfidence)
        severe_fp_rate = (
            data["false_positive_severe"] / data["sample_size"] if data["sample_size"] > 0 else 0
        )
        if severe_fp_rate > 0.10:
            entries.append(
                {
                    "skill": skill,
                    "issue_type": "overconfidence",
                    "severity": "high",
                    "evidence": {
                        "severe_fp_rate": round(severe_fp_rate, 3),
                        "severe_fp_count": data["false_positive_severe"],
                        "sample_size": data["sample_size"],
                    },
                    "suggested_action": "Review confidence scoring; reduce position size or add confirmation filters",
                    "priority_score": int(severe_fp_rate * data["sample_size"] * 150),
                    "generated_by": "signal-postmortem",
                    "generated_at": now,
                    "status": "pending",
                }
            )

    # Sort by priority score descending
    entries.sort(key=lambda x: x["priority_score"], reverse=True)

    return entries


def analyze_regime_correlation(postmortems: list, skill: str) -> str:
    """
    Analyze which regime has the most false positives for a skill.
    """
    regime_fps = defaultdict(int)
    regime_totals = defaultdict(int)

    for pm in postmortems:
        if pm.get("source_skill") != skill:
            continue

        regime = pm.get("regime_at_signal", "UNKNOWN")
        regime_totals[regime] += 1

        if pm.get("outcome_category", "").startswith("FALSE_POSITIVE"):
            regime_fps[regime] += 1

    # Find regime with highest FP rate
    worst_regime = "NONE"
    worst_rate = 0.0

    for regime, total in regime_totals.items():
        if total < 5:  # Minimum for correlation
            continue
        rate = regime_fps[regime] / total
        if rate > worst_rate and rate > 0.2:  # Threshold for correlation
            worst_rate = rate
            worst_regime = regime

    return worst_regime
