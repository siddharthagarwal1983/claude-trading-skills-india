"""Walk-forward replay of India swing workflows on NSE end-of-day bars.

Two strategies share one simulator:

* ``vcp`` — india-vcp-screener's funnel, then india-breakout-trade-planner's
  ``generate_plans``: buy-stop-limit orders above the pivot, stop under the last
  contraction low, 2R target.
* ``momentum-burst`` — india-stockbee-momentum-burst-screener's
  ``analyze_symbol``, then the rules in its references: a next-session buy
  limit a little above the trigger close, stop at the trigger-day low, time exit
  after a few sessions; sized like the breakout planner.

Every NSE session D of the test window:

1. **Exits** — positions entered before D are checked against D's bar with the
   paper ledger's exit rule (stop before target when one bar touches both;
   stop fills get the stop slippage), then the time stop. Dividends with
   ex-date D are credited.
2. **Entries** — orders planned the evening before are filled with the paper
   ledger's entry rule for their type (stop-limit or limit; one-price circuit
   sessions do not fill), if cash allows.
3. **Mark to market** at D's close.
4. **Plan** — D's screen (the screener's own code on bars up to D) is turned into
   orders for the next session.

Nothing after D is visible when D is screened or planned. The simulation runs on
bars adjusted for splits and bonuses as of the data download, so share counts are
in today's units; rupee amounts are unaffected. Screens see each session's prices
as quoted then (point in time): only actions with ex-date on or before D adjust
the bars a screen of D reads, and plan prices are converted back to today's units.

Phase 1.1 corrections (docs/research/PHASE1_1A_BACKTESTER_FIX_REPORT.md), each with
a switch that restores the earlier behaviour: LIMIT entries are stopped on the entry
session when its low reaches the stop (F2); equity is valued net of estimated
selling charges (F3); an optional historical regime gate (F4, off by default);
point-in-time screening prices (F7b); an optional trade-through rule for limit
fills (A1, off by default).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from _india_data import IST, PRICE_FIELDS, adjustment_factors, nse_tick_size, round_to_tick

SCREEN_SCHEMA = 1
HISTORY_CALENDAR_DAYS = 430  # same window as the live screener
PLAN_TIME = time(20, 0)  # plans are made after the bhavcopy, as in live use
REGIME_SMA = 200
STRATEGIES = ("vcp", "momentum-burst")
MB_SIGNAL_STATES = ("ACTIONABLE_DAY1", "MANUAL_REVIEW")
MB_SKIP_TAGS = ("possible_upper_circuit",)  # the screener sends these to manual review

# Phase 1.1 modes. The first value of each is the default.
LIQUIDATION_VALUATIONS = ("net", "gross")
REGIME_MODES = ("no_gate", "sma200_proxy", "exposure_file")
LIMIT_FILLS = ("touch", "trade_through")
SCREEN_PRICES = ("point_in_time", "back_adjusted")
REGIME_ACTIONS = ("NEW_ENTRY_ALLOWED", "REDUCE_ONLY", "CASH_PRIORITY")
ENTRY_ALLOWED = "NEW_ENTRY_ALLOWED"
REGIME_DESCRIPTIONS = {
    "no_gate": "no regime gate: entries in every regime (pre-Phase-1.1 behaviour); "
    "momentum screens use the screener's default market gate (neutral) every session",
    "sma200_proxy": "PROXY, not the india-exposure-coach: NIFTY 500 close vs its 200-session "
    "average at the signal close (at or above = NEW_ENTRY_ALLOWED, below = REDUCE_ONLY, "
    "fewer than 200 closes = unknown, treated as REDUCE_ONLY)",
    "exposure_file": "per-session india-exposure-coach actions read from --regime-file; "
    "sessions missing from the file are unknown and treated as REDUCE_ONLY",
}


# --------------------------------------------------------------------------- market data


@dataclass
class Series:
    """Oldest-first adjusted bars of one symbol or index."""

    bars: list
    warnings: list = field(default_factory=list)
    actions: list = field(default_factory=list)

    def __post_init__(self):
        self.bars = sorted(self.bars, key=lambda b: b["date"])
        self.dates = [b["date"] for b in self.bars]
        self.by_date = {b["date"]: b for b in self.bars}
        self.adj_events = adjustment_factors(self.actions)  # [(ex_date, price factor)]

    def window(self, start: str, end: str) -> list:
        lo = bisect.bisect_left(self.dates, start)
        hi = bisect.bisect_right(self.dates, end)
        return self.bars[lo:hi]

    def last_on_or_before(self, day: str) -> dict | None:
        i = bisect.bisect_right(self.dates, day)
        return self.bars[i - 1] if i else None


@dataclass
class MarketData:
    rows: list  # universe rows: symbol, company, industry
    stocks: dict  # symbol -> Series
    benchmark: Series  # RS benchmark of the screener (default NIFTY 500)
    benchmark_name: str

    @property
    def sessions(self) -> list:
        return self.benchmark.dates


def _window_start(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=HISTORY_CALENDAR_DAYS)).isoformat()


# --------------------------------------------------------------------------- point in time


def future_factor(series: Series, day: str) -> float:
    """Product of split/bonus price factors with ex-date after ``day`` (1.0 if none).

    The series is back-adjusted for every action in the data; dividing its prices by
    this factor (and multiplying volumes) gives the prices as quoted on ``day``.
    """
    f = 1.0
    for ex_date, factor in series.adj_events:
        if ex_date > day:
            f *= factor
    return f


def point_in_time_bars(bars: list, factor: float) -> list:
    """Back-adjusted bars restated in the units quoted when ``factor`` was still ahead."""
    if math.isclose(factor, 1.0):
        return bars
    out = []
    for bar in bars:
        new = dict(bar)
        for name in PRICE_FIELDS:
            if new.get(name) is not None:
                new[name] = round(new[name] / factor, 4)
        if new.get("volume") is not None:
            new["volume"] = int(round(new["volume"] * factor))
        if new.get("adj_factor") is not None:
            new["adj_factor"] = new["adj_factor"] / factor
        out.append(new)
    return out


def to_sim_units(price: float | None, factor: float) -> float | None:
    """A price quoted on the signal day, in the simulation's (today's) units."""
    if price is None or math.isclose(factor, 1.0):
        return price
    return round(price * factor, 6)


# --------------------------------------------------------------------------- regime


def sma200_action(benchmark: Series, day: str) -> str | None:
    """Proxy posture at ``day``'s close: benchmark at/above its 200-session SMA, else not."""
    i = bisect.bisect_right(benchmark.dates, day)
    if i < REGIME_SMA:
        return None
    closes = [b["close"] for b in benchmark.bars[i - REGIME_SMA : i]]
    return ENTRY_ALLOWED if closes[-1] >= sum(closes) / REGIME_SMA else "REDUCE_ONLY"


