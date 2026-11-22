"""Pure breadth calculations for an NSE stock universe (no I/O).

Per stock and session the calculator derives: close above its 20/50/200-day
simple moving average, advance/decline versus the prior close, and a new
52-week high or low (high/low versus the prior 251 sessions). Aggregating
across the universe gives daily breadth; smoothing and a McClellan
oscillator (19/39 EMA of ratio-adjusted net advances) follow.

A stock contributes to a measure only once it has enough history for it, so
counts grow as newly listed or recently added stocks mature. Every
percentage is "of the stocks that can be measured that day".
"""

from __future__ import annotations

from collections import deque

SMA_WINDOWS = (20, 50, 200)
YEAR_WINDOW = 252  # session itself + 251 prior sessions


def _rolling_extreme_flags(values: list[float], window: int, highest: bool) -> list:
    """For each i: True if values[i] beats every value in the prior ``window - 1`` sessions."""
    flags: list = [None] * len(values)
    dq: deque = deque()  # indexes, values monotonic
    prior = window - 1
    for i, value in enumerate(values):
        while dq and dq[0] < i - prior:
            dq.popleft()
        if i >= prior:
            best = values[dq[0]] if dq else None
            flags[i] = best is None or (value > best if highest else value < best)
        while dq and (values[dq[-1]] <= value if highest else values[dq[-1]] >= value):
            dq.pop()
        dq.append(i)
    return flags


def stock_flags(bars: list[dict]) -> dict:
    """{date: {"above_20": bool|None, ..., "change": +1/0/-1|None, "new_high": .., "new_low": ..}}."""
    closes = [b["close"] for b in bars]
    out: dict = {}
    sums = {w: 0.0 for w in SMA_WINDOWS}
    new_high = _rolling_extreme_flags([b["high"] for b in bars], YEAR_WINDOW, True)
    new_low = _rolling_extreme_flags([b["low"] for b in bars], YEAR_WINDOW, False)
    for i, bar in enumerate(bars):
        row: dict = {}
        for w in SMA_WINDOWS:
            sums[w] += closes[i]
            if i >= w:
                sums[w] -= closes[i - w]
            row[f"above_{w}"] = closes[i] > sums[w] / w if i >= w - 1 else None
        if i == 0:
            row["change"] = None
        else:
            diff = closes[i] - closes[i - 1]
            row["change"] = 1 if diff > 0 else (-1 if diff < 0 else 0)
        row["new_high"] = new_high[i]
        row["new_low"] = new_low[i]
        out[bar["date"]] = row
    return out


def _pct(count: int, total: int):
    return round(100.0 * count / total, 2) if total else None


def daily_breadth(flags_by_symbol: dict, calendar: list) -> list:
    """Aggregate per-stock flags into one breadth row per calendar session."""
    rows = []
    for day in calendar:
        counts = {f"above_{w}": [0, 0] for w in SMA_WINDOWS}
        adv = dec = unch = 0
        highs = lows = measured_year = 0
        for flags in flags_by_symbol.values():
            row = flags.get(day)
            if row is None:
                continue
            for w in SMA_WINDOWS:
                value = row[f"above_{w}"]
                if value is not None:
                    counts[f"above_{w}"][1] += 1
                    counts[f"above_{w}"][0] += int(value)
            change = row["change"]
            if change == 1:
                adv += 1
            elif change == -1:
                dec += 1
            elif change == 0:
                unch += 1
            if row["new_high"] is not None:
                measured_year += 1
                highs += int(row["new_high"])
                lows += int(bool(row["new_low"]))
        rows.append(
            {
                "date": day,
                **{f"pct_above_{w}": _pct(*counts[f"above_{w}"]) for w in SMA_WINDOWS},
                "measured_200": counts["above_200"][1],
                "advances": adv,
                "declines": dec,
                "unchanged": unch,
                "new_highs": highs,
                "new_lows": lows,
                "net_new_highs_pct": _pct(highs - lows, measured_year),
                "measured_52w": measured_year,
            }
        )
    return rows


def sma(values: list, window: int) -> list:
    out: list = []
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - window + 1) : i + 1] if v is not None]
        out.append(round(sum(chunk) / window, 2) if len(chunk) == window else None)
    return out


def ema(values: list, period: int) -> list:
    alpha = 2.0 / (period + 1)
    out: list = []
    current = None
    for value in values:
        if value is None:
            out.append(current)
            continue
        current = value if current is None else current + alpha * (value - current)
        out.append(current)
    return out


