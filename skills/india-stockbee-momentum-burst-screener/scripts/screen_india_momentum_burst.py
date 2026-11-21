#!/usr/bin/env python3
"""
India Stockbee Momentum Burst Screener (NSE)

Screens NSE equities for Stockbee-style 3-5 day Momentum Burst candidates with
the same scoring as the US screener (4% breakout, range expansion, prior range
contraction, close location, failure filters, risk distance), tuned for India:

  - Free official NSE end-of-day data, adjusted for bonuses and splits.
  - NSE trading calendar (XBOM schedule + Muhurat sessions, 19:00 IST cutoff).
  - Rupee breakout (close - open >= ₹ threshold AND >= a minimum %), replacing
    the US $0.90 dollar breakout, which is meaningless across Indian price levels.
  - Liquidity gate on trigger-day rupee turnover (default ₹2 crore).
  - Soft warning when the close sits at the day high on a 5/10/20% move, a sign
    the stock may have been locked at its upper circuit (buyers unfilled).

Input modes:
  A. Nifty universe: --universe nifty500
  B. Explicit symbols: --symbols TATAMOTORS TRENT
  C. Symbols file: --universe-file my_list.csv
  D. Offline OHLCV JSON: --prices-json data/daily_ohlcv.json

Output:
  india_stockbee_momentum_burst_YYYY-MM-DD_HHMMSS.json / .md in --output-dir
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import _india_data as india

CRORE = 10_000_000
CIRCUIT_BANDS = (5.0, 10.0, 20.0)
# Replaced in tests; the NSE calendar needs pandas-market-calendars at runtime.
SESSIONS_FN = india.nse_sessions
_SESSION_CACHE: dict = {}


@dataclass
class Bar:
    """Normalized daily OHLCV bar."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class BaseProfile:
    """Prior-base metrics computed from bars before the trigger day."""

    prior_base_days: int
    base_width_pct: float
    avg_prior_range_pct: float
    volume_dry_up: bool


def normalize_symbol(value: Any) -> str:
    """Normalize an NSE symbol (upper case; dots become dashes)."""
    if value is None:
        return ""
    symbol = str(value).strip().upper()
    if not symbol or symbol in {"NAN", "NULL", "NONE"}:
        return ""
    return symbol.replace(".", "-")


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_round(value: Any, digits: int = 2) -> float:
    return round(to_float(value), digits)


def _iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError) as exc:
        raise ValueError("date must be canonical YYYY-MM-DD") from exc


def _date_arg(value: str) -> str:
    try:
        _iso_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _age_arg(value: str) -> int:
    try:
        count = int(value)
        if count < 0:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bar age must be a non-negative integer") from exc
    return count


def _eligible_bars(bars: list[Bar], args: argparse.Namespace):
    expected = getattr(args, "_expected_session", None)
    if expected is None:
        expected = resolve_expected_session(getattr(args, "as_of", None))
    allowed = getattr(args, "max_bar_age_sessions", 0)
    if type(allowed) is not int or allowed < 0:
        raise ValueError("max_bar_age_sessions must be a non-negative integer")
    info = {
        "expected_session": expected.isoformat(),
        "max_bar_age_sessions": allowed,
        "latest_bar_date": None,
        "bar_age_sessions": None,
    }
    try:
        for bar in bars:
            _iso_date(bar.date)
    except ValueError:
        return [], info, ["invalid_price_date"]
    eligible = sorted(
        (bar for bar in bars if bar.date <= info["expected_session"]),
        key=lambda bar: bar.date,
        reverse=True,
    )
    history_days = getattr(args, "history_days", None)
    if history_days is not None:
        eligible = eligible[:history_days]
    if not eligible:
        return [], info, ["no_completed_price_data"]
    info["latest_bar_date"] = eligible[0].date
    age = _bar_age(_iso_date(eligible[0].date), expected)
    info["bar_age_sessions"] = age
    if age is None:
        return eligible, info, ["invalid_price_date"]
    return eligible, info, ["stale_price_data"] if age > allowed else []


def normalize_bars(raw_bars: list[dict[str, Any]], limit: int | None = None) -> list[Bar]:
    """Normalize raw OHLCV dicts into most-recent-first Bar objects."""
    bars: list[Bar] = []
    invalid_dates: list[Bar] = []
    for row in raw_bars or []:
        if not isinstance(row, dict):
            continue
        bar = Bar(
            date=str(row.get("date") or row.get("timestamp") or ""),
            open=to_float(row.get("open")),
            high=to_float(row.get("high")),
            low=to_float(row.get("low")),
            close=to_float(row.get("close") or row.get("adjClose")),
            volume=to_int(row.get("volume")),
        )
        try:
            _iso_date(bar.date)
        except ValueError:
            # Keep unplaceable rows even when OHLC is unusable or beyond the limit.
            # Analysis must reject the symbol rather than silently hide bad dates.
            invalid_dates.append(bar)
            continue
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            continue
        if bar.high < bar.low:
            continue
        bars.append(bar)

    bars.sort(key=lambda b: b.date, reverse=True)
    if limit is not None:
        bars = bars[:limit]
    return invalid_dates + bars


