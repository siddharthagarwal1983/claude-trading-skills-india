#!/usr/bin/env python3
"""
India Exposure Coach - one-page market posture for NSE swing trading.

Combines the India regime skills with free NSE market context into an equity
exposure ceiling and one action: NEW_ENTRY_ALLOWED, REDUCE_ONLY or
CASH_PRIORITY.

Inputs (all optional; missing critical inputs cut the score and confidence):
  --breadth        india-market-breadth-analyzer JSON   (critical)
  --distribution   india-ibd-distribution-day-monitor JSON (critical)
  --ftd            india-ftd-detector JSON (counts only during a correction)
  --flows          FII/DII daily net flows JSON (e.g. saved from DalalOS)
Fetched directly from NSE unless --no-fetch:
  NIFTY 50 trend vs its 50/200-day averages (critical), INDIA VIX level and
  5-session change, NIFTY 50 P/E percentile.

Usage:
    python3 calculate_india_exposure.py \
      --breadth reports/india_market_breadth_*.json \
      --distribution reports/india_ibd_distribution_day_monitor_*.json \
      --ftd reports/india_ftd_detector_*.json --output-dir reports/
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _india_data as india  # noqa: E402

WEIGHTS = {
    "breadth": 0.25,
    "distribution": 0.20,
    "trend": 0.20,
    "volatility": 0.15,
    "ftd": 0.10,
    "flows": 0.05,
    "valuation": 0.05,
}
CRITICAL_INPUTS = {"breadth", "distribution", "trend"}
DISTRIBUTION_SCORES = {"NORMAL": 85, "CAUTION": 60, "HIGH": 35, "SEVERE": 15}
FTD_STATE_SCORES = {
    "FTD_WINDOW": 45,
    "RALLY_ATTEMPT": 40,
    "CORRECTION": 20,
    "RALLY_FAILED": 15,
    "FTD_INVALIDATED": 15,
}
CONTEXT_CALENDAR_DAYS = 420


# --------------------------------------------------------------------------- inputs


def resolve_input(pattern: str | None) -> Path | None:
    """A path or glob; globs resolve to the newest matching file."""
    if not pattern:
        return None
    matches = sorted(glob.glob(pattern)) if any(ch in pattern for ch in "*?[") else [pattern]
    return Path(matches[-1]) if matches and Path(matches[-1]).exists() else None


def load_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def report_as_of(kind: str, data: dict) -> str | None:
    if kind == "breadth":
        return data.get("as_of")
    if kind == "distribution":
        return (data.get("market_distribution_state") or {}).get("as_of")
    if kind == "ftd":
        return (data.get("metadata") or {}).get("expected_session")
    if kind == "flows":
        rows = flow_rows(data)
        return rows[-1]["date"] if rows else None
    return None


def extract_breadth(data: dict | None) -> tuple:
    if not data:
        return None, {}
    comp = data.get("composite") or {}
    score = comp.get("composite_score")
    latest = data.get("latest") or {}
    detail = {
        "zone": comp.get("zone"),
        "pct_above_50": latest.get("pct_above_50"),
        "pct_above_200": latest.get("pct_above_200"),
        "coverage": (data.get("coverage") or {}).get("verdict"),
    }
    if score is None or detail["coverage"] == "not_evaluable":
        return None, detail
    return max(0, min(100, int(round(score)))), detail


def extract_distribution(data: dict | None) -> tuple:
    if not data:
        return None, {}
    level = (data.get("market_distribution_state") or {}).get("overall_risk_level")
    return DISTRIBUTION_SCORES.get(level), {"overall_risk_level": level}


def extract_ftd(data: dict | None) -> tuple:
    """FTD evidence matters only during a correction; NO_SIGNAL means not applicable."""
    if not data:
        return None, {}
    state = (data.get("market_state") or {}).get("combined_state")
    detail = {"combined_state": state, "data_gap": bool(data.get("data_gap"))}
    if detail["data_gap"]:
        return None, detail
    if state == "FTD_CONFIRMED":
        total = (data.get("quality_score") or {}).get("total_score")
        return (max(0, min(100, int(total))) if total is not None else None), detail
    return FTD_STATE_SCORES.get(state), detail


def flow_rows(data: dict | list | None) -> list:
    """Normalise FII/DII rows: [{date, fii_net_cr, dii_net_cr}] oldest first.

    Accepts a list, or a dict with "rows" / "data" / "flows", using keys such as
    fii_net / fii_net_cr / fii_net_inr_cr (and the dii equivalents).
    """
    rows = data
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("flows") or data.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("rows") or rows.get("flows") or []
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        fii = next(
            (row[k] for k in row if k.lower().startswith("fii") and "net" in k.lower()), None
        )
        dii = next(
            (row[k] for k in row if k.lower().startswith("dii") and "net" in k.lower()), None
        )
        if isinstance(fii, (int, float)) and isinstance(dii, (int, float)):
            out.append(
                {"date": str(row["date"])[:10], "fii_net_cr": float(fii), "dii_net_cr": float(dii)}
            )
    return sorted(out, key=lambda r: r["date"])


def extract_flows(data: dict | None, sessions: int = 20) -> tuple:
    rows = flow_rows(data) if data else []
    if len(rows) < 5:
        return None, {}
    recent = rows[-sessions:]
    fii = sum(r["fii_net_cr"] for r in recent)
    dii = sum(r["dii_net_cr"] for r in recent)
    combined = fii + dii
    if fii > 0 and dii > 0:
        score = 80
    elif combined > 0:
        score = 60
    elif fii < 0 and dii < 0:
        score = 20
    else:
        score = 40
    detail = {
        "sessions": len(recent),
        "fii_net_cr": round(fii, 1),
        "dii_net_cr": round(dii, 1),
        "combined_net_cr": round(combined, 1),
    }
    return score, detail


# --------------------------------------------------------------------------- NSE context


def _sma(closes: list, window: int):
    return sum(closes[-window:]) / window if len(closes) >= window else None


def trend_score(closes: list) -> tuple:
    if len(closes) < 200:
        return None, {"reason": "fewer than 200 sessions"}
    price, s50, s200 = closes[-1], _sma(closes, 50), _sma(closes, 200)
    above50, above200 = price > s50, price > s200
    if above50 and above200 and s50 > s200:
        score = 85
    elif above200:
        score = 55
    elif above50:
        score = 40
    else:
        score = 15
    return score, {
        "close": price,
        "sma50": round(s50, 2),
        "sma200": round(s200, 2),
        "above_sma50": above50,
        "above_sma200": above200,
    }


def volatility_score(vix_closes: list) -> tuple:
    if len(vix_closes) < 6:
        return None, {}
    level = vix_closes[-1]
    change_5d = (level / vix_closes[-6] - 1) * 100
    if level < 12:
        score = 85
    elif level < 16:
        score = 75
    elif level < 20:
        score = 55
    elif level < 25:
        score = 35
    else:
        score = 15
    if change_5d > 20:
        score = max(0, score - 15)  # a fresh volatility spike
    return score, {"india_vix": level, "change_5d_pct": round(change_5d, 1)}


def valuation_score(pe_values: list) -> tuple:
    clean = [v for v in pe_values if v]
    if len(clean) < 120:
        return None, {}
    current = clean[-1]
    pct = 100.0 * sum(1 for v in clean if v <= current) / len(clean)
    if pct >= 95:
        score = 30
    elif pct >= 80:
        score = 40
    elif pct <= 20:
        score = 80
    else:
        score = 60
    return score, {"nifty50_pe": current, "pe_percentile": round(pct, 1), "sessions": len(clean)}


def fetch_context(cache, end: date) -> dict:
    start = end - timedelta(days=CONTEXT_CALENDAR_DAYS)
    out: dict = {}
    try:
        nifty = cache.index_bars("NIFTY 50", start, end)["bars"]
        out["nifty"] = nifty
    except (india.IndiaDataError, ValueError) as exc:
        out["nifty_error"] = str(exc)
    try:
        out["vix"] = cache.index_bars("INDIA VIX", start, end)["bars"]
    except (india.IndiaDataError, ValueError) as exc:
        out["vix_error"] = str(exc)
    return out


# --------------------------------------------------------------------------- decision


def calculate_composite(scores: dict) -> tuple:
    provided = [k for k in WEIGHTS if scores.get(k) is not None]
    missing = [k for k in WEIGHTS if scores.get(k) is None]
    weight = sum(WEIGHTS[k] for k in provided)
    if not weight:
        return 50.0, provided, missing
    composite = sum(scores[k] * WEIGHTS[k] for k in provided) / weight
    composite -= 10 * len(set(missing) & CRITICAL_INPUTS)
    return max(0.0, composite), provided, missing


def determine_exposure_ceiling(composite: float) -> int:
    if composite >= 80:
        return min(100, int(90 + (composite - 80)))
    if composite >= 65:
        return int(70 + (composite - 65) * 1.3)
    if composite >= 50:
        return int(50 + (composite - 50) * 1.3)
    if composite >= 35:
        return int(30 + (composite - 35) * 1.3)
    if composite >= 20:
        return int(10 + (composite - 20) * 1.3)
    return max(0, int(composite / 2))


def determine_recommendation(
    composite: float, distribution: int | None, missing_critical: int
) -> str:
    if composite < 30 or (distribution is not None and distribution < 25):
        return "CASH_PRIORITY"
    if composite < 50 or (distribution is not None and distribution < 40) or missing_critical >= 2:
        return "REDUCE_ONLY"
    return "NEW_ENTRY_ALLOWED"


def determine_participation(breadth_detail: dict) -> str:
    p50, p200 = breadth_detail.get("pct_above_50"), breadth_detail.get("pct_above_200")
    if p50 is None or p200 is None:
        return "UNKNOWN"
    if p200 >= 60 and p50 >= 55:
        return "BROAD"
    if p200 >= 40:
        return "MODERATE"
    return "NARROW"


def determine_confidence(provided: list, missing: list) -> str:
    missing_critical = len(set(missing) & CRITICAL_INPUTS)
    if len(provided) >= 6 and missing_critical == 0:
        return "HIGH"
    if len(provided) >= 4 and missing_critical <= 1:
        return "MEDIUM"
    return "LOW"


def rationale(result: dict) -> list:
    lines = []
    comps = result["components"]
    for key in sorted(comps, key=lambda k: -WEIGHTS[k]):
        c = comps[key]
        if c["score"] is not None:
            lines.append(f"{key}: {c['score']}/100 (weight {WEIGHTS[key]:.0%})")
    for key in result["inputs_missing"]:
        tag = " [critical]" if key in CRITICAL_INPUTS else ""
        lines.append(f"{key}: not available{tag}")
    return lines


def render_markdown(result: dict) -> str:
    ctx = result["market_context"]
    lines = [
        "# India Market Posture (NSE)",
        "",
        f"**Evaluation session:** {result['as_of']}",
        "",
        f"## Exposure Ceiling: {result['exposure_ceiling_pct']}%",
        f"## Recommendation: {result['recommendation']}",
        "",
        f"- Composite score: {result['composite_score']} / 100",
        f"- Participation: {result['participation']}",
        f"- Confidence: {result['confidence']}",
        "",
        "## Components",
        "",
        "| Input | Score | Weight | Detail |",
        "|---|---:|---:|---|",
    ]
    for key, comp in result["components"].items():
        detail = ", ".join(f"{k}={v}" for k, v in (comp["detail"] or {}).items())
        lines.append(
            f"| {key} | {comp['score'] if comp['score'] is not None else '—'} | {WEIGHTS[key]:.0%} | {detail} |"
        )
    if result["stale_inputs"]:
        lines += ["", "**Stale inputs ignored:** " + ", ".join(result["stale_inputs"])]
    lines += ["", "## Market Context", ""]
    for key, value in ctx.items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        "## Rationale",
        "",
        *[f"- {line}" for line in result["rationale"]],
        "",
        "*Exposure guidance for position sizing, not a buy or sell signal.*",
        "",
    ]
    return "\n".join(lines)


def parse_arguments(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="India Exposure Coach - NSE market posture")
    parser.add_argument("--breadth", help="india-market-breadth-analyzer JSON (path or glob)")
    parser.add_argument(
        "--distribution", help="india-ibd-distribution-day-monitor JSON (path or glob)"
    )
    parser.add_argument("--ftd", help="india-ftd-detector JSON (path or glob)")
    parser.add_argument("--flows", help="FII/DII daily flows JSON (path or glob)")
    parser.add_argument(
        "--no-fetch", action="store_true", help="Do not fetch NIFTY / INDIA VIX context"
    )
    parser.add_argument(
        "--max-age-sessions", type=int, default=2, help="Drop inputs older than this"
    )
    parser.add_argument("--as-of", help="YYYY-MM-DD; default: latest completed NSE session")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--output-dir", default="reports/")
    return parser.parse_args(argv)


def main(argv: list | None = None, *, cache=None, now_fn=None) -> int:  # noqa: C901
    args = parse_arguments(argv)
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    try:
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        end = latest
        if args.as_of:
            requested = date.fromisoformat(args.as_of)
            if requested > latest:
                raise ValueError(
                    f"--as-of {args.as_of} is after the latest completed NSE session {latest}"
                )
            end = cache._sessions_fn(requested - timedelta(days=21), requested)[-1]
        recent_sessions = cache._sessions_fn(end - timedelta(days=30), end)
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, IndexError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    oldest_ok = (
        recent_sessions[-(args.max_age_sessions + 1)].isoformat()
        if len(recent_sessions) > args.max_age_sessions
        else None
    )

    raw = {}
    stale = []
    for kind in ("breadth", "distribution", "ftd", "flows"):
        path = resolve_input(getattr(args, kind))
        data = load_json(path)
        if data is None:
            if getattr(args, kind):
                print(
                    f"Warning: no readable {kind} input for {getattr(args, kind)}", file=sys.stderr
                )
            continue
        stamp = report_as_of(kind, data)
        if stamp is None or (oldest_ok and stamp < oldest_ok) or stamp > end.isoformat():
            stale.append(f"{kind} ({stamp})")
            continue
        raw[kind] = data

    components: dict = {}
    for kind, extractor in (
        ("breadth", extract_breadth),
        ("distribution", extract_distribution),
        ("ftd", extract_ftd),
        ("flows", extract_flows),
    ):
        score, detail = extractor(raw.get(kind))
        components[kind] = {"score": score, "detail": detail}

    context: dict = {}
    trend = vol = val = (None, {})
    if not args.no_fetch:
        fetched = fetch_context(cache, end)
        nifty = [b for b in fetched.get("nifty", []) if b["date"] <= end.isoformat()]
        vix = [b for b in fetched.get("vix", []) if b["date"] <= end.isoformat()]
        trend = trend_score([b["close"] for b in nifty])
        vol = volatility_score([b["close"] for b in vix])
        val = valuation_score([b.get("pe") for b in nifty])
        for key in ("nifty_error", "vix_error"):
            if key in fetched:
                context[key] = fetched[key]
    components["trend"] = {"score": trend[0], "detail": trend[1]}
    components["volatility"] = {"score": vol[0], "detail": vol[1]}
    components["valuation"] = {"score": val[0], "detail": val[1]}
    components = {k: components[k] for k in WEIGHTS}

    scores = {k: v["score"] for k, v in components.items()}
    composite, provided, missing = calculate_composite(scores)
    missing_critical = len(set(missing) & CRITICAL_INPUTS)
    context.update({k: v for k, v in trend[1].items() if k != "reason"})
    context.update(vol[1])
    context.update(val[1])
    result = {
        "schema_version": "1.0",
        "skill": "india-exposure-coach",
        "exchange": india.EXCHANGE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": end.isoformat(),
        "composite_score": round(composite, 1),
        "exposure_ceiling_pct": determine_exposure_ceiling(composite),
        "recommendation": determine_recommendation(
            composite, scores.get("distribution"), missing_critical
        ),
        "participation": determine_participation(components["breadth"]["detail"]),
        "confidence": determine_confidence(provided, missing),
        "components": components,
        "inputs_provided": provided,
        "inputs_missing": missing,
        "stale_inputs": stale,
        "market_context": context,
    }
    result["rationale"] = rationale(result)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = out / f"india_exposure_posture_{stamp}.json"
    md_path = out / f"india_exposure_posture_{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(
        f"{result['recommendation']}: exposure ceiling {result['exposure_ceiling_pct']}% "
        f"(composite {result['composite_score']}, confidence {result['confidence']})"
    )
    if stale:
        print(f"  Stale inputs ignored: {', '.join(stale)}")
    print(f"  JSON report: {json_path}")
    print(f"  Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