def add_derived_series(rows: list) -> list:
    """Add 8/50-session MAs of %>200DMA, the McClellan oscillator and summation index."""
    above_200 = [r["pct_above_200"] for r in rows]
    ma8, ma50 = sma(above_200, 8), sma(above_200, 50)
    ratio_adjusted = []
    for r in rows:
        total = r["advances"] + r["declines"]
        ratio_adjusted.append((r["advances"] - r["declines"]) / total * 1000 if total else None)
    fast, slow = ema(ratio_adjusted, 19), ema(ratio_adjusted, 39)
    summation = 0.0
    for i, r in enumerate(rows):
        r["breadth_8ma"] = ma8[i]
        r["breadth_50ma"] = ma50[i]
        osc = None if fast[i] is None or slow[i] is None else round(fast[i] - slow[i], 2)
        r["mcclellan_oscillator"] = osc
        if osc is not None:
            summation += osc
        r["mcclellan_summation"] = round(summation, 2) if osc is not None else None
    return rows


# --------------------------------------------------------------------------- scoring


def _interp(value, points: list) -> int | None:
    """Piecewise-linear map through (x, score) points sorted by x; clipped at the ends."""
    if value is None:
        return None
    if value <= points[0][0]:
        return int(points[0][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            return int(round(y0 + (y1 - y0) * (value - x0) / (x1 - x0)))
    return int(points[-1][1])


COMPONENTS = {
    # name: (weight, description)
    "participation": (0.30, "% of stocks above their 200-day average"),
    "momentum": (0.20, "% of stocks above their 50-day average"),
    "trend": (0.20, "8-session MA of %>200DMA versus its 50-session MA (percentage points)"),
    "new_highs_lows": (0.15, "10-session average of net new 52-week highs, % of universe"),
    "mcclellan": (0.15, "McClellan oscillator (19/39 EMA of ratio-adjusted net advances)"),
}
ZONES = (
    (80, "Strong", "Broad participation; new swing entries are supported by breadth."),
    (60, "Healthy", "Most stocks are trending up; normal position sizing."),
    (40, "Neutral", "Mixed participation; be selective and keep risk modest."),
    (20, "Weak", "Narrow market; reduce new risk and favour leaders only."),
    (0, "Critical", "Broad breakdown; protect capital and wait for breadth to recover."),
)


def score_latest(rows: list) -> dict:
    """Score the most recent row; components without data are excluded and reported."""
    latest = rows[-1]
    recent = [r["net_new_highs_pct"] for r in rows[-10:] if r["net_new_highs_pct"] is not None]
    nnh = round(sum(recent) / len(recent), 2) if recent else None
    trend_gap = (
        round(latest["breadth_8ma"] - latest["breadth_50ma"], 2)
        if latest["breadth_8ma"] is not None and latest["breadth_50ma"] is not None
        else None
    )
    inputs = {
        "participation": latest["pct_above_200"],
        "momentum": latest["pct_above_50"],
        "trend": trend_gap,
        "new_highs_lows": nnh,
        "mcclellan": latest["mcclellan_oscillator"],
    }
    scores = {
        "participation": _interp(inputs["participation"], [(15, 0), (30, 25), (50, 60), (70, 100)]),
        "momentum": _interp(inputs["momentum"], [(15, 0), (30, 25), (50, 60), (70, 100)]),
        "trend": _interp(inputs["trend"], [(-10, 0), (-5, 30), (0, 60), (5, 100)]),
        "new_highs_lows": _interp(inputs["new_highs_lows"], [(-5, 0), (0, 50), (5, 100)]),
        "mcclellan": _interp(inputs["mcclellan"], [(-50, 0), (0, 50), (50, 100)]),
    }
    weight = sum(COMPONENTS[k][0] for k, v in scores.items() if v is not None)
    composite = (
        round(sum(COMPONENTS[k][0] * v for k, v in scores.items() if v is not None) / weight, 1)
        if weight
        else None
    )
    zone, guidance = "Unavailable", "Not enough history to score breadth."
    if composite is not None:
        for floor, name, text in ZONES:
            if composite >= floor:
                zone, guidance = name, text
                break
    prior_50ma = rows[-6]["breadth_50ma"] if len(rows) >= 6 else None
    deteriorating = bool(
        trend_gap is not None
        and trend_gap < 0
        and prior_50ma is not None
        and latest["breadth_50ma"] is not None
        and latest["breadth_50ma"] < prior_50ma
    )
    washout = latest["pct_above_50"] is not None and latest["pct_above_50"] < 15
    return {
        "composite_score": composite,
        "zone": zone,
        "guidance": guidance,
        "components": {
            k: {
                "input": inputs[k],
                "score": scores[k],
                "weight": COMPONENTS[k][0],
                "description": COMPONENTS[k][1],
            }
            for k in COMPONENTS
        },
        "missing_components": sorted(k for k, v in scores.items() if v is None),
        "flags": {
            "deteriorating": deteriorating,
            "washout": bool(washout),
        },
    }


def percentile_rank(values: list, current) -> float | None:
    clean = [v for v in values if v is not None]
    if current is None or not clean:
        return None
    return round(100.0 * sum(1 for v in clean if v <= current) / len(clean), 1)