def read_universe_file(path: str) -> list[str]:
    """Read symbols from CSV, JSON, or plain-text file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        values: list[Any]
        if isinstance(data, list):
            values = data
        elif isinstance(data, dict):
            values = data.get("symbols") or data.get("universe") or data.get("tickers") or []
        else:
            values = []
        symbols = []
        for item in values:
            if isinstance(item, dict):
                symbols.append(normalize_symbol(item.get("symbol") or item.get("ticker")))
            else:
                symbols.append(normalize_symbol(item))
        return sorted({s for s in symbols if s})

    if p.suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames:
            lower_fields = {f.lower(): f for f in reader.fieldnames}
            symbol_field = (
                lower_fields.get("symbol")
                or lower_fields.get("ticker")
                or lower_fields.get("tickers")
                or reader.fieldnames[0]
            )
            return sorted({normalize_symbol(row.get(symbol_field)) for row in reader if row})

    symbols = []
    for line in text.splitlines():
        token = line.split(",")[0].strip()
        if token and not token.startswith("#"):
            symbols.append(normalize_symbol(token))
    return sorted({s for s in symbols if s})


def read_prices_json(path: str) -> dict[str, list[Bar]]:
    """Read offline OHLCV JSON in one of several common shapes."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_symbol: dict[str, list[dict[str, Any]]] = {}

    if isinstance(data, dict) and "symbols" in data and isinstance(data["symbols"], dict):
        data = data["symbols"]

    if isinstance(data, dict):
        for symbol, value in data.items():
            if isinstance(value, dict) and isinstance(value.get("historical"), list):
                by_symbol[normalize_symbol(symbol)] = value["historical"]
            elif isinstance(value, list):
                by_symbol[normalize_symbol(symbol)] = value
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = normalize_symbol(item.get("symbol") or item.get("ticker"))
            historical = item.get("historical") or item.get("bars") or item.get("prices")
            if symbol and isinstance(historical, list):
                by_symbol[symbol] = historical

    normalized: dict[str, list[Bar]] = {}
    for symbol, rows in by_symbol.items():
        bars = normalize_bars(rows)
        if symbol and bars:
            normalized[symbol] = bars
    return normalized


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct_change(new: float, old: float) -> float:
    if old <= 0:
        return 0.0
    return ((new / old) - 1.0) * 100.0


def close_location_pct(bar: Bar) -> float:
    day_range = bar.high - bar.low
    if day_range <= 0:
        return 50.0
    return ((bar.close - bar.low) / day_range) * 100.0


def up_streak_before_trigger(bars: list[Bar], max_days: int = 5) -> int:
    """Count consecutive up closes immediately before the latest trigger bar."""
    streak = 0
    for idx in range(1, min(len(bars) - 1, max_days) + 1):
        if bars[idx].close > bars[idx + 1].close:
            streak += 1
        else:
            break
    return streak


def has_recent_breakdown(
    bars: list[Bar], lookback_days: int = 5, threshold_pct: float = -4.0
) -> bool:
    """Return True if any prior day had a close-to-close breakdown at/below threshold."""
    end = min(len(bars) - 1, lookback_days + 1)
    for idx in range(1, end):
        if pct_change(bars[idx].close, bars[idx + 1].close) <= threshold_pct:
            return True
    return False


