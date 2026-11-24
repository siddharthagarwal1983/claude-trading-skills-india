"""India paper ledger: paper orders, EOD fills from NSE bhavcopy, rupee accounting.

State lives in one directory (default ``state/india-paper/``)::

    account.json     starting capital and charge/slippage settings
    orders.json      every paper order (OPEN, FILLED, EXPIRED, CANCELLED, REJECTED)
    positions.json   open paper positions with their stop/target exit rules
    journal.jsonl    append-only fills and dividends with rupee cash movements
    theses/          trader-memory-core state (one thesis per entry order)

Each entry order opens a trader-memory-core thesis (IDEA -> ENTRY_READY at
placement, ACTIVE on fill, CLOSED/PARTIALLY_CLOSED on exits, INVALIDATED when
the order expires, is cancelled or rejected). Fill prices handed to the thesis
store are charge-inclusive, so its realized P&L is net of Indian delivery
charges; raw prices and the charge breakdown stay in the journal.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import paper_engine as eng
from _india_data import (
    MUHURAT_SESSIONS,
    BarCache,
    CalendarDependencyMissingError,
    IndiaDataError,
    SymbolNotFoundError,
    default_cache_dir,
    safe_symbol,
)

SKILL_ID = "india-paper-ledger"
SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = Path("state") / "india-paper"
DEFAULT_VALID_SESSIONS = {"CLOSE": 1, "LIMIT": 10, "STOP": 10, "STOP_LIMIT": 10}
THESIS_TYPES = (
    "dividend_income",
    "growth_momentum",
    "mean_reversion",
    "earnings_drift",
    "pivot_breakout",
)
OPEN_THESIS_STATUSES = ("ACTIVE", "PARTIALLY_CLOSED")
CLOSE_UTC = "T10:00:00+00:00"  # 15:30 IST, the NSE close
MUHURAT = {d.isoformat() for d in MUHURAT_SESSIONS}

__all__ = [
    "CalendarDependencyMissingError",
    "IndiaDataError",
    "LedgerError",
    "PaperLedger",
    "SymbolNotFoundError",
    "format_inr",
    "load_thesis_store",
    "render_markdown",
    "write_reports",
]


class LedgerError(RuntimeError):
    """A paper-ledger operation that cannot proceed (state, lock, lifecycle)."""


# --------------------------------------------------------------------------- helpers


def format_inr(value: float | int | None) -> str:
    """Rupees with Indian digit grouping: ₹12,34,567.50."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(float(value)):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{frac}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError(f"{name} must be a positive number")
    if value != value or value in (float("inf"),):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _whole_qty(qty: Any) -> int:
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise ValueError("qty must be a whole number of shares (1 or more)")
    return qty