def regime_actions(
    mode: str, benchmark: Series, sessions: list, file_map: dict | None = None
) -> dict | None:
    """{session: action or None} for a gated mode; None for ``no_gate``."""
    if mode not in REGIME_MODES:
        raise ValueError(f"regime_mode must be one of {REGIME_MODES}")
    if mode == "no_gate":
        return None
    if mode == "sma200_proxy":
        return {d: sma200_action(benchmark, d) for d in sessions}
    if file_map is None:
        raise ValueError("regime_mode exposure_file needs a regime file")
    return {d: file_map.get(d) for d in sessions}


def load_regime_file(path) -> dict:
    """{date: action} from JSON: {"rows": [{date, action}]}, a list of rows, or {date: action}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        data = data["rows"]
    if isinstance(data, list):
        pairs = [(row.get("date"), row.get("action")) for row in data]
    elif isinstance(data, dict):
        pairs = list(data.items())
    else:
        raise ValueError("regime file must be a JSON object or list")
    out = {}
    for day, action in pairs:
        date.fromisoformat(str(day))
        if action not in REGIME_ACTIONS:
            raise ValueError(f"regime file: unknown action {action!r} on {day}")
        out[str(day)] = action
    return out


def market_gate_for(action: str | None) -> str:
    """india-exposure-coach SKILL.md step 4: NEW_ENTRY_ALLOWED -> allowed, otherwise
    restrictive (CASH_PRIORITY also skips new entries; unknown counts as REDUCE_ONLY)."""
    return "allowed" if action == ENTRY_ALLOWED else "restrictive"


# --------------------------------------------------------------------------- screening


SLIM_FIELDS = (
    "symbol",
    "company_name",
    "sector",
    "price",
    "composite_score",
    "rating",
    "execution_state",
    "valid_vcp",
    "avg_turnover_cr",
    "distance_from_pivot_pct",
)


def slim_result(r: dict) -> dict:
    """The fields india-breakout-trade-planner reads, nothing else."""
    out = {k: r.get(k) for k in SLIM_FIELDS}
    contractions = (r.get("vcp_pattern") or {}).get("contractions") or []
    out["vcp_pattern"] = {
        "pivot_price": (r.get("vcp_pattern") or {}).get("pivot_price"),
        "contractions": [{"low_price": contractions[-1].get("low_price")}] if contractions else [],
    }
    out["volume_pattern"] = {
        "breakout_volume_detected": (r.get("volume_pattern") or {}).get("breakout_volume_detected")
    }
    out["pivot_proximity"] = {
        "distance_from_pivot_pct": (r.get("pivot_proximity") or {}).get("distance_from_pivot_pct")
    }
    return out


def screen_day(
    day: str,
    data: MarketData,
    screener,
    args: argparse.Namespace,
    top: int,
    *,
    pit: bool = True,
) -> dict:
    """india-vcp-screener's funnel as of ``day`` using only bars dated on or before it.

    With ``pit`` (default) the bars are restated as quoted on ``day``; the factor used
    for each restated symbol is returned in ``pit_factors`` so plans can be converted.
    """
    start = _window_start(day)
    outcomes: dict = {}
    histories: dict = {}
    pit_factors: dict = {}
    for row in data.rows:
        sym = row["symbol"]
        series = data.stocks.get(sym)
        if series is None:
            outcomes[sym] = "missing_data"
            continue
        bars = series.window(start, day)
        if not bars:
            outcomes[sym] = "missing_data"
            continue
        if pit:
            factor = future_factor(series, day)
            if not math.isclose(factor, 1.0):
                bars = point_in_time_bars(bars, factor)
                pit_factors[sym] = factor
        warnings = [w for w in series.warnings if w["date"] <= day]
        history, outcome = screener.prepare_history(sym, bars, warnings, day)
        if history is None:
            outcomes[sym] = outcome
            continue
        histories[sym] = history
    bench = screener.to_history(data.benchmark.window(start, day))
    if len(bench) < screener.MIN_HISTORY_BARS:
        return {"date": day, "skipped": f"benchmark has only {len(bench)} bars", "results": []}
    results, pre, trend = screener.run_funnel(data.rows, histories, bench, args, outcomes)
    return {
        "date": day,
        "results": [slim_result(r) for r in results[:top]],
        "pit_factors": pit_factors,
        "funnel": {
            "universe": len(data.rows),
            "evaluated": len(histories),
            "pre_filter_passed": len(pre),
            "trend_template_passed": len(trend),
            "vcp_candidates": len(results),
        },
    }


MB_FIELDS = (
    "symbol",
    "date",
    "state",
    "rating",
    "setup_score",
    "entry_reference",
    "stop_reference",
    "risk_pct_to_stop",
    "primary_trigger",
    "soft_failure_tags",
    "day_gain_pct",
    "turnover_cr",
)


def screen_day_momentum(
    day: str,
    data: MarketData,
    mb,
    args: argparse.Namespace,
    *,
    pit: bool = True,
    gate: str | None = None,
) -> dict:
    """india-stockbee-momentum-burst-screener's analysis as of ``day`` (bars up to it).

    ``pit``: bars as quoted on ``day`` (see ``screen_day``). ``gate``: the session's
    ``--market-gate`` value; None keeps ``args.market_gate`` (the screener default).
    """
    args._expected_session = date.fromisoformat(day)
    start = (
        date.fromisoformat(day) - timedelta(days=int(args.history_days * 1.6) + 30)
    ).isoformat()
    saved_gate = getattr(args, "market_gate", None)
    if gate is not None:
        args.market_gate = gate
    results, pit_factors = [], {}
    try:
        for row in data.rows:
            series = data.stocks.get(row["symbol"])
            if series is None:
                continue
            window = series.window(start, day)
            if pit and window:
                factor = future_factor(series, day)
                if not math.isclose(factor, 1.0):
                    window = point_in_time_bars(window, factor)
                    pit_factors[row["symbol"]] = factor
            bars = mb.normalize_bars(window)
            if bars:
                results.append(mb.analyze_symbol(row["symbol"], bars, args))
    finally:
        if gate is not None:
            args.market_gate = saved_gate
    states: dict = {}
    for r in results:
        states[r["state"]] = states.get(r["state"], 0) + 1
    kept = [r for r in mb.sort_results(results) if r["state"] != "REJECTED"]
    out = {
        "date": day,
        "results": [{k: r.get(k) for k in MB_FIELDS} for r in kept],
        "pit_factors": {
            r["symbol"]: pit_factors[r["symbol"]] for r in kept if r["symbol"] in pit_factors
        },
        "funnel": {"universe": len(data.rows), "analyzed": len(results), "states": states},
    }
    if gate is not None:
        out["market_gate"] = gate
    return out


def screen_key(
    universe: str,
    benchmark: str,
    args: argparse.Namespace,
    top: int,
    data,
    strategy: str = "vcp",
    extra: dict | None = None,
) -> str:
    """Cache key: strategy, screener settings plus every split/bonus factor in the data.

    ``extra`` holds backtest-side screening settings (point-in-time prices, per-session
    market gates); left out when empty so pre-Phase-1.1 caches keep their keys.
    """
    settings = {
        k: v
        for k, v in sorted(vars(args).items())
        if k not in ("output_dir", "cache_dir", "symbols") and not k.startswith("_")
    }
    factors = sorted(
        (sym, a.get("ex_date"), a.get("factor"))
        for sym, s in data.stocks.items()
        for a in s.actions
        if a.get("factor") not in (None, 1, 1.0)
    )
    payload = {
        "schema": SCREEN_SCHEMA,
        "strategy": strategy,
        "universe": universe,
        "symbols": sorted(data.stocks),
        "benchmark": benchmark,
        "top": top,
        "settings": settings,
        "factors": factors,
    }
    if extra:
        payload["extra"] = extra
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class ScreenCache:
    """One JSON line per screened session, appended as screening progresses."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.days: dict = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue  # a run killed mid-write leaves a partial line
                        self.days[row["date"]] = row

    def add(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        self.days[row["date"]] = row


def screen_range(
    sessions: list,
    data: MarketData,
    screener,
    args: argparse.Namespace,
    top: int,
    cache: ScreenCache,
    progress: Callable[[str], None] = lambda _m: None,
    strategy: str = "vcp",
    pit: bool = True,
    gates: dict | None = None,
) -> dict:
    """Screen every session not yet cached. ``gates``: {session: market gate} (momentum)."""
    todo = [d for d in sessions if d not in cache.days]
    for n, day in enumerate(todo, 1):
        if strategy == "vcp":
            cache.add(screen_day(day, data, screener, args, top, pit=pit))
        else:
            gate = gates.get(day) if gates is not None else None
            cache.add(screen_day_momentum(day, data, screener, args, pit=pit, gate=gate))
        if n % 20 == 0 or n == len(todo):
            progress(f"  screened {n}/{len(todo)} sessions (last {day})")
    return {d: cache.days[d] for d in sessions}


# --------------------------------------------------------------------------- simulation


@dataclass
class SimParams:
    strategy: str = "vcp"
    capital: float = 1_000_000.0
    risk_pct: float = 0.5
    max_position_pct: float = 10.0
    max_sector_pct: float = 30.0
    max_portfolio_heat_pct: float = 6.0
    target_r_multiple: float = 2.0
    stop_buffer_pct: float = 1.0
    max_chase_pct: float = 2.0
    pivot_buffer_pct: float = 0.1
    max_adv_pct: float = 1.0
    valid_sessions: int = 1
    brokerage_per_order: float = 0.0
    dp_charge: float = 15.93
    slippage_bps: float = 10.0
    max_hold_sessions: int | None = None
    # vcp: research-only planner rules (live: 8% cap, score 70+, nothing below 70 sized).
    # momentum-burst: risk cap at the limit price and minimum setup score (80 = ACTIONABLE_DAY1).
    max_risk_pct: float = 8.0
    min_score: float = 70.0
    developing_multiplier: float = 0.5
    # Phase 1.1 switches (see the module docstring); defaults are the corrected behaviour,
    # except the regime gate and the limit-fill rule, which keep the earlier semantics.
    entry_session_stop: bool = True  # F2: LIMIT fills stopped on the entry session
    liquidation_valuation: str = "net"  # F3: "net" of estimated selling charges, or "gross"
    regime_mode: str = "no_gate"  # F4: "no_gate", "sma200_proxy" or "exposure_file"
    limit_fill: str = "touch"  # A1: "touch" or "trade_through" (sensitivity)

    @classmethod
    def for_strategy(cls, strategy: str, **values) -> SimParams:
        """Defaults for a strategy; momentum bursts exit on time, not at a target."""
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}")
        base = {"strategy": strategy}
        if strategy == "momentum-burst":
            base.update(
                target_r_multiple=0.0, max_hold_sessions=5, max_risk_pct=10.0, min_score=80.0
            )
        base.update({k: v for k, v in values.items() if v is not None})
        return cls(**base)

    PLAN_FIELDS = (
        "risk_pct",
        "max_position_pct",
        "max_sector_pct",
        "max_portfolio_heat_pct",
        "target_r_multiple",
        "stop_buffer_pct",
        "max_chase_pct",
        "pivot_buffer_pct",
        "max_adv_pct",
        "valid_sessions",
        "brokerage_per_order",
        "dp_charge",
    )