def detect_base_profile(
    bars: list[Bar],
    min_base_days: int,
    max_base_days: int,
    max_base_width_pct: float,
    max_prior_avg_range_pct: float,
) -> BaseProfile:
    """Detect the best simple prior consolidation window before the trigger day."""
    if len(bars) < min_base_days + 2:
        return BaseProfile(0, 0.0, 0.0, False)

    best: BaseProfile | None = None
    longest_window = min(max_base_days, len(bars) - 1)
    for window in range(min_base_days, longest_window + 1):
        prior = bars[1 : 1 + window]
        ref_close = prior[0].close
        if ref_close <= 0:
            continue
        base_width_pct = (
            (max(b.high for b in prior) - min(b.low for b in prior)) / ref_close
        ) * 100
        avg_range_pct = average([((b.high - b.low) / b.close) * 100 for b in prior if b.close > 0])
        if base_width_pct <= max_base_width_pct and avg_range_pct <= max_prior_avg_range_pct:
            # Compare base volume with the older pre-base volume, when available.
            base_vol = average([float(b.volume) for b in prior])
            older = bars[1 + window : 1 + window * 2]
            older_vol = average([float(b.volume) for b in older]) if older else 0.0
            volume_dry_up = bool(older_vol and base_vol <= older_vol * 0.85)
            candidate = BaseProfile(window, base_width_pct, avg_range_pct, volume_dry_up)
            if best is None:
                best = candidate
            elif candidate.prior_base_days > best.prior_base_days:
                best = candidate
            elif (
                candidate.prior_base_days == best.prior_base_days
                and candidate.base_width_pct < best.base_width_pct
            ):
                best = candidate

    if best:
        return best

    fallback_window = min(longest_window, max(min_base_days, 1))
    prior = bars[1 : 1 + fallback_window]
    ref_close = prior[0].close if prior else 1.0
    width = (
        ((max(b.high for b in prior) - min(b.low for b in prior)) / ref_close) * 100
        if prior
        else 0.0
    )
    avg_range = average([((b.high - b.low) / b.close) * 100 for b in prior if b.close > 0])
    return BaseProfile(0, width, avg_range, False)


def volume_score(profile: TriggerProfile) -> int:
    best_ratio = max(profile.volume_ratio_1d, profile.volume_ratio_20d)
    if best_ratio >= 3.0:
        return 15
    if best_ratio >= 2.0:
        return 12
    if best_ratio >= 1.5:
        return 9
    if best_ratio >= 1.0:
        return 6
    return 0


def setup_score(base: BaseProfile, prev_bar: Bar, args: argparse.Namespace) -> int:
    score = 0
    if base.prior_base_days >= 10:
        score += 10
    elif base.prior_base_days >= 5:
        score += 8
    elif base.prior_base_days >= 3:
        score += 6
    elif base.prior_base_days > 0:
        score += 3

    if base.base_width_pct and base.base_width_pct <= 8:
        score += 7
    elif base.base_width_pct <= 12:
        score += 5
    elif base.base_width_pct <= args.max_base_width_pct:
        score += 3

    prev_range_pct = (
        ((prev_bar.high - prev_bar.low) / prev_bar.close) * 100 if prev_bar.close > 0 else 0
    )
    if prev_range_pct <= args.narrow_prior_day_range_pct:
        score += 5
    elif prev_bar.close < prev_bar.open:
        score += 4

    if base.volume_dry_up:
        score += 3

    return min(25, score)


def close_quality_score(location_pct: float) -> int:
    if location_pct >= 90:
        return 10
    if location_pct >= 80:
        return 9
    if location_pct >= 70:
        return 7
    if location_pct >= 60:
        return 5
    if location_pct >= 50:
        return 3
    return 0


def risk_distance_score(risk_pct_to_stop: float) -> int:
    if risk_pct_to_stop <= 0:
        return 0
    if risk_pct_to_stop <= 2.5:
        return 15
    if risk_pct_to_stop <= 4.0:
        return 12
    if risk_pct_to_stop <= 6.0:
        return 8
    if risk_pct_to_stop <= 8.0:
        return 5
    if risk_pct_to_stop <= 10.0:
        return 2
    return 0


def failure_filter_score(
    profile: TriggerProfile,
    base: BaseProfile,
    prior_up_streak: int,
    recent_breakdown: bool,
    args: argparse.Namespace,
) -> tuple[int, list[str]]:
    score = 10
    reasons: list[str] = []
    if prior_up_streak >= 3:
        score -= 4
        reasons.append("prior_3day_runup")
    if recent_breakdown:
        score -= 4
        reasons.append("recent_4pct_breakdown")
    if base.base_width_pct > args.max_base_width_pct:
        score -= 3
        reasons.append("wide_prior_base")
    if profile.close_location_pct < 50:
        score -= 2
        reasons.append("weak_close_location")
    return max(0, score), reasons


def market_gate_score(market_gate: str) -> int:
    if market_gate == "allowed":
        return 5
    if market_gate == "neutral":
        return 3
    return 0


