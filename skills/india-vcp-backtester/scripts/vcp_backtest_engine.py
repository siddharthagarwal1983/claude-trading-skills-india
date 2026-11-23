"""Walk-forward replay of the India VCP breakout workflow on NSE end-of-day bars.

Every NSE session D of the test window:

1. **Exits** — positions entered before D are checked against D's bar with the
   paper ledger's exit rule (stop before target when one bar touches both;
   stop fills get the stop slippage). Dividends with ex-date D are credited.
2. **Entries** — stop-limit orders planned the evening before are filled with
   the paper ledger's entry rule (gap past the limit = no fill, one-price
   circuit sessions do not fill, slippage capped at the limit), if cash allows.
3. **Mark to market** at D's close.
4. **Plan** — D's screen (india-vcp-screener's own funnel on bars up to D) goes
   to india-breakout-trade-planner's ``generate_plans`` with the simulated
   account as the ledger; its actionable orders are queued for the next session.

Nothing after D is visible when D is screened or planned. Bars are adjusted for
splits and bonuses as of the data download, so share counts are in today's
units; rupee amounts are unaffected.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from _india_data import IST

SCREEN_SCHEMA = 1
HISTORY_CALENDAR_DAYS = 430  # same window as the live screener
PLAN_TIME = time(20, 0)  # plans are made after the bhavcopy, as in live use
REGIME_SMA = 200


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


def screen_day(day: str, data: MarketData, screener, args: argparse.Namespace, top: int) -> dict:
    """india-vcp-screener's funnel as of ``day`` using only bars dated on or before it."""
    start = _window_start(day)
    outcomes: dict = {}
    histories: dict = {}
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
        "funnel": {
            "universe": len(data.rows),
            "evaluated": len(histories),
            "pre_filter_passed": len(pre),
            "trend_template_passed": len(trend),
            "vcp_candidates": len(results),
        },
    }


def screen_key(universe: str, benchmark: str, args: argparse.Namespace, top: int, data) -> str:
    """Cache key: screener settings plus every split/bonus factor in the data."""
    settings = {
        k: v
        for k, v in sorted(vars(args).items())
        if k not in ("output_dir", "cache_dir", "symbols")
    }
    factors = sorted(
        (sym, a.get("ex_date"), a.get("factor"))
        for sym, s in data.stocks.items()
        for a in s.actions
        if a.get("factor") not in (None, 1, 1.0)
    )
    blob = json.dumps(
        {
            "schema": SCREEN_SCHEMA,
            "universe": universe,
            "symbols": sorted(data.stocks),
            "benchmark": benchmark,
            "top": top,
            "settings": settings,
            "factors": factors,
        },
        sort_keys=True,
        default=str,
    )
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
) -> dict:
    todo = [d for d in sessions if d not in cache.days]
    for n, day in enumerate(todo, 1):
        cache.add(screen_day(day, data, screener, args, top))
        if n % 20 == 0 or n == len(todo):
            progress(f"  screened {n}/{len(todo)} sessions (last {day})")
    return {d: cache.days[d] for d in sessions}


# --------------------------------------------------------------------------- simulation


@dataclass
class SimParams:
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
    # Research-only planner rules (live defaults: 8% cap, score 70+, nothing below 70 sized).
    max_risk_pct: float = 8.0
    min_score: float = 70.0
    developing_multiplier: float = 0.5

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
    if not 0 < p.max_risk_pct <= 30:
        raise ValueError("max_risk_pct must be above 0 and at most 30")
    if not 60 <= p.min_score <= 100:
        raise ValueError("min_score must be between 60 and 100")
    if not 0 <= p.developing_multiplier <= 1.75:
        raise ValueError("developing_multiplier must be between 0 and 1.75")
    if p.max_hold_sessions is not None and p.max_hold_sessions < 1:
        raise ValueError("max_hold_sessions must be at least 1")


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
    def __init__(self, data: MarketData, screens: dict, params: SimParams, planner, paper):
        validate_params(params)
        self.data = data
        self.screens = screens
        self.p = params
        self.planner = planner
        self.eng = paper
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
            "orders_rejected_cash": 0,
            "orders_locked": 0,
            "orders_suspended": 0,
            "exits_locked": 0,
            "dividends": 0.0,
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

    def _equity(self, day: str) -> tuple:
        value = sum(
            p["qty"] * (self._close(s, day) or p["entry_price"]) for s, p in self.positions.items()
        )
        return self.cash + value, value

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
                    "STOP_LIMIT", bar, limit=order["limit"], trigger=order["trigger"]
                )
                if fill is None and float(bar["open"]) > order["limit"]:
                    self.events["orders_gapped_past_limit"] += 1
            if fill is None:
                order["sessions_left"] -= 1
                if order["sessions_left"] > 0:
                    self.pending.append(order)
                else:
                    self.events["orders_expired"] += 1
                continue
            price = min(
                self.eng.apply_slippage(fill["price"], self.p.slippage_bps, "BUY"), order["limit"]
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
            }

    def _plan(self, day: str, equity: float, invested: float) -> None:
        screen = self.screens.get(day)
        if not screen or screen.get("skipped"):
            return
        self.events["screened_sessions"] += 1
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
        for plan in plans["actionable_orders"]:
            self.events["actionable_plans"] += 1
            order = plan["paper_order"]
            self.pending.append(
                {
                    "symbol": order["symbol"],
                    "qty": order["qty"],
                    "trigger": order["trigger"],
                    "limit": order["limit"],
                    "stop": order["stop"],
                    "target": order["target"],
                    "sessions_left": order["valid_sessions"],
                    "signal_session": day,
                    "risk_inr": plan["trade_plan"]["risk_inr"],
                    "sector": plan.get("sector"),
                    "rating_band": plan.get("rating_band"),
                    "composite_score": plan.get("composite_score"),
                }
            )

    # ------------------------------------------------------------- run

    def run(self, sessions: list) -> dict:
        for i, day in enumerate(sessions):
            self._exits(day, i)
            self._entries(day)
            equity, invested = self._equity(day)
            self.curve.append(
                {
                    "date": day,
                    "equity": round(equity, 2),
                    "cash": round(self.cash, 2),
                    "invested": round(invested, 2),
                    "positions": len(self.positions),
                    "open_risk": round(self._open_risk(), 2),
                }
            )
            if i < len(sessions) - 1:
                self._plan(day, equity, invested)
        last = sessions[-1] if sessions else None
        open_positions = [
            {
                "symbol": s,
                "qty": p["qty"],
                "entry_session": p["entry_session"],
                "entry_price": p["entry_price"],
                "last_close": self._close(s, last),
                "stop": p["stop"],
                "target": p["target"],
                "unrealized": round(
                    p["qty"] * ((self._close(s, last) or p["entry_price"]) - p["entry_price"]), 2
                ),
            }
            for s, p in sorted(self.positions.items())
        ]
        self.events["dividends"] = round(self.events["dividends"], 2)
        return {
            "params": {k: v for k, v in asdict(self.p).items()},
            "curve": self.curve,
            "trades": self.trades,
            "open_positions": open_positions,
            "events": self.events,
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