LIVE_MAX_RISK_PCT = 8.0
LIVE_MIN_SCORE = 70.0


def validate_params(p: SimParams) -> None:
    if p.strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}")
    if not 0 < p.max_risk_pct <= 30:
        raise ValueError("max_risk_pct must be above 0 and at most 30")
    low = 60 if p.strategy == "vcp" else 55
    if not low <= p.min_score <= 100:
        raise ValueError(f"min_score must be between {low} and 100")
    if p.target_r_multiple < 0 or (p.strategy == "vcp" and p.target_r_multiple <= 0):
        raise ValueError("target_r_multiple must be positive (0 = no target, momentum-burst only)")
    if p.strategy == "momentum-burst" and not p.max_hold_sessions:
        raise ValueError("momentum-burst needs max_hold_sessions (the 3-5 session exit)")
    if not 0 <= p.developing_multiplier <= 1.75:
        raise ValueError("developing_multiplier must be between 0 and 1.75")
    if p.max_hold_sessions is not None and p.max_hold_sessions < 1:
        raise ValueError("max_hold_sessions must be at least 1")
    if not isinstance(p.entry_session_stop, bool):
        raise ValueError("entry_session_stop must be true or false")
    for name, allowed in (
        ("liquidation_valuation", LIQUIDATION_VALUATIONS),
        ("regime_mode", REGIME_MODES),
        ("limit_fill", LIMIT_FILLS),
    ):
        if getattr(p, name) not in allowed:
            raise ValueError(f"{name} must be one of {allowed}")