def score_to_rating(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "A-"
    if score >= 70:
        return "B"
    if score >= 55:
        return "Watch"
    return "Reject"


def score_to_state(score: int, market_gate: str, hard_rejected: bool) -> str:
    if hard_rejected:
        return "REJECTED"
    if market_gate == "restrictive" and score >= 70:
        return "MANUAL_REVIEW_ONLY"
    if score >= 80:
        return "ACTIONABLE_DAY1"
    if score >= 70:
        return "MANUAL_REVIEW"
    if score >= 55:
        return "WATCH_ONLY"
    return "REJECTED"


def sort_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state_priority = {
        "ACTIONABLE_DAY1": 0,
        "MANUAL_REVIEW": 1,
        "MANUAL_REVIEW_ONLY": 2,
        "WATCH_ONLY": 3,
        "REJECTED": 4,
    }
    return sorted(
        results,
        key=lambda row: (state_priority.get(row.get("state"), 9), -row.get("setup_score", 0)),
    )


@dataclass
class TriggerProfile:
    """Trigger metrics for the latest bar."""

    trigger_tags: list[str]
    primary_trigger: str
    day_gain_pct: float
    rupee_gain: float
    current_range_pct: float
    current_range_rupees: float
    volume_ratio_1d: float
    volume_ratio_20d: float
    close_location_pct: float
    prev_day_gain_pct: float


# ---------------------------------------------------------------------------
# NSE calendar
# ---------------------------------------------------------------------------


def _sessions(start: date, end: date) -> list[date]:
    key = (id(SESSIONS_FN), start, end)
    if key not in _SESSION_CACHE:
        _SESSION_CACHE[key] = SESSIONS_FN(start, end)
    return _SESSION_CACHE[key]


def resolve_expected_session(as_of: str | None, *, now: datetime | None = None) -> date:
    """Latest NSE session whose bhavcopy is final, or an earlier explicit cutoff."""
    now = now if now is not None else datetime.now(timezone.utc)
    completed = india.latest_completed_session(now, sessions_fn=SESSIONS_FN)
    if as_of is None:
        return completed
    requested_day = _iso_date(as_of)
    earlier = [d for d in _sessions(requested_day - timedelta(days=21), requested_day)]
    if not earlier:
        raise ValueError(f"no NSE session on or before {as_of}")
    requested = earlier[-1]
    if requested > completed:
        raise ValueError("as-of exceeds the latest completed NSE session")
    return requested


def _bar_age(day: date, expected: date) -> int | None:
    """Sessions between the bar and the expected session; None if not a session."""
    if day > expected:
        return 0
    sessions = _sessions(day, expected)
    if day not in sessions:
        return None
    return sum(1 for s in sessions if day < s <= expected)


# ---------------------------------------------------------------------------
# Triggers and scoring (India-tuned)
# ---------------------------------------------------------------------------


def detect_triggers(bars: list[Bar], args: argparse.Namespace) -> TriggerProfile:
    """Detect trigger tags and current-day metrics."""
    latest, prev, prior_2 = bars[0], bars[1], bars[2]
    day_gain_pct = pct_change(latest.close, prev.close)
    rupee_gain = latest.close - latest.open
    rupee_gain_pct = (rupee_gain / latest.open) * 100 if latest.open > 0 else 0.0
    current_range = latest.high - latest.low
    current_range_pct = (current_range / latest.close) * 100 if latest.close > 0 else 0.0
    prior_ranges = [b.high - b.low for b in bars[1:4]]
    prior_range_max = max(prior_ranges) if prior_ranges else 0.0
    prev_day_gain_pct = pct_change(prev.close, prior_2.close)
    volume_ratio_1d = latest.volume / prev.volume if prev.volume > 0 else 0.0
    avg_vol_20 = average([float(b.volume) for b in bars[1:21]])
    volume_ratio_20d = latest.volume / avg_vol_20 if avg_vol_20 > 0 else 0.0

    tags: list[str] = []
    volume_floor_ok = latest.volume >= args.min_volume
    volume_expanded = latest.volume > prev.volume
    if day_gain_pct >= args.four_pct_threshold and volume_expanded and volume_floor_ok:
        tags.append("4pct_breakout")
    if (
        rupee_gain >= args.rupee_threshold
        and rupee_gain_pct >= args.rupee_breakout_min_pct
        and volume_floor_ok
    ):
        tags.append("rupee_breakout")
    if (
        current_range > prior_range_max
        and prev_day_gain_pct <= args.max_prev_day_gain_for_range
        and volume_expanded
        and volume_floor_ok
    ):
        tags.append("range_expansion")
    if latest.volume >= args.high_volume_threshold:
        tags.append("high_volume")

    primary_order = ["4pct_breakout", "range_expansion", "rupee_breakout", "high_volume"]
    primary = next((tag for tag in primary_order if tag in tags), "none")
    return TriggerProfile(
        trigger_tags=tags,
        primary_trigger=primary,
        day_gain_pct=day_gain_pct,
        rupee_gain=rupee_gain,
        current_range_pct=current_range_pct,
        current_range_rupees=current_range,
        volume_ratio_1d=volume_ratio_1d,
        volume_ratio_20d=volume_ratio_20d,
        close_location_pct=close_location_pct(latest),
        prev_day_gain_pct=prev_day_gain_pct,
    )


def trigger_score(profile: TriggerProfile) -> int:
    score = 0
    if "4pct_breakout" in profile.trigger_tags:
        score += 14
        if profile.day_gain_pct >= 7:
            score += 3
    if "range_expansion" in profile.trigger_tags:
        score += 10
    if "rupee_breakout" in profile.trigger_tags:
        score += 8
    if "high_volume" in profile.trigger_tags:
        score += 2
    return min(20, score)


def possible_upper_circuit(bar: Bar, day_gain_pct: float, tolerance: float = 0.15) -> bool:
    """Close at the high on a move matching an NSE price band (5/10/20%)."""
    if bar.close < bar.high or day_gain_pct < CIRCUIT_BANDS[0] - tolerance:
        return False
    return any(abs(day_gain_pct - band) <= tolerance for band in CIRCUIT_BANDS)


def downstream_action(state: str, rating: str) -> str:
    if state == "ACTIONABLE_DAY1":
        return "Send to technical-analyst, then india-position-sizer if chart review passes."
    if state == "MANUAL_REVIEW":
        return "Manual chart review only; consider reduced risk if upgraded by chart quality."
    if state == "MANUAL_REVIEW_ONLY":
        return (
            "Market gate is restrictive; review for model book or very selective exceptions only."
        )
    if state == "WATCH_ONLY":
        return "Watchlist/model-book candidate; wait for cleaner risk or follow-through."
    return "Do not trade from this screen; retain only for review statistics."


def analyze_symbol(symbol: str, bars: list[Bar], args: argparse.Namespace) -> dict[str, Any]:
    """Analyze one symbol and return a structured result."""
    symbol = normalize_symbol(symbol)
    bars, freshness, reject_reasons = _eligible_bars(bars, args)
    if reject_reasons or len(bars) < max(25, args.min_base_days + 3):
        return {
            "symbol": symbol,
            "state": "REJECTED",
            "rating": "Reject",
            "setup_score": 0,
            "reject_reasons": reject_reasons or ["insufficient_history"],
            "date": freshness["latest_bar_date"],
            **freshness,
            "downstream_action": downstream_action("REJECTED", "Reject"),
        }

    latest, prev = bars[0], bars[1]
    turnover = latest.close * latest.volume
    if latest.close < args.min_price:
        reject_reasons.append("below_min_price")
    if latest.volume < args.min_volume:
        reject_reasons.append("below_min_volume")
    if turnover < args.min_turnover_cr * CRORE:
        reject_reasons.append("below_min_turnover")

    profile = detect_triggers(bars, args)
    if not [t for t in profile.trigger_tags if t != "high_volume"]:
        reject_reasons.append("no_momentum_burst_trigger")

    base = detect_base_profile(
        bars,
        min_base_days=args.min_base_days,
        max_base_days=args.max_base_days,
        max_base_width_pct=args.max_base_width_pct,
        max_prior_avg_range_pct=args.max_prior_avg_range_pct,
    )
    prior_up_streak = up_streak_before_trigger(bars)
    recent_breakdown = has_recent_breakdown(
        bars,
        lookback_days=args.recent_breakdown_lookback,
        threshold_pct=-abs(args.breakdown_threshold_pct),
    )
    entry_reference, stop_reference = latest.close, latest.low
    risk_pct_to_stop = ((entry_reference - stop_reference) / entry_reference) * 100
    if entry_reference <= stop_reference:
        reject_reasons.append("invalid_stop_reference")
    if risk_pct_to_stop > args.max_risk_pct_to_stop:
        reject_reasons.append("risk_too_wide")

    components = {
        "trigger": trigger_score(profile),
        "volume": volume_score(profile),
        "setup": setup_score(base, prev, args),
        "close_quality": close_quality_score(profile.close_location_pct),
        "risk_distance": risk_distance_score(risk_pct_to_stop),
        "failure_filters": 0,
        "market_gate": market_gate_score(args.market_gate),
    }
    components["failure_filters"], soft_tags = failure_filter_score(
        profile, base, prior_up_streak, recent_breakdown, args
    )
    composite_score = int(sum(components.values()))
    hard_rejected = bool(reject_reasons)
    rating = score_to_rating(composite_score if not hard_rejected else 0)
    state = score_to_state(composite_score, args.market_gate, hard_rejected)
    if freshness["bar_age_sessions"] and state == "ACTIONABLE_DAY1":
        state = "MANUAL_REVIEW"
        soft_tags = [*soft_tags, "stale_bar_tolerated"]
    upper_circuit = possible_upper_circuit(latest, profile.day_gain_pct)
    if upper_circuit:
        soft_tags = [*soft_tags, "possible_upper_circuit"]
        if state == "ACTIONABLE_DAY1":
            state = "MANUAL_REVIEW"  # a locked circuit may leave next-day entry unfillable

    return {
        "symbol": symbol,
        "date": latest.date,
        **freshness,
        "state": state,
        "rating": rating,
        "setup_score": composite_score if not hard_rejected else 0,
        "raw_setup_score": composite_score,
        "primary_trigger": profile.primary_trigger,
        "trigger_tags": profile.trigger_tags,
        "day_gain_pct": safe_round(profile.day_gain_pct),
        "rupee_gain": safe_round(profile.rupee_gain),
        "close": safe_round(latest.close),
        "prev_close": safe_round(prev.close),
        "open": safe_round(latest.open),
        "high": safe_round(latest.high),
        "low": safe_round(latest.low),
        "volume": latest.volume,
        "prev_volume": prev.volume,
        "turnover_cr": safe_round(turnover / CRORE),
        "volume_ratio_1d": safe_round(profile.volume_ratio_1d, 2),
        "volume_ratio_20d": safe_round(profile.volume_ratio_20d, 2),
        "current_range_pct": safe_round(profile.current_range_pct),
        "current_range_rupees": safe_round(profile.current_range_rupees),
        "close_location_pct": safe_round(profile.close_location_pct),
        "possible_upper_circuit": upper_circuit,
        "prior_base_days": base.prior_base_days,
        "base_width_pct": safe_round(base.base_width_pct),
        "avg_prior_range_pct": safe_round(base.avg_prior_range_pct),
        "volume_dry_up": base.volume_dry_up,
        "prior_up_streak": prior_up_streak,
        "recent_4pct_breakdown": recent_breakdown,
        "entry_reference": safe_round(entry_reference),
        "stop_reference": safe_round(stop_reference),
        "risk_pct_to_stop": safe_round(risk_pct_to_stop),
        "exit_template": "3_to_5_day_or_abnormal_profit_protection",
        "components": components,
        "soft_failure_tags": soft_tags,
        "reject_reasons": reject_reasons,
        "downstream_action": downstream_action(state, rating),
    }


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="India Stockbee Momentum Burst Screener (NSE)")
    parser.add_argument(
        "--universe", choices=sorted(india.UNIVERSE_FILES), help="Nifty index to scan"
    )
    parser.add_argument("--symbols", nargs="*", default=[], help="Explicit NSE symbols to scan")
    parser.add_argument("--universe-file", help="CSV, JSON, or TXT file containing NSE symbols")
    parser.add_argument("--prices-json", help="Offline OHLCV JSON keyed by symbol")
    parser.add_argument("--cache-dir", type=Path, default=None, help="India data cache directory")
    parser.add_argument(
        "--as-of",
        type=_date_arg,
        help="Completed daily cutoff YYYY-MM-DD; default: latest completed NSE session",
    )
    parser.add_argument("--max-bar-age-sessions", type=_age_arg, default=0)
    parser.add_argument("--max-symbols", type=int, default=750, help="Maximum symbols to process")
    parser.add_argument("--history-days", type=int, default=80, help="Daily bars per symbol")

    parser.add_argument(
        "--min-price", type=float, default=50.0, help="Minimum close in ₹ (default 50)"
    )
    parser.add_argument(
        "--min-volume", type=int, default=100_000, help="Minimum trigger-day shares"
    )
    parser.add_argument(
        "--min-turnover-cr", type=float, default=2.0, help="Minimum trigger-day turnover, ₹ crore"
    )
    parser.add_argument("--four-pct-threshold", type=float, default=4.0)
    parser.add_argument("--rupee-threshold", type=float, default=25.0, help="Rupee breakout, ₹")
    parser.add_argument(
        "--rupee-breakout-min-pct",
        type=float,
        default=1.5,
        help="Rupee breakout must also be at least this %% of the open",
    )
    parser.add_argument("--high-volume-threshold", type=int, default=5_000_000)
    parser.add_argument("--max-prev-day-gain-for-range", type=float, default=2.0)

    parser.add_argument("--min-base-days", type=int, default=3)
    parser.add_argument("--max-base-days", type=int, default=20)
    parser.add_argument("--max-base-width-pct", type=float, default=15.0)
    parser.add_argument("--max-prior-avg-range-pct", type=float, default=5.0)
    parser.add_argument("--narrow-prior-day-range-pct", type=float, default=3.0)
    parser.add_argument("--recent-breakdown-lookback", type=int, default=5)
    parser.add_argument("--breakdown-threshold-pct", type=float, default=4.0)
    parser.add_argument("--max-risk-pct-to-stop", type=float, default=10.0)
    parser.add_argument(
        "--market-gate",
        choices=["allowed", "neutral", "restrictive"],
        default="neutral",
        help="Latest market-regime/exposure decision",
    )
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--output-dir", default="reports/")
    return parser.parse_args(argv)


