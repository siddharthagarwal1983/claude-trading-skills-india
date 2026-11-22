#!/usr/bin/env python3
"""India IBD Distribution Day Monitor (NSE) — CLI entrypoint.

Workflow:
  1. Resolve config (config/default.yaml, optional --config override).
  2. Load NIFTY 50 and NIFTY 500 daily history from free official NSE data
     (real index volume), with Diwali Muhurat sessions removed.
  3. Rebase via prepare_effective_history (as_of normalization).
  4. Detect, enrich and count active distribution days (d5/d15/d25).
  5. Compute MA filters (21EMA / 50SMA) -> market_below_ma flag.
  6. Classify per-index risk and combine to an overall risk level.
  7. Generate a portfolio action for SWING_BOOK or NIFTY_ETF exposure.
  8. Write JSON + Markdown reports to --output-dir.

No API key is needed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - environment guard
    print(
        "Error: pyyaml is required. Install this skill's dependencies: "
        f"pip install -r {Path(__file__).resolve().parents[1] / 'requirements.txt'}",
        file=sys.stderr,
    )
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _india_data as india  # noqa: E402
from data_loader import fetch_index_ohlcv  # noqa: E402
from distribution_day_tracker import (  # noqa: E402
    count_active_in_window,
    detect_distribution_days,
    enrich_records,
)
from exposure_policy import generate_portfolio_action, supported_instruments  # noqa: E402
from history_utils import prepare_effective_history  # noqa: E402
from math_utils import calc_ema, calc_sma  # noqa: E402
from models import DDRecord, DistributionDayRule, IndexResult, RiskThresholds  # noqa: E402
from report_generator import write_json, write_markdown  # noqa: E402
from risk_classifier import classify_risk, combine_index_risks  # noqa: E402

SKILL_ID = "india-ibd-distribution-day-monitor"
REPORT_PREFIX = "india_ibd_distribution_day_monitor"
RULE_VERSION = "ibd_dd_india_v1.0"
DEFAULT_CONFIG_PATH = HERE.parent / "config" / "default.yaml"
DEFAULT_INDEXES = [
    {"symbol": "NIFTY 50", "benchmark_name": "NIFTY 50 index (NSE large caps)"},
    {"symbol": "NIFTY 500", "benchmark_name": "NIFTY 500 index (NSE broad market)"},
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="India IBD Distribution Day Monitor (NSE indexes)")
    p.add_argument(
        "--symbols", default=None, help='Comma-separated NSE indexes, e.g. "NIFTY 50,NIFTY 500"'
    )
    p.add_argument("--lookback-days", type=int, default=None, help="Sessions to load (default 80)")
    p.add_argument(
        "--instrument", default=None, choices=supported_instruments(), help="Exposure policy"
    )
    p.add_argument("--current-exposure", type=int, default=None)
    p.add_argument("--base-trailing-stop", type=int, default=None)
    p.add_argument(
        "--as-of", default=None, help="YYYY-MM-DD; default = latest completed NSE session"
    )
    p.add_argument("--config", default=None)
    p.add_argument("--cache-dir", type=Path, default=None, help="India data cache directory")
    p.add_argument("--output-dir", default="reports/")
    return p.parse_args(argv)


def _build_explanation(symbol: str, d5: int, d15: int, d25: int, risk: str, today_dd: bool) -> str:
    today = "is a distribution day today" if today_dd else "is not a distribution day today"
    return (
        f"{symbol} {today}. Active distribution days within 5/15/25 elapsed sessions: "
        f"{d5}/{d15}/{d25}. Risk level: {risk}."
    )


def _build_cluster_state(d5: int, d15: int, d25: int) -> dict:
    return {
        "has_d5_cluster": d5 >= 2,
        "has_d15_cluster": d15 >= 3,
        "has_d25_cluster": d25 >= 5,
        "cluster_description": f"within 5/15/25 elapsed sessions: {d5}/{d15}/{d25}",
    }


class ConfigError(ValueError):
    """A configuration-shape diagnostic that never embeds file contents."""


def load_config(path: str | None) -> dict:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"config not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        try:
            config = yaml.safe_load(f)
        except UnicodeError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            # SafeLoader's scalar constructors can raise plain Python errors
            # (e.g. !!int/!!bool/!!timestamp). Their text may expose a key.
            raise ConfigError("invalid YAML value or excessive nesting") from None
    if config is None:
        return {}  # Preserve empty/comment-only/null YAML as default configuration.
    if not isinstance(config, dict):
        raise ConfigError("top-level YAML must be a mapping (key/value pairs)")
    for field in ("data", "strategy_context"):
        value = config.get(field)
        if value is None:
            if field in config:
                config[field] = {}
        elif not isinstance(value, dict):
            raise ConfigError(f"{field} must be a mapping or null")
    indexes = config.get("indexes")
    if indexes is not None:
        if not isinstance(indexes, list):
            raise ConfigError("indexes must be a list or null")
        for index, entry in enumerate(indexes):
            if not isinstance(entry, dict):
                raise ConfigError(f"indexes[{index}] must be a mapping")
            symbol = entry.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                raise ConfigError(f"indexes[{index}].symbol must be a non-empty string")
    return config


def _build_rule(config: dict) -> DistributionDayRule:
    rule_cfg = config.get("distribution_day_rule") or {}
    return DistributionDayRule(
        min_decline_pct=rule_cfg.get("min_decline_pct", -0.002),
        expiration_sessions=rule_cfg.get("expiration_sessions", 25),
        invalidation_gain_pct=rule_cfg.get("invalidation_gain_pct", 0.05),
        invalidation_price_source=rule_cfg.get("invalidation_price_source", "high"),
    )


def _build_thresholds(config: dict) -> RiskThresholds:
    t_cfg = config.get("risk_thresholds") or {}
    high = t_cfg.get("high") or {}
    severe = t_cfg.get("severe") or {}
    return RiskThresholds(
        caution_d25=(t_cfg.get("caution") or {}).get("d25_count", 3),
        high_d25=high.get("d25_count", 5),
        high_d15=high.get("d15_count", 3),
        high_d5=high.get("d5_count", 2),
        severe_d25=severe.get("d25_count", 6),
        severe_d15=severe.get("d15_count", 4),
        severe_ma_d25=severe.get("severe_ma_d25", 5),
    )


def _required_min_sessions(config: dict, rule: DistributionDayRule) -> int:
    lookback = config.get("lookback_days", 80)
    ma_cfg = config.get("moving_average_filters") or {}
    sma_periods = ma_cfg.get("sma_periods") or []
    ema_periods = ma_cfg.get("ema_periods") or []
    return max(
        int(lookback),
        max(sma_periods, default=0),
        max(ema_periods, default=0),
        rule.expiration_sessions + 2,
    )


def _compute_ma_filters(effective_history: list[dict], ma_cfg: dict) -> tuple[dict, list[str]]:
    """Compute close_above_21ema, close_above_50sma, market_below_21ema_or_50ma."""
    audit_flags: list[str] = []
    if not ma_cfg.get("enabled", True) or not effective_history:
        return {
            "close_above_21ema": None,
            "close_above_50sma": None,
            "market_below_21ema_or_50ma": None,
        }, audit_flags

    closes = [row["close"] for row in effective_history]
    today_close = closes[0]

    ema_period = (ma_cfg.get("ema_periods") or [21])[0]
    sma_period = (ma_cfg.get("sma_periods") or [50])[0]

    def _ma_or_none(fn, period):
        if len(closes) < period:
            return None
        try:
            return fn(closes, period)
        except ValueError:
            return None

    ema_val = _ma_or_none(calc_ema, ema_period)
    sma_val = _ma_or_none(calc_sma, sma_period)

    close_above_ema = (today_close > ema_val) if ema_val is not None else None
    close_above_sma = (today_close > sma_val) if sma_val is not None else None

    if close_above_ema is None or close_above_sma is None:
        audit_flags.append("insufficient_data_for_moving_average")
        market_below = None
    else:
        # market_below = True only when close is below BOTH MAs
        market_below = (not close_above_ema) and (not close_above_sma)

    return {
        "close_above_21ema": close_above_ema,
        "close_above_50sma": close_above_sma,
        "market_below_21ema_or_50ma": market_below,
    }, audit_flags


def _record_to_dict(r: DDRecord) -> dict:
    return asdict(r)


def _today_dict(effective_history: list[dict], records: list[DDRecord]) -> dict:
    """Return summary fields for the evaluation session."""
    if not effective_history:
        return {}
    today = effective_history[0]
    yesterday = effective_history[1] if len(effective_history) > 1 else None
    pct_change = None
    volume_change_pct = None
    if yesterday and today.get("close") and yesterday.get("close"):
        pct_change = today["close"] / yesterday["close"] - 1
    if yesterday and today.get("volume") and yesterday.get("volume"):
        volume_change_pct = today["volume"] / yesterday["volume"] - 1
    return {
        "date": today.get("date"),
        "close": today.get("close"),
        "previous_close": yesterday.get("close") if yesterday else None,
        "pct_change": pct_change,
        "volume": today.get("volume"),
        "previous_volume": yesterday.get("volume") if yesterday else None,
        "volume_change_pct": volume_change_pct,
    }


def analyze_index(
    symbol: str,
    benchmark_name: str,
    history: list[dict],
    config: dict,
    rule: DistributionDayRule,
    thresholds: RiskThresholds,
    as_of: str | None,
) -> tuple[IndexResult, list[str]]:
    """Run the full per-index pipeline and return the IndexResult."""
    required_min = _required_min_sessions(config, rule)
    effective_history, hist_audit = prepare_effective_history(history, as_of, required_min)
    audit_flags = list(hist_audit.get("audit_flags", []))

    raw_records, skipped = detect_distribution_days(effective_history, rule)
    records = enrich_records(raw_records, effective_history, rule)

    active = [r for r in records if r.status == "active"]
    removed = [r for r in records if r.status != "active"]

    d5 = count_active_in_window(records, 5)
    d15 = count_active_in_window(records, 15)
    d25 = count_active_in_window(records, 25)

    ma_cfg = config.get("moving_average_filters") or {}
    trend_filters, ma_flags = _compute_ma_filters(effective_history, ma_cfg)
    audit_flags.extend(ma_flags)

    market_below = trend_filters.get("market_below_21ema_or_50ma")
    risk = classify_risk(d5, d15, d25, market_below, thresholds)

    today_is_dd = any(r.dd_index == 0 and r.status == "active" for r in records)
    explanation = _build_explanation(symbol, d5, d15, d25, risk, today_is_dd)

    result = IndexResult(
        symbol=symbol,
        benchmark_name=benchmark_name,
        is_distribution_day_today=today_is_dd,
        today=_today_dict(effective_history, records),
        d5_count=d5,
        d15_count=d15,
        d25_count=d25,
        active_distribution_days=[_record_to_dict(r) for r in active],
        removed_distribution_days=[_record_to_dict(r) for r in removed],
        risk_level=risk,
        cluster_state=_build_cluster_state(d5, d15, d25),
        trend_filters=trend_filters,
        explanation=explanation,
        skipped_sessions=skipped,
    )
    return result, audit_flags


def build_payload(
    config: dict,
    rule: DistributionDayRule,
    thresholds: RiskThresholds,
    index_results: list[IndexResult],
    overall_risk: str,
    portfolio_action: dict,
    aggregate_audit: dict,
) -> dict:
    """Assemble the final JSON payload."""
    primary = index_results[0].symbol if index_results else None

    return {
        "market_distribution_state": {
            "as_of": aggregate_audit.get("as_of_resolved"),
            "generated_at": aggregate_audit.get("generated_at"),
            "overall_risk_level": overall_risk,
            "primary_signal_symbol": primary,
            "index_results": [_index_result_to_dict(r) for r in index_results],
        },
        "portfolio_action": portfolio_action,
        "rule_evaluation": {
            "rule_version": RULE_VERSION,
            "distribution_day_rule": {
                "min_decline_pct": rule.min_decline_pct,
                "expiration_sessions": rule.expiration_sessions,
                "invalidation_gain_pct": rule.invalidation_gain_pct,
                "invalidation_price_source": rule.invalidation_price_source,
            },
            "thresholds_used": {
                "caution_d25": thresholds.caution_d25,
                "high_d25": thresholds.high_d25,
                "high_d15": thresholds.high_d15,
                "high_d5": thresholds.high_d5,
                "severe_d25": thresholds.severe_d25,
                "severe_d15": thresholds.severe_d15,
                "severe_ma_d25": thresholds.severe_ma_d25,
            },
        },
        "audit": aggregate_audit,
    }


def _index_result_to_dict(r: IndexResult) -> dict:
    return asdict(r)


def _resolve_indexes(config: dict, symbols_override: list[str] | None) -> list[dict]:
    indexes_cfg = config.get("indexes") or DEFAULT_INDEXES
    if symbols_override:
        # The override defines the list; configured labels are reused when known.
        known = {i["symbol"].upper(): i for i in indexes_cfg}
        indexes_cfg = [
            known.get(s.upper(), {"symbol": s, "benchmark_name": f"{s} index (NSE)"})
            for s in dict.fromkeys(symbols_override)
        ]
    return [
        dict(item, instrument_name=item.get("benchmark_name", item["symbol"]))
        for item in indexes_cfg
    ]


def _resolve_end(as_of: str | None, latest: date, sessions_fn) -> date:
    """Latest completed session, or the last NSE session on/before --as-of."""
    if as_of is None:
        return latest
    try:
        requested = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"--as-of must be YYYY-MM-DD, got {as_of!r}") from exc
    if requested > latest:
        raise ValueError(f"--as-of {as_of} is after the latest completed NSE session {latest}")
    sessions = sessions_fn(requested - timedelta(days=21), requested)
    if not sessions:
        raise ValueError(f"no NSE session on or before {as_of}")
    return sessions[-1]


def main(argv: list[str] | None = None, *, cache=None, now_fn=None) -> int:  # noqa: C901
    args = parse_args(argv)
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    try:
        config = load_config(args.config)
    except OSError as exc:
        print(
            f"Error: cannot read configuration {config_path} ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1
    except UnicodeError:
        print(f"Error: configuration {config_path} must be UTF-8 YAML.", file=sys.stderr)
        return 1
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        print(f"Error: configuration {config_path} has invalid YAML{location}.", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"Error: invalid configuration {config_path}: {exc}.", file=sys.stderr)
        return 1

    if args.lookback_days is not None:
        config["lookback_days"] = args.lookback_days
    strategy_ctx = config.setdefault("strategy_context", {}) or {}
    config["strategy_context"] = strategy_ctx
    if args.instrument:
        strategy_ctx["instrument"] = args.instrument
    if args.current_exposure is not None:
        strategy_ctx["current_exposure_pct"] = args.current_exposure
    if args.base_trailing_stop is not None:
        strategy_ctx["base_trailing_stop_pct"] = args.base_trailing_stop
    symbols_override = (
        [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    )
    indexes_cfg = _resolve_indexes(config, symbols_override)

    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if cache is None:
        cache = india.BarCache(cache_root, now_fn=now_fn)
    try:
        latest = india.latest_completed_session(now_fn(), sessions_fn=cache._sessions_fn)
        end = _resolve_end(args.as_of, latest, cache._sessions_fn)
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rule = _build_rule(config)
    thresholds = _build_thresholds(config)
    lookback = int(config.get("lookback_days", 80))
    required = _required_min_sessions(config, rule)

    audit_flags: list[str] = []
    symbols_loaded: list[str] = []
    skipped_all: list[dict] = []
    muhurat_removed: dict = {}
    index_results: list[IndexResult] = []
    as_of_resolved: str | None = None
    for entry in indexes_cfg:
        symbol = entry["symbol"]
        history, fetch_audit = fetch_index_ohlcv(cache, symbol, max(lookback, required) + 5, end)
        audit_flags.extend(fetch_audit.get("audit_flags", []))
        skipped_all.extend(fetch_audit.get("skipped_sessions", []))
        if fetch_audit.get("muhurat_sessions_removed"):
            muhurat_removed[symbol] = fetch_audit["muhurat_sessions_removed"]
        if fetch_audit.get("error"):
            print(f"Warning: {symbol}: {fetch_audit['error']}", file=sys.stderr)
        if not history:
            continue
        as_of_arg = end.isoformat() if args.as_of else None
        try:
            result, idx_flags = analyze_index(
                symbol=symbol,
                benchmark_name=entry.get("benchmark_name") or symbol,
                history=history,
                config=config,
                rule=rule,
                thresholds=thresholds,
                as_of=as_of_arg,
            )
        except ValueError as exc:
            print(f"Warning: {symbol}: {exc}", file=sys.stderr)
            audit_flags.append("as_of_not_in_history")
            continue
        symbols_loaded.append(symbol)
        audit_flags.extend(idx_flags)
        skipped_all.extend(result.skipped_sessions)
        if as_of_resolved is None and result.today.get("date"):
            as_of_resolved = result.today["date"]
        index_results.append(result)

    if not index_results:
        print("Error: no NSE index history could be loaded", file=sys.stderr)
        return 1

    overall_risk = combine_index_risks(index_results)
    action = generate_portfolio_action(
        risk_level=overall_risk,
        instrument=strategy_ctx.get("instrument", "SWING_BOOK"),
        current_exposure_pct=int(strategy_ctx.get("current_exposure_pct", 100)),
        base_trailing_stop_pct=int(strategy_ctx.get("base_trailing_stop_pct", 10)),
    )
    aggregate_audit = {
        "data_source": "nse_bhavcopy",
        "exchange": india.EXCHANGE,
        "rule_version": RULE_VERSION,
        "as_of_resolved": as_of_resolved,
        "expected_session": end.isoformat(),
        "lookback_days": lookback,
        "symbols_requested": [i["symbol"] for i in indexes_cfg],
        "symbols_loaded": symbols_loaded,
        "instrument_identity": indexes_cfg,
        "muhurat_sessions_removed": muhurat_removed,
        "skipped_sessions": skipped_all,
        "audit_flags": sorted(set(audit_flags)),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_snapshot": {
            "lookback_days": lookback,
            "moving_average_filters": config.get("moving_average_filters"),
            "strategy_context": strategy_ctx,
        },
    }
    payload = build_payload(
        config=config,
        rule=rule,
        thresholds=thresholds,
        index_results=index_results,
        overall_risk=overall_risk,
        portfolio_action=asdict(action),
        aggregate_audit=aggregate_audit,
    )
    payload["skill"] = SKILL_ID
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = output_dir / f"{REPORT_PREFIX}_{stamp}.json"
    md_path = output_dir / f"{REPORT_PREFIX}_{stamp}.md"
    write_json(payload, json_path)
    write_markdown(payload, md_path)
    for r in index_results:
        print(
            f"{r.symbol}: risk {r.risk_level} (d5/d15/d25 = {r.d5_count}/{r.d15_count}/{r.d25_count})"
        )
    print(
        f"Overall: {overall_risk} -> {action.recommended_action} (target exposure {action.target_exposure_pct}%)"
    )
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