MODE_FIELDS = ("entry_session_stop", "liquidation_valuation", "regime_mode", "limit_fill")


@contextmanager
def planner_rules(planner, p: SimParams):
    """Temporarily apply the research rules to the planner module, then restore it.

    Scores in [min_score, 70) are planned as the 'developing' band, sized with
    ``developing_multiplier`` (the live planner sizes them at zero); scores below
    ``min_score`` count as 'weak' and are never planned.
    """
    saved = {
        name: getattr(planner, name)
        for name in ("MAX_RISK_PCT", "PLAN_BANDS", "get_rating_band", "get_sizing_multiplier")
    }
    live_band, live_multiplier = saved["get_rating_band"], saved["get_sizing_multiplier"]

    def band(score):
        return "weak" if score < p.min_score else live_band(score)

    def multiplier(name):
        return p.developing_multiplier if name == "developing" else live_multiplier(name)

    planner.MAX_RISK_PCT = p.max_risk_pct
    planner.get_rating_band = band
    planner.get_sizing_multiplier = multiplier
    if p.min_score < LIVE_MIN_SCORE:
        planner.PLAN_BANDS = tuple(saved["PLAN_BANDS"]) + ("developing",)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(planner, name, value)


class Simulator:
    def __init__(
        self,
        data: MarketData,
        screens: dict,
        params: SimParams,
        planner,
        paper,
        regime: dict | None = None,
    ):
        """``regime``: {session: exposure action or None}, required for a gated regime_mode."""
        validate_params(params)
        if params.regime_mode != "no_gate" and regime is None:
            raise ValueError(f"regime_mode {params.regime_mode} needs a regime map")
        self.data = data
        self.screens = screens
        self.p = params
        self.planner = planner
        self.eng = paper
        self.regime = regime if params.regime_mode != "no_gate" else None
        self.cash = float(params.capital)
        self.positions: dict = {}  # symbol -> position
        self.pending: list = []  # orders for the next session
        self.trades: list = []
        self.curve: list = []
        self.events = {
            "screened_sessions": 0,
            "actionable_plans": 0,
            "deferred_heat": 0,
            "constrained": 0,
            "orders_filled": 0,
            "orders_expired": 0,
            "orders_gapped_past_limit": 0,
            "rejected_risk": 0,
            "orders_rejected_cash": 0,
            "orders_locked": 0,
            "orders_suspended": 0,
            "exits_locked": 0,
            "dividends": 0.0,
            "entry_session_stops": 0,  # F2: LIMIT fills stopped on the entry session
            "entry_session_stop_touch_kept": 0,  # entry-day low <= stop, position kept
            "regime_blocked_sessions": 0,  # F4: screened sessions with entries blocked
            "limit_touch_only": 0,  # A1: limit fills where the low only touched the limit
            "limit_touch_skipped": 0,  # A1: of those, not filled under trade_through
        }
        self._calendar = [date.fromisoformat(d) for d in data.sessions]

    # ------------------------------------------------------------- helpers

    def _sessions_fn(self, start: date, end: date) -> list:
        lo = bisect.bisect_left(self._calendar, start)
        hi = bisect.bisect_right(self._calendar, end)
        return self._calendar[lo:hi]

    def _charges(self, turnover: float, *, buy: bool) -> dict:
        return self.eng.leg_charges(
            turnover, buy=buy, brokerage=self.p.brokerage_per_order, dp_charge=self.p.dp_charge
        )

    def _close(self, sym: str, day: str) -> float | None:
        bar = self.data.stocks[sym].last_on_or_before(day)
        return bar["close"] if bar else None

    def _regime(self, day: str) -> str:
        bench = self.data.benchmark
        i = bisect.bisect_right(bench.dates, day)
        if i < REGIME_SMA:
            return "unknown"
        closes = [b["close"] for b in bench.bars[i - REGIME_SMA : i]]
        return "above_200dma" if closes[-1] >= sum(closes) / REGIME_SMA else "below_200dma"

    def _open_risk(self) -> float:
        risk = 0.0
        for pos in self.positions.values():
            stop_charges = self._charges(pos["stop"] * pos["qty"], buy=False)["total"]
            risk += max(0.0, (pos["entry_effective"] - pos["stop"]) * pos["qty"] + stop_charges)
        return risk

    def _valuation(self, day: str) -> dict:
        """Gross market value of open positions and their estimated selling charges.

        Uses ``sum()`` (compensated float summation on Python 3.12+) exactly as the
        pre-Phase-1.1 code did, so gross valuations are bit-for-bit unchanged.
        """
        values = [
            p["qty"] * (self._close(s, day) or p["entry_price"]) for s, p in self.positions.items()
        ]
        costs = (
            sum(self._charges(v, buy=False)["total"] for v in values)
            if self.p.liquidation_valuation == "net"
            else 0
        )
        return {"market_value": sum(values), "est_liquidation_costs": costs}

    def _equity(self, day: str) -> tuple:
        """(equity, gross invested value); equity is net of estimated selling charges
        unless ``liquidation_valuation`` is "gross"."""
        v = self._valuation(day)
        return self.cash + v["market_value"] - v["est_liquidation_costs"], v["market_value"]

    # ------------------------------------------------------------- session steps

    def _exits(self, day: str, session_index: int) -> None:
        for sym in sorted(self.positions):
            pos = self.positions[sym]
            series = self.data.stocks[sym]
            for action in series.actions:
                if (
                    action.get("ex_date") == day
                    and str(action.get("action_type", "")).upper() == "DIVIDEND"
                ):
                    dps = self.eng.dividend_per_share(action.get("purpose", ""))
                    bar = series.by_date.get(day)
                    factor = (bar or {}).get("adj_factor", 1.0) or 1.0
                    amount = round(dps * factor * pos["qty"], 2)
                    if amount > 0:
                        self.cash += amount
                        pos["dividends"] += amount
                        self.events["dividends"] += amount
            bar = series.by_date.get(day)
            if bar is None or pos["entry_session"] >= day:
                continue
            pos["sessions_held"] += 1
            if self.eng.is_locked(bar):
                self.events["exits_locked"] += 1
                continue
            fill = self.eng.exit_fill(bar, stop=pos["stop"], target=pos["target"])
            if fill is not None and fill["basis"] == "stop":
                fill["price"] = self.eng.apply_slippage(fill["price"], self.p.slippage_bps, "SELL")
            if (
                fill is None
                and self.p.max_hold_sessions
                and pos["sessions_held"] >= self.p.max_hold_sessions
            ):
                fill = {"price": float(bar["close"]), "basis": "close", "exit_reason": "time_stop"}
            if fill is not None:
                self._sell(sym, day, fill)

    def _sell(self, sym: str, day: str, fill: dict) -> None:
        pos = self.positions.pop(sym)
        qty = pos["qty"]
        charges = self._charges(fill["price"] * qty, buy=False)
        proceeds = charges["turnover"] - charges["total"]
        self.cash += proceeds
        cost = pos["entry_turnover"] + pos["buy_charges"]
        net = round(proceeds - cost, 2)
        self.trades.append(
            {
                "symbol": sym,
                "sector": pos["sector"],
                "rating_band": pos["rating_band"],
                "composite_score": pos["composite_score"],
                "signal_session": pos["signal_session"],
                "entry_session": pos["entry_session"],
                "exit_session": day,
                "sessions_held": pos["sessions_held"],
                "qty": qty,
                "entry_price": pos["entry_price"],
                "exit_price": fill["price"],
                "stop": pos["stop"],
                "target": pos["target"],
                "exit_reason": fill["exit_reason"],
                "exit_basis": fill["basis"],
                "charges": round(pos["buy_charges"] + charges["total"], 2),
                "gross_pnl": round((fill["price"] - pos["entry_price"]) * qty, 2),
                "net_pnl": net,
                "net_pnl_pct": round(net / cost * 100, 3),
                "planned_risk_inr": pos["planned_risk_inr"],
                "r_multiple": round(net / pos["planned_risk_inr"], 3)
                if pos["planned_risk_inr"]
                else None,
                "dividends": round(pos["dividends"], 2),
                "regime_at_entry": pos["regime"],
                "regime_action_at_signal": pos.get("regime_action"),
                "entry_basis": pos.get("entry_basis"),
                "entry_day_stop_touched": pos.get("entry_day_stop_touched", False),
            }
        )

    def _entries(self, day: str) -> None:
        orders, self.pending = self.pending, []
        for order in orders:
            sym = order["symbol"]
            bar = self.data.stocks[sym].by_date.get(day) if sym in self.data.stocks else None
            if bar is None:
                self.events["orders_suspended"] += 1
                order["sessions_left"] -= 1
                if order["sessions_left"] > 0:
                    self.pending.append(order)
                else:
                    self.events["orders_expired"] += 1
                continue
            fill = None
            if self.eng.is_locked(bar):
                self.events["orders_locked"] += 1
            else:
                fill = self.eng.entry_fill(
                    order["order_type"], bar, limit=order["limit"], trigger=order["trigger"]
                )
                if fill is None and float(bar["open"]) > order["limit"]:
                    self.events["orders_gapped_past_limit"] += 1
                if fill is not None and self._touch_only(order, bar, fill):
                    self.events["limit_touch_only"] += 1
                    if self.p.limit_fill == "trade_through":
                        self.events["limit_touch_skipped"] += 1
                        fill = None
            if fill is None:
                order["sessions_left"] -= 1
                if order["sessions_left"] > 0:
                    self.pending.append(order)
                else:
                    self.events["orders_expired"] += 1
                continue
            price = fill["price"]
            if order["order_type"] in ("STOP", "STOP_LIMIT"):  # as india-paper-ledger
                price = min(
                    self.eng.apply_slippage(price, self.p.slippage_bps, "BUY"), order["limit"]
                )
            qty = order["qty"]
            charges = self._charges(price * qty, buy=True)
            cost = charges["turnover"] + charges["total"]
            if cost > self.cash:
                self.events["orders_rejected_cash"] += 1
                continue
            self.cash -= cost
            self.events["orders_filled"] += 1
            self.positions[sym] = {
                "qty": qty,
                "entry_session": day,
                "signal_session": order["signal_session"],
                "entry_price": price,
                "entry_turnover": charges["turnover"],
                "buy_charges": charges["total"],
                "entry_effective": self.eng.effective_price(
                    price, qty, charges["total"], side="BUY"
                ),
                "stop": order["stop"],
                "target": order["target"],
                "planned_risk_inr": order["risk_inr"],
                "sector": order["sector"],
                "rating_band": order["rating_band"],
                "composite_score": order["composite_score"],
                "sessions_held": 0,
                "dividends": 0.0,
                "regime": self._regime(order["signal_session"]),
                "regime_action": order.get("regime_action"),
                "entry_basis": fill["basis"],
                "entry_day_stop_touched": False,
            }
            self._entry_session_stop(sym, day, bar, order)

    def _touch_only(self, order: dict, bar: dict, fill: dict) -> bool:
        """A LIMIT fill at the limit whose session low did not trade a tick below it."""
        if order["order_type"] != "LIMIT" or fill["basis"] != "limit":
            return False
        factor = order.get("pit_factor", 1.0)
        tick = nse_tick_size(order["limit"] / factor) * factor  # tick as quoted then
        return float(bar["low"]) > order["limit"] - tick + 1e-9

    def _entry_session_stop(self, sym: str, day: str, bar: dict, order: dict) -> None:
        """F2. A buy limit fills at the open or as the price falls through the limit, so a
        session low at or under the (lower) stop came after the fill: exit that session at
        the stop, or at the open when it opened there. Buy-stops fill on the way up and a
        daily bar cannot show whether the low came first, so their stop is checked from
        the next session (the paper ledger's policy) and the touch is recorded."""
        if float(bar["low"]) > order["stop"]:
            return
        pos = self.positions[sym]
        pos["entry_day_stop_touched"] = True
        if order["order_type"] != "LIMIT" or not self.p.entry_session_stop:
            self.events["entry_session_stop_touch_kept"] += 1
            return
        fill = self.eng.exit_fill(bar, stop=order["stop"], target=None)
        fill["price"] = self.eng.apply_slippage(fill["price"], self.p.slippage_bps, "SELL")
        fill["exit_reason"] = "stop_hit_entry_session"
        self.events["entry_session_stops"] += 1
        self._sell(sym, day, fill)

    def _plan(self, day: str, equity: float, invested: float) -> None:
        screen = self.screens.get(day)
        if not screen or screen.get("skipped"):
            return
        self.events["screened_sessions"] += 1
        action = None
        if self.regime is not None:
            # D's posture is known after D's close, before D's plans; exits are unaffected.
            action = self.regime.get(day)
            if action != ENTRY_ALLOWED:
                self.events["regime_blocked_sessions"] += 1
                return
        if self.p.strategy == "momentum-burst":
            self._plan_momentum(day, screen, equity, action)
            return
        held = set(self.positions)
        pending = {o["symbol"] for o in self.pending}
        ledger = {
            "path": None,
            "as_of": day,
            "equity": equity,
            "cash": self.cash,
            "open_risk_pct": self._open_risk() / equity * 100 if equity > 0 else 0.0,
            "positions": [
                {"symbol": s, "market_value": p["qty"] * (self._close(s, day) or p["entry_price"])}
                for s, p in self.positions.items()
            ],
            "held": held,
            "pending": pending,
        }
        params = self.planner.PlanParams(
            account_size=equity, **{k: getattr(self.p, k) for k in SimParams.PLAN_FIELDS}
        )
        as_of = datetime.combine(date.fromisoformat(day), PLAN_TIME, tzinfo=IST)
        with planner_rules(self.planner, self.p):
            plans = self.planner.generate_plans(
                {"metadata": {"as_of": day}, "results": screen["results"]},
                params,
                as_of=as_of,
                ledger=ledger,
                sessions_fn=self._sessions_fn,
            )
        self.events["deferred_heat"] += len(plans["deferred"])
        self.events["constrained"] += len(plans["constrained"])
        pit_factors = screen.get("pit_factors") or {}
        for plan in plans["actionable_orders"]:
            self.events["actionable_plans"] += 1
            order = plan["paper_order"]
            # Plans are priced (and tick-rounded) as quoted on D; convert to today's units.
            f = pit_factors.get(order["symbol"], 1.0)
            qty = order["qty"] if math.isclose(f, 1.0) else math.floor(order["qty"] / f + 1e-9)
            self.pending.append(
                {
                    "symbol": order["symbol"],
                    "qty": qty,
                    "order_type": order["order_type"],
                    "trigger": to_sim_units(order["trigger"], f),
                    "limit": to_sim_units(order["limit"], f),
                    "stop": to_sim_units(order["stop"], f),
                    "target": to_sim_units(order["target"], f),
                    "pit_factor": f,
                    "sessions_left": order["valid_sessions"],
                    "signal_session": day,
                    "risk_inr": plan["trade_plan"]["risk_inr"],
                    "sector": plan.get("sector"),
                    "rating_band": plan.get("rating_band"),
                    "composite_score": plan.get("composite_score"),
                    "regime_action": action,
                }
            )

    def _avg_turnover_cr(self, sym: str, day: str, sessions: int = 20) -> float | None:
        series = self.data.stocks[sym]
        i = bisect.bisect_right(series.dates, day)
        bars = series.bars[max(0, i - sessions) : i]
        values = [
            b["close"] * b["volume"] for b in bars if isinstance(b.get("volume"), (int, float))
        ]
        return sum(values) / len(values) / 1e7 if values else None

    def _plan_momentum(self, day: str, screen: dict, equity: float, action=None) -> None:
        """Next-session buy limits for the day's bursts, sized like the breakout planner.

        Limit and stop are tick-rounded as quoted on D, then converted to today's units.
        """
        p = self.p
        pit_factors = screen.get("pit_factors") or {}
        busy = set(self.positions) | {o["symbol"] for o in self.pending}
        heat = self._open_risk() / equity * 100 if equity > 0 else 0.0
        cash = self.cash
        sector_pct: dict = {}
        sectors = {r["symbol"]: r.get("industry") or "Unknown" for r in self.data.rows}
        for sym, pos in self.positions.items():
            value = pos["qty"] * (self._close(sym, day) or pos["entry_price"])
            sector_pct[sectors.get(sym)] = (
                sector_pct.get(sectors.get(sym), 0.0) + value / equity * 100
            )
        for r in screen["results"]:
            sym = r["symbol"]
            if (
                r["state"] not in MB_SIGNAL_STATES
                or (r.get("setup_score") or 0) < p.min_score
                or any(t in (r.get("soft_failure_tags") or []) for t in MB_SKIP_TAGS)
                or r.get("date") != day
                or sym in busy
            ):
                continue
            ref, stop = r.get("entry_reference"), r.get("stop_reference")
            if not ref or not stop or stop <= 0 or stop >= ref:
                continue
            quoted_limit = round_to_tick(ref * (1 + p.max_chase_pct / 100), "down")
            quoted_stop = round_to_tick(stop, "down")
            if (quoted_limit - quoted_stop) / quoted_limit * 100 > p.max_risk_pct:
                self.events["rejected_risk"] += 1
                continue
            f = pit_factors.get(sym, 1.0)
            limit, stop = to_sim_units(quoted_limit, f), to_sim_units(quoted_stop, f)
            self.events["actionable_plans"] += 1
            sector = sectors.get(sym, "Unknown")
            sizing = self.planner.size_position(
                worst_entry=limit,
                stop_loss=stop,
                account_size=equity,
                risk_pct=p.risk_pct,
                sizing_multiplier=1.0,
                max_position_pct=p.max_position_pct,
                max_sector_pct=p.max_sector_pct,
                current_sector_exposure_pct=sector_pct.get(sector, 0.0),
                avg_turnover_cr=self._avg_turnover_cr(sym, day),
                max_adv_pct=p.max_adv_pct,
                cash_available=cash,
                brokerage=p.brokerage_per_order,
                dp_charge=p.dp_charge,
            )
            if sizing["shares"] == 0:
                self.events["constrained"] += 1
                continue
            risk_pct = sizing["risk_inr"] / equity * 100
            if heat + risk_pct > p.max_portfolio_heat_pct:
                self.events["deferred_heat"] += 1
                continue
            heat += risk_pct
            cash -= sizing["position_value"] + sizing["buy_charges"]
            sector_pct[sector] = (
                sector_pct.get(sector, 0.0) + sizing["position_value"] / equity * 100
            )
            target = None
            if p.target_r_multiple > 0:
                quoted_target = round_to_tick(
                    quoted_limit + p.target_r_multiple * (quoted_limit - quoted_stop), "nearest"
                )
                target = to_sim_units(quoted_target, f)
            busy.add(sym)
            self.pending.append(
                {
                    "symbol": sym,
                    "qty": sizing["shares"],
                    "order_type": "LIMIT",
                    "trigger": None,
                    "limit": limit,
                    "stop": stop,
                    "target": target,
                    "pit_factor": f,
                    "regime_action": action,
                    "sessions_left": p.valid_sessions,
                    "signal_session": day,
                    "risk_inr": sizing["risk_inr"],
                    "sector": sector,
                    "rating_band": r.get("rating"),
                    "composite_score": r.get("setup_score"),
                }
            )

    # ------------------------------------------------------------- run

    def run(self, sessions: list) -> dict:
        for i, day in enumerate(sessions):
            self._exits(day, i)
            self._entries(day)
            v = self._valuation(day)
            equity = self.cash + v["market_value"] - v["est_liquidation_costs"]
            invested = v["market_value"]
            self.curve.append(
                {
                    "date": day,
                    "equity": round(equity, 2),
                    "cash": round(self.cash, 2),
                    "invested": round(invested, 2),
                    "positions": len(self.positions),
                    "open_risk": round(self._open_risk(), 2),
                    "market_value": round(v["market_value"], 2),
                    "est_liquidation_costs": round(v["est_liquidation_costs"], 2),
                }
            )
            if i < len(sessions) - 1:
                self._plan(day, equity, invested)
        last = sessions[-1] if sessions else None
        open_positions = [
            self._open_position(s, p, last) for s, p in sorted(self.positions.items())
        ]
        final = (
            self._valuation(last) if last else {"market_value": 0.0, "est_liquidation_costs": 0.0}
        )
        self.events["dividends"] = round(self.events["dividends"], 2)
        return {
            "params": {k: v for k, v in asdict(self.p).items()},
            "curve": self.curve,
            "trades": self.trades,
            "open_positions": open_positions,
            "final_valuation": {
                "mode": self.p.liquidation_valuation,
                "market_value": round(final["market_value"], 2),
                "est_liquidation_costs": round(final["est_liquidation_costs"], 2),
                "net_liquidation_value": round(
                    final["market_value"] - final["est_liquidation_costs"], 2
                ),
                "cash": round(self.cash, 2),
            },
            "events": self.events,
        }

    def _open_position(self, sym: str, p: dict, last: str | None) -> dict:
        """An open position at the end: gross and net of buy and estimated sell charges."""
        close = self._close(sym, last) if last else None
        market_value = p["qty"] * (close or p["entry_price"])
        sell = self._charges(market_value, buy=False)["total"]
        cost = p["entry_turnover"] + p["buy_charges"]
        return {
            "symbol": sym,
            "qty": p["qty"],
            "entry_session": p["entry_session"],
            "entry_price": p["entry_price"],
            "last_close": close,
            "stop": p["stop"],
            "target": p["target"],
            "unrealized": round(p["qty"] * ((close or p["entry_price"]) - p["entry_price"]), 2),
            "market_value": round(market_value, 2),
            "buy_charges": round(p["buy_charges"], 2),
            "est_sell_charges": round(sell, 2),
            "net_liquidation_value": round(market_value - sell, 2),
            "unrealized_net": round(market_value - sell - cost, 2),
            "regime_action_at_signal": p.get("regime_action"),
            "entry_day_stop_touched": p.get("entry_day_stop_touched", False),
        }