def build_symbol_list(args: argparse.Namespace, cache_root: Path) -> list[str]:
    symbols = {normalize_symbol(s) for s in args.symbols if normalize_symbol(s)}
    if args.universe_file:
        symbols.update(read_universe_file(args.universe_file))
    if args.universe:
        symbols.update(row["symbol"] for row in india.load_universe(args.universe, cache_root))
    return sorted(symbols)[: args.max_symbols]


def collect_price_data(args: argparse.Namespace, cache, cache_root: Path) -> tuple[dict, dict]:
    """Price data from offline JSON or the NSE cache, plus fetch statistics."""
    if args.prices_json:
        offline = read_prices_json(args.prices_json)
        explicit = set(build_symbol_list(args, cache_root))
        if explicit:
            offline = {s: bars for s, bars in offline.items() if s in explicit}
        return offline, {"source": "prices_json", "fetch_errors": {}}

    symbols = build_symbol_list(args, cache_root)
    if not symbols:
        raise ValueError(
            "No symbols to process. Use --universe, --symbols, --universe-file, or --prices-json."
        )
    end = args._expected_session
    start = end - timedelta(days=int(args.history_days * 1.6) + 30)
    price_data, errors = {}, {}
    for idx, symbol in enumerate(symbols, 1):
        if idx % 50 == 0 or idx == len(symbols):
            print(f"  Loading history: {idx}/{len(symbols)}", file=sys.stderr, flush=True)
        try:
            data = cache.stock_bars(symbol, start, end)
        except (india.IndiaDataError, ValueError) as exc:
            errors[symbol] = str(exc)
            continue
        bars = normalize_bars(data["bars"])
        if bars:
            price_data[symbol] = bars
    return price_data, {"source": "nse_bhavcopy", "fetch_errors": errors}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def report_warnings(metadata: dict[str, Any]) -> list[str]:
    processed = metadata.get("symbols_processed", 0)
    stale = metadata.get("stale_price_data_count", 0)
    warnings = []
    if processed > 0 and stale == processed:
        warnings.append(
            f"All {processed} processed symbols have stale price bars (expected NSE session "
            f"{metadata.get('expected_session', 'unknown')}). NSE may not have published that "
            "session's bhavcopy yet (it is treated as final after 19:00 IST), or the input does "
            "not cover it. Zero candidates here does not mean no setups."
        )
    failed = len(metadata.get("fetch_errors") or {})
    if failed:
        warnings.append(
            f"{failed} symbols could not be loaded from NSE; see metadata.fetch_errors."
        )
    return warnings


