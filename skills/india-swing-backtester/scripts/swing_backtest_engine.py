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

from _india_data import IST, round_to_tick

SCREEN_SCHEMA = 1
HISTORY_CALENDAR_DAYS = 430  # same window as the live screener
PLAN_TIME = time(20, 0)  # plans are made after the bhavcopy, as in live use
REGIME_SMA = 200
STRATEGIES = ("vcp", "momentum-burst")
MB_SIGNAL_STATES = ("ACTIONABLE_DAY1", "MANUAL_REVIEW")
MB_SKIP_TAGS = ("possible_upper_circuit",)  # the screener sends these to manual review


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


def screen_day_momentum(day: str, data: MarketData, mb, args: argparse.Namespace) -> dict:
    """india-stockbee-momentum-burst-screener's analysis as of ``day`` (bars up to it)."""
    args._expected_session = date.fromisoformat(day)
    start = (
        date.fromisoformat(day) - timedelta(days=int(args.history_days * 1.6) + 30)
    ).isoformat()
    results = []
    for row in data.rows:
        series = data.stocks.get(row["symbol"])
        if series is None:
            continue
        bars = mb.normalize_bars(series.window(start, day))
        if bars:
            results.append(mb.analyze_symbol(row["symbol"], bars, args))
    states: dict = {}
    for r in results:
        states[r["state"]] = states.get(r["state"], 0) + 1
    kept = [r for r in mb.sort_results(results) if r["state"] != "REJECTED"]
    return {
        "date": day,
        "results": [{k: r.get(k) for k in MB_FIELDS} for r in kept],
        "funnel": {"universe": len(data.rows), "analyzed": len(results), "states": states},
    }


def screen_key(
    universe: str, benchmark: str, args: argparse.Namespace, top: int, data, strategy: str = "vcp"
) -> str:
    """Cache key: strategy, screener settings plus every split/bonus factor in the data."""
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
    blob = json.dumps(
        {
            "schema": SCREEN_SCHEMA,
            "strategy": strategy,
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
    strategy: str = "vcp",
) -> dict:
    todo = [d for d in sessions if d not in cache.days]
    for n, day in enumerate(todo, 1):
        if strategy == "vcp":
            cache.add(screen_day(day, data, screener, args, top))
        else:
            cache.add(screen_day_momentum(day, data, screener, args))
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
            "rejected_risk": 0,
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
                    order["order_type"], bar, limit=order["limit"], trigger=order["trigger"]
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
            }

    def _plan(self, day: str, equity: float, invested: float) -> None:
        screen = self.screens.get(day)
        if not screen or screen.get("skipped"):
            return
        self.events["screened_sessions"] += 1
        if self.p.strategy == "momentum-burst":
            self._plan_momentum(day, screen, equity)
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
        for plan in plans["actionable_orders"]:
            self.events["actionable_plans"] += 1
            order = plan["paper_order"]
            self.pending.append(
                {
                    "symbol": order["symbol"],
                    "qty": order["qty"],
                    "order_type": order["order_type"],
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

    def _avg_turnover_cr(self, sym: str, day: str, sessions: int = 20) -> float | None:
        series = self.data.stocks[sym]
        i = bisect.bisect_right(series.dates, day)
        bars = series.bars[max(0, i - sessions) : i]
        values = [
            b["close"] * b["volume"] for b in bars if isinstance(b.get("volume"), (int, float))
        ]
        return sum(values) / len(values) / 1e7 if values else None

    def _plan_momentum(self, day: str, screen: dict, equity: float) -> None:
        """Next-session buy limits for the day's bursts, sized like the breakout planner."""
        p = self.p
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
            limit = round_to_tick(ref * (1 + p.max_chase_pct / 100), "down")
            stop = round_to_tick(stop, "down")
            if (limit - stop) / limit * 100 > p.max_risk_pct:
                self.events["rejected_risk"] += 1
                continue
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
                target = round_to_tick(limit + p.target_r_multiple * (limit - stop), "nearest")
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
    known.pop("strategy")
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