def apply_overrides(params: SimParams, overrides: dict) -> SimParams:
    values = asdict(params)
    for key, value in overrides.items():
        if key not in values:
            raise ValueError(f"unknown parameter {key!r}")
        values[key] = value
    return SimParams(**values)


def parse_sweep(specs: list) -> list:
    """['target_r_multiple=1.5,2,3', 'stop_buffer_pct=0.5,1'] -> list of override dicts."""
    axes = []
    known = asdict(SimParams())
    known.pop("strategy")
    for name in MODE_FIELDS:  # modes are CLI flags, not numeric sweep axes
        known.pop(name)
    for spec in specs or []:
        if "=" not in spec:
            raise ValueError(f"--sweep needs name=v1,v2,..., got {spec!r}")
        name, values = spec.split("=", 1)
        name = name.strip().replace("-", "_")
        if name not in known or name == "capital":
            raise ValueError(f"cannot sweep {name!r}")
        caster: Any = int if name in ("valid_sessions", "max_hold_sessions") else float
        try:
            axes.append((name, [caster(v) for v in values.split(",") if v.strip()]))
        except ValueError as exc:
            raise ValueError(f"--sweep {name}: values must be numbers") from exc
    combos = [{}]
    for name, values in axes:
        combos = [{**c, name: v} for c in combos for v in values]
    return combos
