#!/usr/bin/env python3
"""Paper-trade NSE delivery swing trades in rupees with end-of-day fills.

Subcommands: init, buy, sell, set-exit, cancel, process, status, orders.
Fills come from official NSE end-of-day bars (free NSE bhavcopy MCP over HTTP,
cached in .cache/india-data/). Positions are journaled as trader-memory-core
theses under <state-dir>/theses/.

Exit codes: 0 success, 1 invalid input or ledger state, 2 missing dependency.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

REQUIREMENTS_HINT = (
    "Install the runtime dependencies: pip install -r "
    "skills/india-paper-ledger/requirements.txt (or `uv sync --extra dev` in this repository)."
)


def finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"{value!r} is not a finite number")
    return number


def whole_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from exc
    return number


def iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "--placed-at needs a timezone, e.g. 2026-11-23T14:30+05:30"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="India paper ledger: NSE delivery paper trades with EOD fills in rupees."
    )
    parser.add_argument(
        "--state-dir",
        default="state/india-paper",
        help="Ledger state directory (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-dir", default=None, help="NSE bar cache (default: .cache/india-data in the repo)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a paper account")
    init.add_argument(
        "--capital", type=finite_float, required=True, help="Starting capital in rupees"
    )
    init.add_argument("--brokerage-per-order", type=finite_float, default=0.0)
    init.add_argument(
        "--dp-charge", type=finite_float, default=None, help="Per sell (default 15.93)"
    )
    init.add_argument(
        "--slippage-bps", type=finite_float, default=0.0, help="Applied to stop fills"
    )

    buy = sub.add_parser("buy", help="Place a paper entry order (opens a thesis)")
    buy.add_argument("--symbol", required=True, help="NSE symbol, e.g. RELIANCE or M&M")
    buy.add_argument("--qty", type=whole_number, help="Shares (or use --sizer-report)")
    buy.add_argument("--sizer-report", help="india-position-sizer JSON (shares mode)")
    buy.add_argument(
        "--type",
        default="close",
        choices=["close", "limit", "stop", "stop-limit"],
        help="close = next eligible closing price (default)",
    )
    buy.add_argument("--limit", type=finite_float)
    buy.add_argument("--trigger", type=finite_float)
    buy.add_argument(
        "--stop", type=finite_float, help="Protective stop (required unless in report)"
    )
    buy.add_argument("--target", type=finite_float)
    buy.add_argument("--stop-basis", default="intraday", choices=["intraday", "close"])
    buy.add_argument("--valid-sessions", type=whole_number)
    buy.add_argument(
        "--thesis-type",
        default="pivot_breakout",
        choices=[
            "dividend_income",
            "growth_momentum",
            "mean_reversion",
            "earnings_drift",
            "pivot_breakout",
        ],
    )
    buy.add_argument("--thesis", help="Thesis statement")
    buy.add_argument("--thesis-id", help="Attach to an existing IDEA/ENTRY_READY thesis")
    buy.add_argument("--placed-at", type=iso_datetime, help="Backdate placement (ISO, with zone)")

    sell = sub.add_parser("sell", help="Sell at the next eligible close (all or --qty)")
    sell.add_argument("--thesis-id", required=True)
    sell.add_argument("--qty", type=whole_number)
    sell.add_argument("--placed-at", type=iso_datetime)

    set_exit = sub.add_parser("set-exit", help="Move the stop/target of an open position")
    set_exit.add_argument("--thesis-id", required=True)
    set_exit.add_argument("--stop", type=finite_float)
    set_exit.add_argument("--target", type=finite_float)
    set_exit.add_argument("--stop-basis", choices=["intraday", "close"])

    cancel = sub.add_parser("cancel", help="Cancel an open order")
    cancel.add_argument("--order-id", required=True)
    cancel.add_argument("--reason", default="cancelled by user")

    for name, text in (
        ("process", "Fill orders and exits for every session up to --as-of, then report"),
        ("status", "Mark positions to the latest close and write the report"),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument(
            "--as-of", type=date.fromisoformat, help="Default: latest completed session"
        )
        cmd.add_argument("--output-dir", default="reports/", help="Default: %(default)s")
        cmd.add_argument("--no-report", action="store_true", help="Skip writing report files")

    orders = sub.add_parser("orders", help="List paper orders as JSON")
    orders.add_argument("--status", choices=["OPEN", "FILLED", "EXPIRED", "CANCELLED", "REJECTED"])
    return parser


def load_sizer_report(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    if report.get("mode") != "shares":
        raise ValueError(f"{path}: india-position-sizer report must be in shares mode")
    params = report.get("parameters") or {}
    qty = report.get("final_recommended_shares")
    if not isinstance(qty, int) or qty < 1:
        raise ValueError(f"{path}: final_recommended_shares is {qty!r}; nothing to buy")
    return {"qty": qty, "entry": params.get("entry_price"), "stop": params.get("stop_price")}


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def run(args, ledger_factory) -> int:
    import paper_ledger as pl

    ledger = ledger_factory(args)
    if args.command == "init":
        kwargs = {
            "brokerage_per_order": args.brokerage_per_order,
            "slippage_bps": args.slippage_bps,
        }
        if args.dp_charge is not None:
            kwargs["dp_charge"] = args.dp_charge
        _print(ledger.init_account(args.capital, **kwargs))
        return 0
    if args.command == "buy":
        qty, stop, limit, trigger = args.qty, args.stop, args.limit, args.trigger
        order_type = args.type.upper().replace("-", "_")
        if args.sizer_report:
            report = load_sizer_report(args.sizer_report)
            if qty is not None and qty != report["qty"]:
                raise ValueError(f"--qty {qty} differs from the sizer report's {report['qty']}")
            if stop is not None and abs(stop - report["stop"]) > 0.01:
                raise ValueError(f"--stop {stop} differs from the sizer report's {report['stop']}")
            qty, stop = report["qty"], report["stop"]
            if order_type in ("LIMIT", "STOP_LIMIT") and limit is None:
                limit = report["entry"]
            if order_type in ("STOP", "STOP_LIMIT") and trigger is None:
                trigger = report["entry"]
        if qty is None:
            raise ValueError("give --qty or --sizer-report")
        if stop is None:
            raise ValueError("give --stop (every paper entry needs a protective stop)")
        order = ledger.place_entry(
            args.symbol,
            qty,
            stop=stop,
            order_type=order_type,
            limit=limit,
            trigger=trigger,
            target=args.target,
            stop_basis=args.stop_basis,
            valid_sessions=args.valid_sessions,
            thesis_type=args.thesis_type,
            thesis=args.thesis,
            thesis_id=args.thesis_id,
            placed_at=args.placed_at,
            source_report=args.sizer_report,
        )
        _print(order)
        return 0
    if args.command == "sell":
        _print(ledger.place_exit(args.thesis_id, args.qty, placed_at=args.placed_at))
        return 0
    if args.command == "set-exit":
        _print(
            ledger.set_exit(
                args.thesis_id, stop=args.stop, target=args.target, stop_basis=args.stop_basis
            )
        )
        return 0
    if args.command == "cancel":
        _print(ledger.cancel(args.order_id, args.reason))
        return 0
    if args.command == "orders":
        _print([o for o in ledger.orders() if args.status in (None, o["status"])])
        return 0

    as_of = args.as_of
    if as_of is None:
        from _india_data import latest_completed_session

        as_of = latest_completed_session(datetime.now(timezone.utc))
    if args.command == "process":
        result = ledger.process(as_of)
        print(
            f"Processed {len(result['sessions'])} session(s) up to {as_of}: "
            f"{len(result['events'])} event(s)"
        )
        for event in result["events"]:
            print("  " + json.dumps(event, ensure_ascii=False))
        for warning in result["warnings"]:
            print(f"  WARNING: {warning}", file=sys.stderr)
    snap = ledger.snapshot(as_of)
    a = snap["account"]
    print(
        f"Equity {pl.format_inr(a['equity'])} (cash {pl.format_inr(a['cash'])}, holdings "
        f"{pl.format_inr(a['holdings_value'])}), return {a['return_pct']:.2f}%, "
        f"{len(snap['positions'])} position(s), {len(snap['open_orders'])} open order(s)"
    )
    if not args.no_report:
        json_path, md_path = pl.write_reports(snap, Path(args.output_dir))
        print(f"Report: {md_path}\nJSON:   {json_path}")
    return 0


def default_factory(args):
    import paper_ledger as pl

    bars_fn = None
    if args.cache_dir:
        bars_fn = pl.nse_bars_fn(Path(args.cache_dir))
    return pl.PaperLedger(Path(args.state_dir), bars_fn=bars_fn)


def main(argv: list | None = None, *, ledger_factory=default_factory) -> int:
    args = build_parser().parse_args(argv)
    try:
        import paper_ledger as pl
    except ImportError as exc:
        print(f"Error: missing dependency ({exc}). {REQUIREMENTS_HINT}", file=sys.stderr)
        return 2
    try:
        return run(args, ledger_factory)
    except ImportError as exc:
        print(f"Error: missing dependency ({exc}). {REQUIREMENTS_HINT}", file=sys.stderr)
        return 2
    except pl.CalendarDependencyMissingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (
        pl.LedgerError,
        pl.IndiaDataError,
        ValueError,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