def generate_json_report(
    results: list[dict[str, Any]], metadata: dict[str, Any], output_path: str
) -> None:
    payload = {
        "schema_version": "1.0",
        "skill": "india-stockbee-momentum-burst-screener",
        "warnings": report_warnings(metadata),
        "metadata": metadata,
        "candidates": results,
    }
    Path(output_path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def format_candidate_md(row: dict[str, Any]) -> str:
    def fmt(value: Any) -> str:
        return "n/a" if value is None else str(value)

    tags = ", ".join(row.get("trigger_tags") or []) or "none"
    rejects = ", ".join(row.get("reject_reasons") or []) or "none"
    soft = ", ".join(row.get("soft_failure_tags") or []) or "none"
    volume = row.get("volume")
    volume_str = f"{volume:,}" if isinstance(volume, (int, float)) else "n/a"
    return "\n".join(
        [
            f"### {row['symbol']} — {fmt(row.get('rating'))} / {fmt(row.get('setup_score'))}",
            "",
            f"- State: `{fmt(row.get('state'))}`",
            f"- Trigger: `{fmt(row.get('primary_trigger'))}` ({tags})",
            f"- Price: close ₹{fmt(row.get('close'))}, day gain {fmt(row.get('day_gain_pct'))}%, "
            f"open-to-close ₹{fmt(row.get('rupee_gain'))}",
            f"- Volume: {volume_str} shares, turnover ₹{fmt(row.get('turnover_cr'))} cr "
            f"({fmt(row.get('volume_ratio_1d'))}x previous day, {fmt(row.get('volume_ratio_20d'))}x 20d avg)",
            f"- Close location: {fmt(row.get('close_location_pct'))}%",
            f"- Base: {fmt(row.get('prior_base_days'))} days, width {fmt(row.get('base_width_pct'))}%",
            f"- Entry / stop reference: ₹{fmt(row.get('entry_reference'))} / ₹{fmt(row.get('stop_reference'))} "
            f"({fmt(row.get('risk_pct_to_stop'))}% risk to stop)",
            f"- Soft failure tags: {soft}",
            f"- Reject reasons: {rejects}",
            f"- Downstream action: {fmt(row.get('downstream_action'))}",
            "",
        ]
    )


def generate_markdown_report(
    results: list[dict[str, Any]],
    metadata: dict[str, Any],
    output_path: str,
    top: int,
    include_rejected: bool,
) -> None:
    counts: dict[str, int] = {}
    for row in results:
        counts[row.get("state", "UNKNOWN")] = counts.get(row.get("state", "UNKNOWN"), 0) + 1
    actionable = [r for r in results if r.get("state") != "REJECTED"][:top]
    rejected = [r for r in results if r.get("state") == "REJECTED"]
    lines = [
        "# India Stockbee Momentum Burst Report (NSE)",
        "",
        f"Generated at: {metadata['generated_at']}",
        f"Market gate: `{metadata['market_gate']}`",
        f"Input mode: `{metadata['input_mode']}`",
        "",
        "## Summary",
        "",
        f"- Symbols processed: {metadata['symbols_processed']}",
        f"- Non-rejected candidates: {len(actionable)}",
        f"- Rejected candidates: {len(rejected)}",
        f"- Expected completed NSE session: {metadata.get('expected_session', 'unavailable')}",
        f"- Maximum bar age (sessions): {metadata.get('max_bar_age_sessions', 0)}",
        f"- Stale price data rejected: {metadata.get('stale_price_data_count', 0)}",
        f"- Symbols that failed to load: {len(metadata.get('fetch_errors') or {})}",
        "",
        "| State | Count |",
        "|---|---:|",
    ]
    for state in [
        "ACTIONABLE_DAY1",
        "MANUAL_REVIEW",
        "MANUAL_REVIEW_ONLY",
        "WATCH_ONLY",
        "REJECTED",
    ]:
        lines.append(f"| {state} | {counts.get(state, 0)} |")
    warnings = report_warnings(metadata)
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- WARNING: {w}" for w in warnings)
    lines.extend(["", "## Candidates", ""])
    if actionable:
        lines.extend(format_candidate_md(row) for row in actionable)
    else:
        lines.extend(["No non-rejected candidates found.", ""])
    if include_rejected and rejected:
        lines.extend(["", "## Rejected", ""])
        lines.extend(format_candidate_md(row) for row in rejected[:50])
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list | None = None, *, cache=None, now_fn=None) -> int:
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    if cache is None:
        cache = india.BarCache(cache_root, sessions_fn=SESSIONS_FN, now_fn=lambda: now)
    try:
        args._expected_session = resolve_expected_session(args.as_of, now=now)
        price_data, fetch_stats = collect_price_data(args, cache, cache_root)
        results = [analyze_symbol(symbol, bars, args) for symbol, bars in price_data.items()]
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    results = sort_results(results)
    if args.prices_json:
        input_mode = "prices_json"
    elif args.universe:
        input_mode = f"universe:{args.universe}"
    else:
        input_mode = "symbols"
    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "input_mode": input_mode,
        "symbols_processed": len(price_data),
        "market_gate": args.market_gate,
        "as_of": args.as_of,
        "expected_session": args._expected_session.isoformat(),
        "max_bar_age_sessions": args.max_bar_age_sessions,
        "latest_bar_dates": {r["symbol"]: r["latest_bar_date"] for r in results},
        "stale_price_data_count": sum("stale_price_data" in r["reject_reasons"] for r in results),
        "fetch_errors": fetch_stats["fetch_errors"],
        "data_source": fetch_stats["source"],
        "thresholds": {
            "four_pct_threshold": args.four_pct_threshold,
            "rupee_threshold": args.rupee_threshold,
            "rupee_breakout_min_pct": args.rupee_breakout_min_pct,
            "min_price": args.min_price,
            "min_volume": args.min_volume,
            "min_turnover_cr": args.min_turnover_cr,
            "max_risk_pct_to_stop": args.max_risk_pct_to_stop,
            "max_base_width_pct": args.max_base_width_pct,
        },
    }
    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = os.path.join(args.output_dir, f"india_stockbee_momentum_burst_{stamp}.json")
    md_path = os.path.join(args.output_dir, f"india_stockbee_momentum_burst_{stamp}.md")
    generate_json_report(results, metadata, json_path)
    generate_markdown_report(
        results, metadata, md_path, top=args.top, include_rejected=args.include_rejected
    )

    print(f"India momentum burst screening complete (NSE session {metadata['expected_session']})")
    print(
        f"  Symbols processed: {len(price_data)}; failed to load: {len(fetch_stats['fetch_errors'])}"
    )
    for idx, row in enumerate([r for r in results if r.get("state") != "REJECTED"][:5], 1):
        print(
            f"  {idx}. {row['symbol']:12} {row['state']:18} Score: {row['setup_score']:3} "
            f"({row['rating']}) Trigger: {row['primary_trigger']}"
        )
    print(f"  JSON Report:     {json_path}")
    print(f"  Markdown Report: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