def load_thesis_store(scripts_dir: Path | None = None):
    """Load trader-memory-core's thesis_store from the sibling skill.

    Override the location with ``INDIA_PAPER_TRADER_MEMORY_DIR`` (the
    trader-memory-core ``scripts`` directory) for a standalone install.
    """
    if scripts_dir is None:
        env = os.environ.get("INDIA_PAPER_TRADER_MEMORY_DIR")
        scripts_dir = (
            Path(env)
            if env
            else Path(__file__).resolve().parents[2] / "trader-memory-core" / "scripts"
        )
    # trader-memory-core needs these; check first so a missing one fails with a
    # requirements hint (CLI exit 2) instead of deep inside its import.
    import jsonschema  # noqa: F401
    import yaml  # noqa: F401

    module_path = Path(scripts_dir) / "thesis_store.py"
    if not module_path.exists():
        raise LedgerError(
            f"trader-memory-core not found at {module_path}. Install the trader-memory-core "
            "skill next to india-paper-ledger or set INDIA_PAPER_TRADER_MEMORY_DIR."
        )
    name = "thesis_store_for_india_paper_ledger"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise LedgerError(f"Could not load trader-memory-core from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError:
        del sys.modules[name]
        raise
    return module


def nse_bars_fn(cache_dir: Path | None = None) -> Callable:
    """Default bars source: unadjusted NSE bhavcopy bars via the shared cache."""
    cache = BarCache(cache_dir or default_cache_dir())

    def fetch(symbol: str, start: date, end: date) -> dict:
        return cache.stock_bars(symbol, start, end, adjusted=False)

    return fetch


# --------------------------------------------------------------------------- ledger


class PaperLedger:
    def __init__(
        self,
        state_dir: Path = DEFAULT_STATE_DIR,
        *,
        bars_fn: Callable | None = None,
        thesis_store: Any = None,
        now_fn: Callable[[], datetime] = _now_utc,
    ):
        self.state_dir = Path(state_dir)
        self.theses_dir = self.state_dir / "theses"
        self._bars_fn = bars_fn
        self.thesis_store = thesis_store or load_thesis_store()
        self._now_fn = now_fn

    # ------------------------------------------------------------------ files

    @property
    def bars_fn(self) -> Callable:
        if self._bars_fn is None:
            self._bars_fn = nse_bars_fn()
        return self._bars_fn

    @property
    def _account_path(self) -> Path:
        return self.state_dir / "account.json"

    @property
    def _orders_path(self) -> Path:
        return self.state_dir / "orders.json"

    @property
    def _positions_path(self) -> Path:
        return self.state_dir / "positions.json"

    @property
    def _journal_path(self) -> Path:
        return self.state_dir / "journal.jsonl"

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.state_dir / ".lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise LedgerError(
                f"{lock} exists: another ledger run is in progress, or a previous run "
                "crashed. Delete the lock file once you are sure no run is active."
            ) from exc
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def account(self) -> dict:
        acct = _read_json(self._account_path, None)
        if acct is None:
            raise LedgerError(
                f"No paper account at {self.state_dir}; run `init --capital <rupees>` first"
            )
        return acct

    def orders(self) -> list:
        return _read_json(self._orders_path, {"orders": []})["orders"]

    def positions(self) -> dict:
        return _read_json(self._positions_path, {"positions": {}})["positions"]

    def journal(self) -> list:
        if not self._journal_path.exists():
            return []
        with open(self._journal_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _save_orders(self, orders: list) -> None:
        _write_json_atomic(self._orders_path, {"orders": orders})

    def _save_positions(self, positions: dict) -> None:
        _write_json_atomic(self._positions_path, {"positions": positions})

    def _append_journal(self, entry: dict) -> None:
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def cash(self) -> float:
        acct = self.account()
        total = acct["starting_capital"] + sum(e.get("cash_delta", 0.0) for e in self.journal())
        return round(total, 2)

    # ------------------------------------------------------------------ account

    def init_account(
        self,
        capital: float,
        *,
        brokerage_per_order: float = 0.0,
        dp_charge: float = eng.DEFAULT_DP_CHARGE,
        slippage_bps: float = 0.0,
        created_on: date | None = None,
    ) -> dict:
        _positive(capital, "capital")
        for value, name in (
            (brokerage_per_order, "brokerage_per_order"),
            (dp_charge, "dp_charge"),
            (slippage_bps, "slippage_bps"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be zero or positive")
        with self._locked():
            if self._account_path.exists():
                raise LedgerError(f"A paper account already exists at {self.state_dir}")
            acct = {
                "schema_version": SCHEMA_VERSION,
                "currency": "INR",
                "exchange": "NSE",
                "product": "CNC (delivery)",
                "starting_capital": float(capital),
                "brokerage_per_order": float(brokerage_per_order),
                "dp_charge": float(dp_charge),
                "slippage_bps": float(slippage_bps),
                "created_on": (created_on or self._now_fn().astimezone(eng.IST).date()).isoformat(),
            }
            _write_json_atomic(self._account_path, acct)
            self._save_orders([])
            self._save_positions({})
        return acct

    # ------------------------------------------------------------------ placing

    def place_entry(
        self,
        symbol: str,
        qty: int,
        *,
        stop: float,
        order_type: str = "CLOSE",
        limit: float | None = None,
        trigger: float | None = None,
        target: float | None = None,
        stop_basis: str = "intraday",
        valid_sessions: int | None = None,
        thesis_type: str = "pivot_breakout",
        thesis: str | None = None,
        thesis_id: str | None = None,
        placed_at: datetime | None = None,
        source_report: str | None = None,
    ) -> dict:
        self.account()
        order_type = str(order_type).upper().replace("-", "_")
        if order_type not in eng.ORDER_TYPES:
            raise ValueError(f"Unknown order type {order_type!r}; use one of {eng.ORDER_TYPES}")
        qty = _whole_qty(qty)
        stop = _positive(stop, "stop")
        if order_type in ("LIMIT", "STOP_LIMIT"):
            if limit is None:
                raise ValueError(f"{order_type} orders need a limit price")
            limit = _positive(limit, "limit")
        if order_type in ("STOP", "STOP_LIMIT"):
            if trigger is None:
                raise ValueError(f"{order_type} orders need a trigger price")
            trigger = _positive(trigger, "trigger")
        if order_type == "STOP_LIMIT" and limit < trigger:
            raise ValueError("STOP_LIMIT limit must be at or above the trigger")
        reference = trigger if trigger is not None else limit
        if reference is not None and reference <= stop:
            raise ValueError("entry price (limit/trigger) must be above the stop")
        if target is not None:
            target = _positive(target, "target")
            if target <= max(stop, reference or 0):
                raise ValueError("target must be above the stop and the entry price")
        if stop_basis not in eng.STOP_BASES:
            raise ValueError(f"stop_basis must be one of {eng.STOP_BASES}")
        if valid_sessions is None:
            valid_sessions = DEFAULT_VALID_SESSIONS[order_type]
        if (
            isinstance(valid_sessions, bool)
            or not isinstance(valid_sessions, int)
            or valid_sessions < 1
        ):
            raise ValueError("valid_sessions must be a whole number of sessions (1 or more)")
        if thesis_type not in THESIS_TYPES:
            raise ValueError(f"thesis_type must be one of {THESIS_TYPES}")

        sym = safe_symbol(symbol)
        ticker = eng.ledger_ticker(sym)
        placed_at = placed_at or self._now_fn()
        if placed_at.tzinfo is None:
            raise ValueError("placed_at must be timezone-aware")
        placed_ist = placed_at.astimezone(eng.IST)
        order_id = f"po_{placed_ist:%Y%m%d}_{ticker.lower()}_{uuid.uuid4().hex[:6]}"
        order = {
            "order_id": order_id,
            "side": "BUY",
            "symbol": sym,
            "ledger_ticker": ticker,
            "qty": qty,
            "order_type": order_type,
            "limit": limit,
            "trigger": trigger,
            "stop": stop,
            "target": target,
            "stop_basis": stop_basis,
            "valid_sessions": valid_sessions,
            "sessions_evaluated": 0,
            "placed_at": placed_ist.isoformat(),
            "first_eligible": eng.first_eligible_date(order_type, placed_at).isoformat(),
            "last_evaluated": None,
            "status": "OPEN",
            "status_reason": None,
            "thesis_id": None,
            "fill": None,
            "source_report": source_report,
        }
        with self._locked():
            ts = self.thesis_store
            ready_at = f"{placed_ist.date().isoformat()}T00:00:00+00:00"
            if thesis_id:
                existing = self._load_thesis(thesis_id)
                if existing["ticker"] != ticker:
                    raise LedgerError(
                        f"{thesis_id} is for ticker {existing['ticker']}, not {ticker} ({sym})"
                    )
                if existing["status"] not in ("IDEA", "ENTRY_READY"):
                    raise LedgerError(
                        f"{thesis_id} has status {existing['status']}; only IDEA or "
                        "ENTRY_READY theses can take a paper entry order"
                    )
                if existing["status"] == "IDEA":
                    ts.transition(
                        self.theses_dir,
                        thesis_id,
                        "ENTRY_READY",
                        f"paper order {order_id} placed",
                    )
                ts.update(
                    self.theses_dir, thesis_id, {"exit": {"stop_loss": stop, "take_profit": target}}
                )
                tid = thesis_id
            else:
                statement = thesis or f"Paper {order_type.lower()} entry in {sym} (NSE)"
                data = {
                    "ticker": ticker,
                    "thesis_type": thesis_type,
                    "thesis_statement": statement,
                    "entry": {"target_price": reference} if reference is not None else {},
                    "exit": {"stop_loss": stop, "take_profit": target},
                    "origin": {
                        "skill": SKILL_ID,
                        "output_file": source_report or str(self._orders_path),
                        "raw_provenance": {
                            "order_id": order_id,
                            "nse_symbol": sym,
                            "exchange": "NSE",
                            "currency": "INR",
                            "order_type": order_type,
                            "qty": qty,
                        },
                    },
                    "_source_date": placed_ist.date().isoformat(),
                }
                tid = ts.register(self.theses_dir, data)
                ts.transition(
                    self.theses_dir,
                    tid,
                    "ENTRY_READY",
                    f"paper order {order_id} placed",
                    event_date=ready_at,
                )
            order["thesis_id"] = tid
            orders = self.orders()
            orders.append(order)
            self._save_orders(orders)
        return order

    def place_exit(
        self, thesis_id: str, qty: int | None = None, *, placed_at: datetime | None = None
    ) -> dict:
        self.account()
        positions = self.positions()
        pos = positions.get(thesis_id)
        if pos is None:
            raise LedgerError(f"{thesis_id} has no open paper position")
        pending = sum(
            o["qty"]
            for o in self.orders()
            if o["side"] == "SELL" and o["status"] == "OPEN" and o["thesis_id"] == thesis_id
        )
        available = pos["qty"] - pending
        qty = available if qty is None else _whole_qty(qty)
        if qty < 1 or qty > available:
            raise ValueError(
                f"qty must be between 1 and {available} (held {pos['qty']}, "
                f"{pending} already on pending sell orders)"
            )
        placed_at = placed_at or self._now_fn()
        placed_ist = placed_at.astimezone(eng.IST)
        order = {
            "order_id": f"po_{placed_ist:%Y%m%d}_{pos['ledger_ticker'].lower()}_{uuid.uuid4().hex[:6]}",
            "side": "SELL",
            "symbol": pos["symbol"],
            "ledger_ticker": pos["ledger_ticker"],
            "qty": qty,
            "order_type": "CLOSE",
            "limit": None,
            "trigger": None,
            "stop": None,
            "target": None,
            "stop_basis": None,
            "valid_sessions": 1,
            "sessions_evaluated": 0,
            "placed_at": placed_ist.isoformat(),
            "first_eligible": eng.first_eligible_date("CLOSE", placed_at).isoformat(),
            "last_evaluated": None,
            "status": "OPEN",
            "status_reason": None,
            "thesis_id": thesis_id,
            "fill": None,
            "source_report": None,
        }
        with self._locked():
            orders = self.orders()
            orders.append(order)
            self._save_orders(orders)
        return order

    def set_exit(
        self,
        thesis_id: str,
        *,
        stop: float | None = None,
        target: float | None = None,
        stop_basis: str | None = None,
    ) -> dict:
        positions = self.positions()
        pos = positions.get(thesis_id)
        if pos is None:
            raise LedgerError(f"{thesis_id} has no open paper position")
        new_stop = pos["stop"] if stop is None else _positive(stop, "stop")
        new_target = pos["target"] if target is None else _positive(target, "target")
        if new_stop is not None and new_target is not None and new_stop >= new_target:
            raise ValueError("stop must be below the target")
        if stop_basis is not None and stop_basis not in eng.STOP_BASES:
            raise ValueError(f"stop_basis must be one of {eng.STOP_BASES}")
        with self._locked():
            self.thesis_store.update(
                self.theses_dir,
                thesis_id,
                {"exit": {"stop_loss": new_stop, "take_profit": new_target}},
            )
            pos.update({"stop": new_stop, "target": new_target})
            if stop_basis is not None:
                pos["stop_basis"] = stop_basis
            self._save_positions(positions)
        return pos

    def cancel(self, order_id: str, reason: str = "cancelled by user") -> dict:
        with self._locked():
            orders = self.orders()
            order = next((o for o in orders if o["order_id"] == order_id), None)
            if order is None:
                raise LedgerError(f"No paper order {order_id}")
            if order["status"] != "OPEN":
                raise LedgerError(f"{order_id} is {order['status']}, not OPEN")
            self._finish_order(order, "CANCELLED", reason)
            self._save_orders(orders)
        return order

    def place_plan(
        self,
        plan: dict,
        *,
        symbols: list | None = None,
        placed_at: datetime | None = None,
        source: str | None = None,
    ) -> dict:
        """Place the actionable paper orders of an india-breakout-trade-planner plan.

        Skips symbols that already have an open paper order or position, and
        any order whose planned session has already opened (re-run the planner).
        """
        if not isinstance(plan, dict) or plan.get("skill") != "india-breakout-trade-planner":
            raise ValueError("not an india-breakout-trade-planner plan (skill field mismatch)")
        placed_at = placed_at or self._now_fn()
        wanted = {safe_symbol(s) for s in symbols} if symbols else None
        busy = {o["symbol"] for o in self.orders() if o["status"] == "OPEN"}
        busy |= {p["symbol"] for p in self.positions().values()}
        placed, skipped = [], []
        for item in plan.get("actionable_orders", []):
            po = item["paper_order"]
            sym = safe_symbol(po["symbol"])
            if wanted is not None and sym not in wanted:
                continue
            if sym in busy:
                skipped.append(
                    {"symbol": sym, "reason": "already has an open paper order or position"}
                )
                continue
            session = item.get("plan_valid_for_session") or plan.get("plan_valid_for_session")
            first = eng.first_eligible_date(po["order_type"], placed_at).isoformat()
            if session and first > session:
                skipped.append(
                    {
                        "symbol": sym,
                        "reason": (
                            f"plan was for the {session} session, which has already opened; "
                            "re-run the planner on fresh screener data"
                        ),
                    }
                )
                continue
            order = self.place_entry(
                sym,
                po["qty"],
                stop=po["stop"],
                order_type=po["order_type"],
                limit=po.get("limit"),
                trigger=po.get("trigger"),
                target=po.get("target"),
                valid_sessions=po.get("valid_sessions"),
                thesis_type=po.get("thesis_type", "pivot_breakout"),
                thesis=po.get("thesis"),
                placed_at=placed_at,
                source_report=source,
            )
            busy.add(sym)
            placed.append(order)
        return {"placed": placed, "skipped": skipped}

    # ------------------------------------------------------------------ thesis bridge

    def _load_thesis(self, thesis_id: str) -> dict:
        try:
            return self.thesis_store.get(self.theses_dir, thesis_id)
        except (FileNotFoundError, ValueError) as exc:
            raise LedgerError(f"No thesis {thesis_id} in {self.theses_dir}") from exc

    def _finish_order(
        self, order: dict, status: str, reason: str, *, event_date: str | None = None
    ) -> None:
        """Close out an order; an unfilled entry invalidates its thesis.

        ``event_date`` is the session close while processing; a user cancel uses
        the wall clock, never earlier than the thesis's last history entry.
        """
        order["status"] = status
        order["status_reason"] = reason
        if order["side"] == "BUY" and order.get("thesis_id"):
            th = self._load_thesis(order["thesis_id"])
            if th["status"] in ("IDEA", "ENTRY_READY"):
                at = event_date or self._now_fn().astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                )
                history = th.get("status_history") or []
                if history and history[-1].get("at"):
                    last = datetime.fromisoformat(history[-1]["at"])
                    if datetime.fromisoformat(at) < last:
                        at = history[-1]["at"]
                self.thesis_store.terminate(
                    self.theses_dir,
                    order["thesis_id"],
                    "INVALIDATED",
                    f"paper order {order['order_id']} {status.lower()}: {reason}",
                    event_date=at,
                )

    # ------------------------------------------------------------------ processing

    def process(self, as_of: date) -> dict:
        """Evaluate every session up to ``as_of`` for open orders and positions."""
        acct = self.account()
        result = {"as_of": as_of.isoformat(), "sessions": [], "events": [], "warnings": []}
        with self._locked():
            orders = self.orders()
            positions = self.positions()
            open_orders = [o for o in orders if o["status"] == "OPEN"]
            starts: dict = {}
            for o in open_orders:
                day = date.fromisoformat(o["first_eligible"])
                if o["last_evaluated"]:
                    day = max(day, date.fromisoformat(o["last_evaluated"]) + timedelta(days=1))
                starts[o["symbol"]] = min(starts.get(o["symbol"], day), day)
            for pos in positions.values():
                day = date.fromisoformat(pos["last_evaluated"]) + timedelta(days=1)
                starts[pos["symbol"]] = min(starts.get(pos["symbol"], day), day)

            data: dict = {}
            for sym, start in sorted(starts.items()):
                if start > as_of:
                    continue
                try:
                    reply = self.bars_fn(sym, start, as_of)
                except SymbolNotFoundError as exc:
                    for o in open_orders:
                        if o["symbol"] == sym and o["status"] == "OPEN":
                            self._finish_order(o, "REJECTED", f"unknown NSE symbol {sym}: {exc}")
                            result["events"].append(
                                {"type": "REJECTED", "order_id": o["order_id"], "symbol": sym}
                            )
                    if any(p["symbol"] == sym for p in positions.values()):
                        result["warnings"].append(f"{sym}: NSE reports no such symbol ({exc})")
                    continue
                except (IndiaDataError, ValueError) as exc:
                    result["warnings"].append(f"{sym}: no NSE data this run ({exc}); retry later")
                    continue
                bars = {
                    b["date"]: b
                    for b in reply.get("bars", [])
                    if start.isoformat() <= b["date"] <= as_of.isoformat()
                    and b["date"] not in MUHURAT
                }
                data[sym] = {"bars": bars, "actions": reply.get("actions", [])}

            sessions = sorted({d for entry in data.values() for d in entry["bars"]})
            journal_ids = {e["event_id"] for e in self.journal()}
            for session in sessions:
                ctx = _SessionContext(self, acct, session, data, journal_ids, result)
                ctx.run_exits(positions)
                ctx.run_sell_orders(orders, positions)
                ctx.run_buy_orders(orders, positions)
                self._save_positions(positions)
                self._save_orders(orders)
                result["sessions"].append(session)
            self._save_positions(positions)
            self._save_orders(orders)
        return result

    # ------------------------------------------------------------------ snapshot

    def snapshot(self, as_of: date) -> dict:
        acct = self.account()
        journal = self.journal()
        warnings: list = []
        rows = []
        for tid, pos in sorted(self.positions().items(), key=lambda kv: kv[1]["symbol"]):
            last = None
            try:
                reply = self.bars_fn(pos["symbol"], as_of - timedelta(days=30), as_of)
                usable = [b for b in reply.get("bars", []) if b["date"] <= as_of.isoformat()]
                last = usable[-1] if usable else None
            except (IndiaDataError, ValueError) as exc:
                warnings.append(f"{pos['symbol']}: no price for valuation ({exc})")
            close = float(last["close"]) if last else pos["entry_price"]
            qty = pos["qty"]
            value = round(close * qty, 2)
            exit_charges = eng.leg_charges(
                value, buy=False, brokerage=acct["brokerage_per_order"], dp_charge=acct["dp_charge"]
            )["total"]
            gross = round((close - pos["entry_price"]) * qty, 2)
            net = round(value - exit_charges - pos["entry_effective"] * qty, 2)
            risk = None
            if pos["stop"] is not None:
                stop_charges = eng.leg_charges(
                    pos["stop"] * qty,
                    buy=False,
                    brokerage=acct["brokerage_per_order"],
                    dp_charge=acct["dp_charge"],
                )["total"]
                risk = round(
                    max(0.0, (pos["entry_effective"] - pos["stop"]) * qty + stop_charges), 2
                )
            rows.append(
                {
                    "thesis_id": tid,
                    "symbol": pos["symbol"],
                    "qty": qty,
                    "entry_session": pos["entry_session"],
                    "entry_price": pos["entry_price"],
                    "entry_effective": pos["entry_effective"],
                    "last_close": close,
                    "price_date": last["date"] if last else None,
                    "market_value": value,
                    "unrealized_gross": gross,
                    "unrealized_net": net,
                    "stop": pos["stop"],
                    "target": pos["target"],
                    "stop_basis": pos["stop_basis"],
                    "distance_to_stop_pct": (
                        round((close - pos["stop"]) / close * 100, 2) if pos["stop"] else None
                    ),
                    "open_risk": risk,
                    "blocked": pos.get("blocked"),
                }
            )
            if pos.get("blocked"):
                warnings.append(f"{pos['symbol']} ({tid}): {pos['blocked']}")
        cash = round(acct["starting_capital"] + sum(e.get("cash_delta", 0.0) for e in journal), 2)
        holdings = round(sum(r["market_value"] for r in rows), 2)
        equity = round(cash + holdings, 2)
        fills = [e for e in journal if e["event"] == "FILL"]
        open_risk = round(sum(r["open_risk"] or 0 for r in rows), 2)
        account = {
            "starting_capital": acct["starting_capital"],
            "cash": cash,
            "holdings_value": holdings,
            "equity": equity,
            "realized_pnl": round(sum(e.get("realized_pnl", 0.0) for e in fills), 2),
            "unrealized_net": round(sum(r["unrealized_net"] for r in rows), 2),
            "charges_paid": round(sum(e["charges"]["total"] for e in fills), 2),
            "dividends": round(sum(e["amount"] for e in journal if e["event"] == "DIVIDEND"), 2),
            "return_pct": round((equity / acct["starting_capital"] - 1) * 100, 2),
            "exposure_pct": round(holdings / equity * 100, 2) if equity > 0 else None,
            "open_risk": open_risk,
            "open_risk_pct": round(open_risk / equity * 100, 2) if equity > 0 else None,
        }
        open_orders = [o for o in self.orders() if o["status"] == "OPEN"]
        recent = [e for e in journal if e.get("session", "") <= as_of.isoformat()][-15:]
        return {
            "skill": SKILL_ID,
            "as_of": as_of.isoformat(),
            "generated_at": self._now_fn().astimezone(eng.IST).isoformat(timespec="seconds"),
            "currency": "INR",
            "fill_policy": (
                "Official NSE end-of-day bars: close orders at the closing price; limit, "
                "stop and exit triggers from the session's open/high/low; stop before "
                "target when one bar touches both."
            ),
            "settings": {k: acct[k] for k in ("brokerage_per_order", "dp_charge", "slippage_bps")},
            "account": account,
            "positions": rows,
            "open_orders": open_orders,
            "recent_activity": recent,
            "warnings": warnings,
        }


class _SessionContext:
    """Applies one session's fills; shared by exits, sell orders and buy orders."""

    def __init__(self, ledger, acct, session, data, journal_ids, result):
        self.ledger = ledger
        self.ts = ledger.thesis_store
        self.acct = acct
        self.session = session
        self.data = data
        self.journal_ids = journal_ids
        self.result = result
        self.close_at = f"{session}{CLOSE_UTC}"

    def _bar(self, symbol):
        entry = self.data.get(symbol)
        return entry["bars"].get(self.session) if entry else None

    def _review_actions(self, symbol, after: str):
        actions = (self.data.get(symbol) or {}).get("actions", [])
        window = [a for a in actions if after < a["ex_date"] <= self.session]
        return eng.split_actions(window)

    def _warn(self, text):
        self.result["warnings"].append(f"{self.session} {text}")

    def _event(self, **kwargs):
        self.result["events"].append({"session": self.session, **kwargs})

    def _journal(self, entry):
        self.ledger._append_journal(entry)
        self.journal_ids.add(entry["event_id"])

    def _charges(self, turnover, *, buy):
        return eng.leg_charges(
            turnover,
            buy=buy,
            brokerage=self.acct["brokerage_per_order"],
            dp_charge=self.acct["dp_charge"],
        )

    def _cash(self):
        return self.ledger.cash()

    # ------------------------------------------------------------ exits

    def run_exits(self, positions):
        for tid in sorted(positions):
            pos = positions.get(tid)
            if pos is None or self.session <= pos["last_evaluated"]:
                continue
            bar = self._bar(pos["symbol"])
            if bar is None:
                continue
            dividends, review = self._review_actions(pos["symbol"], pos["last_evaluated"])
            for action in dividends:
                event_id = f"div:{tid}:{action['ex_date']}"
                if event_id in self.journal_ids:
                    continue
                dps = eng.dividend_per_share(action["purpose"])
                amount = round(dps * pos["qty"], 2)
                self._journal(
                    {
                        "event_id": event_id,
                        "event": "DIVIDEND",
                        "session": action["ex_date"],
                        "thesis_id": tid,
                        "symbol": pos["symbol"],
                        "qty": pos["qty"],
                        "per_share": dps,
                        "amount": amount,
                        "cash_delta": amount,
                        "purpose": action["purpose"],
                        "note": "credited on the ex-date (paid later in practice; taxable)",
                    }
                )
                self._event(type="DIVIDEND", thesis_id=tid, symbol=pos["symbol"], amount=amount)
            pos["last_evaluated"] = self.session
            if review:
                labels = "; ".join(f"{a['purpose']} (ex {a['ex_date']})" for a in review)
                pos["blocked"] = (
                    f"corporate action {labels}: share count and prices changed; exits paused. "
                    "Record the adjusted position in trader-memory-core by hand, then re-create "
                    "the paper position"
                )
                self._warn(f"{pos['symbol']}: {pos['blocked']}")
                continue
            if pos.get("blocked"):
                continue
            th = self.ledger._load_thesis(tid)
            remaining = (th.get("position") or {}).get(
                "shares_remaining", (th.get("position") or {}).get("shares")
            )
            if th["status"] not in OPEN_THESIS_STATUSES or remaining != pos["qty"]:
                pos["blocked"] = (
                    f"thesis status {th['status']} / shares {remaining} does not match the paper "
                    f"position ({pos['qty']} shares); exits paused until reconciled"
                )
                self._warn(f"{pos['symbol']}: {pos['blocked']}")
                continue
            if eng.is_locked(bar):
                self._warn(
                    f"{pos['symbol']}: one-price session (circuit lock?), exits not evaluated"
                )
                continue
            fill = eng.exit_fill(
                bar, stop=pos["stop"], target=pos["target"], stop_basis=pos["stop_basis"]
            )
            if fill is None:
                continue
            price = fill["price"]
            if fill["basis"] == "stop":
                price = eng.apply_slippage(price, self.acct["slippage_bps"], "SELL")
            self._sell(
                positions, tid, pos["qty"], price, fill["basis"], fill["exit_reason"], order_id=None
            )

    # ------------------------------------------------------------ sells

    def _sell(self, positions, tid, qty, price, basis, exit_reason, *, order_id, exit_timing=None):
        """Sell ``qty`` of a position. ``exit_timing="entry_session"`` marks a LIMIT entry's
        same-session stop in the journal and events (absent for every other exit)."""
        pos = positions[tid]
        event_id = f"fill:{order_id}" if order_id else f"exit:{tid}:{self.session}"
        charges = self._charges(price * qty, buy=False)
        eff = eng.effective_price(price, qty, charges["total"], side="SELL")
        realized = round((eff - pos["entry_effective"]) * qty, 2)
        if event_id not in self.journal_ids:
            note = (
                f"paper {exit_reason} {self.session} {basis} ₹{price:.2f} x{qty}; "
                f"charges ₹{charges['total']:.2f}"
                + ("; same session as the entry" if exit_timing else "")
            )
            if qty >= pos["qty"]:
                self.ts.close(
                    self.ledger.theses_dir,
                    tid,
                    exit_reason,
                    eff,
                    self.session,
                    event_date=self.close_at,
                )
            else:
                self.ts.trim(
                    self.ledger.theses_dir,
                    tid,
                    qty,
                    eff,
                    self.session,
                    reason=note,
                    event_date=self.close_at,
                )
            entry = {
                "event_id": event_id,
                "event": "FILL",
                "side": "SELL",
                "session": self.session,
                "order_id": order_id,
                "thesis_id": tid,
                "symbol": pos["symbol"],
                "qty": qty,
                "price": price,
                "basis": basis,
                "exit_reason": exit_reason,
                "charges": charges,
                "effective_price": eff,
                "realized_pnl": realized,
                "cash_delta": round(charges["turnover"] - charges["total"], 2),
            }
            if exit_timing:
                entry["exit_timing"] = exit_timing
            self._journal(entry)
        extra = {"exit_timing": exit_timing} if exit_timing else {}
        self._event(
            type="SELL",
            thesis_id=tid,
            symbol=pos["symbol"],
            qty=qty,
            price=price,
            exit_reason=exit_reason,
            realized_pnl=realized,
            **extra,
        )
        if qty >= pos["qty"]:
            del positions[tid]
        else:
            pos["qty"] -= qty
        return {"price": price, "basis": basis, "session": self.session, "charges": charges}

    def run_sell_orders(self, orders, positions):
        for order in orders:
            if order["side"] != "SELL" or order["status"] != "OPEN":
                continue
            if self.session < order["first_eligible"]:
                continue
            if order["last_evaluated"] and self.session <= order["last_evaluated"]:
                continue
            pos = positions.get(order["thesis_id"])
            if pos is None:
                order["status"] = "CANCELLED"
                order["status_reason"] = "position already closed before this sell order filled"
                self._event(type="CANCELLED", order_id=order["order_id"], symbol=order["symbol"])
                continue
            bar = self._bar(order["symbol"])
            if bar is None:
                continue
            order["last_evaluated"] = self.session
            order["sessions_evaluated"] += 1
            if pos.get("blocked"):
                order["status"] = "CANCELLED"
                order["status_reason"] = f"position blocked: {pos['blocked']}"
                continue
            if eng.is_locked(bar):
                self._warn(f"{order['symbol']}: one-price session (circuit lock?), sell not filled")
            else:
                qty = min(order["qty"], pos["qty"])
                order["fill"] = self._sell(
                    positions,
                    order["thesis_id"],
                    qty,
                    float(bar["close"]),
                    "close",
                    "manual",
                    order_id=order["order_id"],
                )
                order["status"] = "FILLED"
                continue
            if order["sessions_evaluated"] >= order["valid_sessions"]:
                order["status"] = "EXPIRED"
                order["status_reason"] = "day order not filled"

    # ------------------------------------------------------------ buys

    def run_buy_orders(self, orders, positions):
        buys = sorted(
            (o for o in orders if o["side"] == "BUY" and o["status"] == "OPEN"),
            key=lambda o: o["placed_at"],
        )
        for order in buys:
            if self.session < order["first_eligible"]:
                continue
            if order["last_evaluated"] and self.session <= order["last_evaluated"]:
                continue
            bar = self._bar(order["symbol"])
            if bar is None:
                continue
            order["last_evaluated"] = self.session
            order["sessions_evaluated"] += 1
            _, review = self._review_actions(order["symbol"], order["placed_at"][:10])
            if review:
                labels = "; ".join(f"{a['purpose']} (ex {a['ex_date']})" for a in review)
                self.ledger._finish_order(
                    order,
                    "CANCELLED",
                    f"corporate action {labels} changed prices after placement; re-place the order",
                    event_date=self.close_at,
                )
                self._warn(f"{order['symbol']}: order {order['order_id']} cancelled, {labels}")
                self._event(type="CANCELLED", order_id=order["order_id"], symbol=order["symbol"])
                continue
            fill = None
            if eng.is_locked(bar):
                self._warn(f"{order['symbol']}: one-price session (circuit lock?), buy not filled")
            else:
                fill = eng.entry_fill(
                    order["order_type"], bar, limit=order["limit"], trigger=order["trigger"]
                )
            if fill is not None:
                self._buy(order, positions, bar, fill)
                continue
            if order["sessions_evaluated"] >= order["valid_sessions"]:
                self.ledger._finish_order(
                    order,
                    "EXPIRED",
                    f"not filled in {order['valid_sessions']} session(s)",
                    event_date=self.close_at,
                )
                self._event(type="EXPIRED", order_id=order["order_id"], symbol=order["symbol"])

    @staticmethod
    def _entry_session_stop(order, bar) -> bool:
        """True when a LIMIT buy's own session low reached its (intraday-basis) stop.

        A buy limit fills at the open (open <= limit) or as the price falls through the
        limit; the stop is below the limit, so that low came after the fill and the stop
        is executed the same session. Buy-stops fill on the way up (a daily bar cannot
        show whether the low came first), CLOSE orders fill at the close itself, and
        close-basis stops are outside this rule: all keep the next-session policy.
        """
        return (
            order["order_type"] == "LIMIT"
            and order["stop_basis"] == "intraday"
            and float(bar["low"]) <= order["stop"]
        )

    def _buy(self, order, positions, bar, fill):
        price = fill["price"]
        if order["order_type"] in ("STOP", "STOP_LIMIT"):
            price = eng.apply_slippage(price, self.acct["slippage_bps"], "BUY")
            if order["order_type"] == "STOP_LIMIT":
                price = min(price, order["limit"])
        qty = order["qty"]
        charges = self._charges(price * qty, buy=True)
        cost = round(charges["turnover"] + charges["total"], 2)
        event_id = f"fill:{order['order_id']}"
        gap = False
        stop_now = self._entry_session_stop(order, bar)
        if event_id not in self.journal_ids:
            cash = self._cash()
            if cost > cash:
                self.ledger._finish_order(
                    order,
                    "REJECTED",
                    f"insufficient paper cash: need {format_inr(cost)}, have {format_inr(cash)}",
                    event_date=self.close_at,
                )
                self._event(type="REJECTED", order_id=order["order_id"], symbol=order["symbol"])
                return
            eff = eng.effective_price(price, qty, charges["total"], side="BUY")
            gap = eff < order["stop"]
            if gap and not stop_now:
                self._warn(
                    f"{order['symbol']}: filled at {format_inr(price)}, below its stop "
                    f"{format_inr(order['stop'])}; the stop will trigger next session"
                )
            self.ts.open_position(
                self.ledger.theses_dir,
                order["thesis_id"],
                eff,
                self.session,
                reason=(
                    f"paper fill {self.session} {fill['basis']} ₹{price:.2f} x{qty}; "
                    f"charges ₹{charges['total']:.2f}"
                ),
                shares=qty,
                event_date=self.close_at,
                allow_gap_fill=gap,
            )
            self._journal(
                {
                    "event_id": event_id,
                    "event": "FILL",
                    "side": "BUY",
                    "session": self.session,
                    "order_id": order["order_id"],
                    "thesis_id": order["thesis_id"],
                    "symbol": order["symbol"],
                    "qty": qty,
                    "price": price,
                    "basis": fill["basis"],
                    "charges": charges,
                    "effective_price": eff,
                    "cash_delta": -cost,
                }
            )
        else:
            eff = eng.effective_price(price, qty, charges["total"], side="BUY")
        positions[order["thesis_id"]] = {
            "thesis_id": order["thesis_id"],
            "order_id": order["order_id"],
            "symbol": order["symbol"],
            "ledger_ticker": order["ledger_ticker"],
            "qty": qty,
            "entry_session": self.session,
            "entry_price": price,
            "entry_effective": eff,
            "stop": order["stop"],
            "target": order["target"],
            "stop_basis": order["stop_basis"],
            "last_evaluated": self.session,
            "blocked": None,
        }
        order["status"] = "FILLED"
        order["fill"] = {
            "session": self.session,
            "price": price,
            "basis": fill["basis"],
            "charges": charges,
            "effective_price": eff,
        }
        self._event(
            type="BUY", order_id=order["order_id"], symbol=order["symbol"], qty=qty, price=price
        )
        if stop_now:
            # Same exit rule as later sessions (stop only; no target or other exit on the
            # entry session): the open when it was already at/under the stop, else the stop.
            exit_fill = eng.exit_fill(bar, stop=order["stop"], target=None, stop_basis="intraday")
            exit_price = eng.apply_slippage(exit_fill["price"], self.acct["slippage_bps"], "SELL")
            self._warn(
                f"{order['symbol']}: LIMIT filled at {format_inr(price)} and the session low "
                f"{format_inr(float(bar['low']))} reached the stop {format_inr(order['stop'])}; "
                f"a buy limit fills before a lower low, so the stop exited the same session at "
                f"{format_inr(exit_price)}"
            )
            self._sell(
                positions,
                order["thesis_id"],
                qty,
                exit_price,
                exit_fill["basis"],
                exit_fill["exit_reason"],
                order_id=None,
                exit_timing="entry_session",
            )
            return
        same_day = eng.exit_fill(
            bar, stop=order["stop"], target=order["target"], stop_basis=order["stop_basis"]
        )
        if same_day is not None and not gap:
            self._warn(
                f"{order['symbol']}: the {same_day['exit_reason'].replace('_', ' ')} level was "
                "also touched on the entry day; daily bars cannot show the order of events, "
                "so exits start next session"
            )


# --------------------------------------------------------------------------- reports


def _pct(value):
    return "—" if value is None else f"{value:.2f}%"


def render_markdown(snap: dict) -> str:
    a = snap["account"]
    lines = [
        f"# India Paper Ledger — {snap['as_of']}",
        "",
        f"Generated {snap['generated_at']} · NSE delivery (CNC) · INR · paper trading only",
        "",
        "## Account",
        "",
        "| Item | Value |",
        "|------|-------|",
        f"| Starting capital | {format_inr(a['starting_capital'])} |",
        f"| Cash | {format_inr(a['cash'])} |",
        f"| Holdings (at last close) | {format_inr(a['holdings_value'])} |",
        f"| Equity | {format_inr(a['equity'])} |",
        f"| Return | {_pct(a['return_pct'])} |",
        f"| Realized P&L (net of charges) | {format_inr(a['realized_pnl'])} |",
        f"| Unrealized P&L (net of exit charges) | {format_inr(a['unrealized_net'])} |",
        f"| Charges paid | {format_inr(a['charges_paid'])} |",
        f"| Dividends | {format_inr(a['dividends'])} |",
        f"| Exposure | {_pct(a['exposure_pct'])} |",
        f"| Open risk to stops | {format_inr(a['open_risk'])} ({_pct(a['open_risk_pct'])}) |",
        "",
        "## Positions",
        "",
    ]
    if snap["positions"]:
        lines += [
            "| Symbol | Qty | Entry | Last close | Value | Unrealized (net) | Stop | Target | To stop | Risk |",
            "|--------|----:|------:|-----------:|------:|-----------------:|-----:|-------:|--------:|-----:|",
        ]
        for p in snap["positions"]:
            flag = " ⚠" if p["blocked"] else ""
            lines.append(
                f"| {p['symbol']}{flag} | {p['qty']} | {format_inr(p['entry_price'])} | "
                f"{format_inr(p['last_close'])} | {format_inr(p['market_value'])} | "
                f"{format_inr(p['unrealized_net'])} | {format_inr(p['stop'])} | "
                f"{format_inr(p['target'])} | {_pct(p['distance_to_stop_pct'])} | "
                f"{format_inr(p['open_risk'])} |"
            )
    else:
        lines.append("No open positions.")
    lines += ["", "## Open orders", ""]
    if snap["open_orders"]:
        lines += [
            "| Order | Side | Symbol | Qty | Type | Limit | Trigger | Stop | Sessions |",
            "|-------|------|--------|----:|------|------:|--------:|-----:|---------:|",
        ]
        for o in snap["open_orders"]:
            lines.append(
                f"| {o['order_id']} | {o['side']} | {o['symbol']} | {o['qty']} | {o['order_type']} | "
                f"{format_inr(o['limit'])} | {format_inr(o['trigger'])} | {format_inr(o['stop'])} | "
                f"{o['sessions_evaluated']}/{o['valid_sessions']} |"
            )
    else:
        lines.append("No open orders.")
    lines += ["", "## Recent activity", ""]
    if snap["recent_activity"]:
        lines += [
            "| Session | Event | Symbol | Qty | Price | Charges | Cash | Realized |",
            "|---------|-------|--------|----:|------:|--------:|-----:|---------:|",
        ]
        for e in snap["recent_activity"]:
            label = e["event"] if e["event"] != "FILL" else e["side"]
            if e.get("exit_reason"):
                label += f" ({e['exit_reason']})"
            price = e.get("price", e.get("per_share"))
            charges = e["charges"]["total"] if e.get("charges") else None
            lines.append(
                f"| {e['session']} | {label} | {e['symbol']} | {e['qty']} | {format_inr(price)} | "
                f"{format_inr(charges)} | {format_inr(e['cash_delta'])} | "
                f"{format_inr(e.get('realized_pnl'))} |"
            )
    else:
        lines.append("No fills yet.")
    if snap["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in snap["warnings"]]
    s = snap["settings"]
    lines += [
        "",
        "## Assumptions",
        "",
        f"- Fills: {snap['fill_policy']}",
        f"- Charges: statutory delivery rates (STT 0.1% per leg, NSE 0.00297%, SEBI ₹10/crore, "
        f"stamp 0.015% on buys, GST 18%), brokerage {format_inr(s['brokerage_per_order'])} per "
        f"order, DP {format_inr(s['dp_charge'])} per sell. Slippage {s['slippage_bps']} bps on "
        "stop fills.",
        "- Paper results ignore market impact, partial fills and taxes (STCG/LTCG). "
        "Not investment advice.",
        "",
    ]
    return "\n".join(lines)


def write_reports(snap: dict, output_dir: Path) -> tuple:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"india_paper_ledger_{snap['as_of']}"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    _write_json_atomic(json_path, snap)
    md_path.write_text(render_markdown(snap), encoding="utf-8")
    return json_path, md_path
