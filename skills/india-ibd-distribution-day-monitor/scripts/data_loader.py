"""NSE index history loader + OHLCV quality validation.

Returns most-recent-first list[dict] (no pandas), with Diwali Muhurat sessions
removed: a one-hour, very-low-volume session would make the next ordinary
session look like "higher volume" and manufacture distribution days.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import _india_data as india


def validate_history_quality(history: list[dict]) -> tuple[list[str], list[dict]]:
    """Return (audit_flags, skipped_sessions) for missing/invalid close or volume."""
    flags: list[str] = []
    skipped: list[dict] = []
    for row in history:
        day = row.get("date")
        close = row.get("close")
        volume = row.get("volume")
        if close is None:
            skipped.append({"date": day, "reason": "missing_close"})
        elif close <= 0:
            skipped.append({"date": day, "reason": "invalid_close"})
        if volume is None:
            skipped.append({"date": day, "reason": "missing_volume"})
        elif volume <= 0:
            skipped.append({"date": day, "reason": "invalid_volume"})
    if skipped:
        flags.append("data_quality_warnings")
    return flags, skipped


def fetch_index_ohlcv(
    cache: Any, index_name: str, sessions: int, end: date
) -> tuple[list[dict], dict]:
    """Most-recent-first OHLCV for an NSE index plus an audit dict."""
    audit: dict = {
        "data_source": "nse_bhavcopy",
        "symbol": index_name,
        "sessions_requested": sessions,
        "sessions_loaded": 0,
        "muhurat_sessions_removed": [],
        "audit_flags": [],
        "skipped_sessions": [],
    }
    start = end - timedelta(days=int(sessions * 1.6) + 30)
    try:
        data = cache.index_bars(index_name, start, end)
    except (india.IndiaDataError, ValueError) as exc:
        audit["audit_flags"].append("no_data_returned")
        audit["error"] = str(exc)
        return [], audit
    bars, removed = india.drop_muhurat_sessions(data.get("bars") or [])
    audit["muhurat_sessions_removed"] = removed
    history = [
        {k: b.get(k) for k in ("date", "open", "high", "low", "close", "volume")}
        for b in reversed(bars)
    ]
    if not history:
        audit["audit_flags"].append("no_data_returned")
        return [], audit
    flags, skipped = validate_history_quality(history)
    audit["sessions_loaded"] = len(history)
    audit["audit_flags"].extend(flags)
    audit["skipped_sessions"] = skipped
    return history, audit
