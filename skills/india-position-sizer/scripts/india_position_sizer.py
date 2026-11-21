"""Position sizer for long NSE cash-market (delivery / CNC) trades.

India-specific behaviour on top of classic fixed-fractional and ATR sizing:

* Rupees throughout, reported with Indian digit grouping (₹12,34,567.00).
* Whole shares only (no fractional shares on NSE); optional SME lot size.
* ATR-derived stops are rounded *down* to the exchange tick.
* Statutory delivery charges (STT, exchange transaction charge, SEBI fee,
  stamp duty, GST, DP charge) are counted inside the risk budget, so a stop-out
  loses at most the planned percentage *after* costs. STT alone is 0.1% on
  each leg, which is large relative to a 1% risk budget on tight stops.

Kelly mode returns a risk budget only (no entry price needed).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

PAISA = Decimal("0.01")

# Statutory rates for equity delivery on NSE. Verify against your broker's
# contract note; brokers differ on brokerage and the DP charge.
CHARGES = {
    "stt_rate": Decimal("0.001"),  # 0.1% on buy and on sell (delivery)
    "exchange_txn_rate": Decimal("0.0000297"),  # NSE cash, 0.00297% per leg
    "sebi_fee_rate": Decimal("0.000001"),  # ₹10 per crore per leg
    "stamp_duty_buy_rate": Decimal("0.00015"),  # 0.015% on the buy leg only
    "gst_rate": Decimal("0.18"),  # on brokerage + exchange charge + SEBI fee
}
DEFAULT_DP_CHARGE = 15.93  # per scrip per sell day, incl. GST (typical discount broker)
DEFAULT_TICK_SIZE = 0.05
CHARGES_NOTE = (
    "Statutory delivery rates (STT 0.1% per leg, NSE 0.00297%, SEBI ₹10/crore, stamp 0.015% "
    "on buy, GST 18%) plus a broker-specific DP charge. Verify against your contract note."
)

FLOAT_FIELDS = (
    "account_size",
    "entry_price",
    "stop_price",
    "risk_pct",
    "atr",
    "atr_multiplier",
    "win_rate",
    "avg_win",
    "avg_loss",
    "max_position_pct",
    "max_sector_pct",
    "current_sector_exposure",
    "brokerage_per_order",
    "dp_charge",
    "tick_size",
)


@dataclass
class SizingParameters:
    account_size: float
    entry_price: float | None = None
    stop_price: float | None = None
    risk_pct: float | None = None
    atr: float | None = None
    atr_multiplier: float = 2.0
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    max_position_pct: float | None = None
    max_sector_pct: float | None = None
    sector: str | None = None
    current_sector_exposure: float = 0.0
    include_costs: bool = True
    brokerage_per_order: float = 0.0
    dp_charge: float = DEFAULT_DP_CHARGE
    tick_size: float = DEFAULT_TICK_SIZE
    lot_size: int = 1


def _d(value: float | int) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(PAISA, rounding=ROUND_HALF_UP))


def format_inr(value: float | int) -> str:
    """Format rupees with Indian digit grouping: ₹12,34,567.50."""
    amount = _d(value).quantize(PAISA, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = f"{abs(amount):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{fraction}"


def validate_parameters(p: SizingParameters) -> None:
    for name in FLOAT_FIELDS:
        value = getattr(p, name)
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if p.account_size <= 0:
        raise ValueError("account_size must be positive")
    if p.entry_price is not None and p.entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if p.stop_price is not None and p.stop_price <= 0:
        raise ValueError("stop_price must be positive")
    if p.stop_price is not None and p.entry_price is not None and p.stop_price >= p.entry_price:
        raise ValueError("stop_price must be below entry_price for long trades")
    if p.risk_pct is not None and p.risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if p.atr is not None and p.atr <= 0:
        raise ValueError("atr must be positive")
    if p.atr_multiplier <= 0:
        raise ValueError("atr_multiplier must be positive")
    if p.win_rate is not None and not 0 < p.win_rate < 1:
        raise ValueError("win_rate must be between 0 and 1 (exclusive)")
    for name in ("avg_win", "avg_loss"):
        value = getattr(p, name)
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be positive")
    if p.brokerage_per_order < 0 or p.dp_charge < 0:
        raise ValueError("brokerage_per_order and dp_charge must not be negative")
    if p.tick_size <= 0:
        raise ValueError("tick_size must be positive")
    if not isinstance(p.lot_size, int) or isinstance(p.lot_size, bool) or p.lot_size < 1:
        raise ValueError("lot_size must be a positive whole number")


# --------------------------------------------------------------------------- charges


def _leg(turnover: Decimal, brokerage: Decimal, *, buy: bool) -> dict:
    stt = turnover * CHARGES["stt_rate"]
    txn = turnover * CHARGES["exchange_txn_rate"]
    sebi = turnover * CHARGES["sebi_fee_rate"]
    stamp = turnover * CHARGES["stamp_duty_buy_rate"] if buy else Decimal(0)
    gst = CHARGES["gst_rate"] * (brokerage + txn + sebi)
    return {
        "turnover": turnover,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_txn": txn,
        "sebi_fee": sebi,
        "stamp_duty": stamp,
        "gst": gst,
    }


def delivery_costs(
    shares: int,
    entry_price: float,
    exit_price: float,
    *,
    brokerage_per_order: float = 0.0,
    dp_charge: float = DEFAULT_DP_CHARGE,
) -> dict:
    """Round-trip delivery charges for buying at entry and selling at exit."""
    if shares <= 0:
        zero_leg = dict.fromkeys(
            ("turnover", "brokerage", "stt", "exchange_txn", "sebi_fee", "stamp_duty", "gst"), 0.0
        )
        return {
            "buy": {**zero_leg, "total": 0.0},
            "sell": {**zero_leg, "dp_charge": 0.0, "total": 0.0},
            "total": 0.0,
        }
    brokerage = _d(brokerage_per_order)
    buy = _leg(_d(shares) * _d(entry_price), brokerage, buy=True)
    sell = _leg(_d(shares) * _d(exit_price), brokerage, buy=False)
    sell["dp_charge"] = _d(dp_charge)
    buy_total = sum(v for k, v in buy.items() if k != "turnover")
    sell_total = sum(v for k, v in sell.items() if k != "turnover")
    as_float = {k: float(v) for k, v in buy.items()}
    as_float_sell = {k: float(v) for k, v in sell.items()}
    return {
        "buy": {**as_float, "total": float(buy_total)},
        "sell": {**as_float_sell, "total": float(sell_total)},
        "total": float(buy_total + sell_total),
    }


def _variable_cost_per_share(entry: Decimal, exit_price: Decimal) -> Decimal:
    stt, txn = CHARGES["stt_rate"], CHARGES["exchange_txn_rate"]
    sebi, stamp, gst = CHARGES["sebi_fee_rate"], CHARGES["stamp_duty_buy_rate"], CHARGES["gst_rate"]
    buy = entry * (stt + txn + sebi + stamp + gst * (txn + sebi))
    sell = exit_price * (stt + txn + sebi + gst * (txn + sebi))
    return buy + sell


def _fixed_cost(p: SizingParameters) -> Decimal:
    return 2 * _d(p.brokerage_per_order) * (1 + CHARGES["gst_rate"]) + _d(p.dp_charge)


# --------------------------------------------------------------------------- sizing


def _to_lots(shares: int, lot_size: int) -> int:
    return max(0, (shares // lot_size) * lot_size)


def round_down_to_tick(price: Decimal, tick: float) -> Decimal:
    step = _d(tick)
    return (price / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _total_risk(p: SizingParameters, shares: int, stop: Decimal) -> Decimal:
    price_risk = _d(shares) * (_d(p.entry_price) - stop)
    if not p.include_costs or shares == 0:
        return price_risk
    costs = delivery_costs(
        shares,
        p.entry_price,
        float(stop),
        brokerage_per_order=p.brokerage_per_order,
        dp_charge=p.dp_charge,
    )
    return price_risk + _d(costs["total"])


def size_for_budget(p: SizingParameters, budget: Decimal, stop: Decimal) -> int:
    """Largest whole-lot share count whose stop-out loss (incl. costs) fits the budget."""
    entry = _d(p.entry_price)
    risk_per_share = entry - stop
    if p.include_costs:
        available = budget - _fixed_cost(p)
        if available <= 0:
            return 0
        shares = int(available / (risk_per_share + _variable_cost_per_share(entry, stop)))
    else:
        shares = int(budget / risk_per_share)
    shares = _to_lots(shares, p.lot_size)
    while shares > 0 and _total_risk(p, shares, stop) > budget:
        shares -= p.lot_size  # guard against rounding at the boundary
    return max(0, shares)


def calculate_kelly(p: SizingParameters) -> dict:
    ratio = p.avg_win / p.avg_loss
    kelly_pct = max(0.0, p.win_rate - (1 - p.win_rate) / ratio) * 100
    return {"kelly_pct": round(kelly_pct, 2), "half_kelly_pct": round(kelly_pct / 2, 2)}


def apply_constraints(shares: int, p: SizingParameters) -> tuple:
    constraints: list = []
    candidates = [shares]
    account, entry = _d(p.account_size), _d(p.entry_price)
    if p.max_position_pct is not None:
        cap = _to_lots(int(account * _d(p.max_position_pct) / 100 / entry), p.lot_size)
        constraints.append(
            {
                "type": "max_position_pct",
                "limit": p.max_position_pct,
                "max_shares": cap,
                "binding": False,
            }
        )
        candidates.append(cap)
    if p.max_sector_pct is not None:
        room = (_d(p.max_sector_pct) - _d(p.current_sector_exposure)) / 100 * account
        cap = _to_lots(max(0, int(room / entry)), p.lot_size)
        constraints.append(
            {
                "type": "max_sector_pct",
                "limit": p.max_sector_pct,
                "current": p.current_sector_exposure,
                "sector": p.sector,
                "max_shares": cap,
                "binding": False,
            }
        )
        candidates.append(cap)
    final = max(0, min(candidates))
    binding = None
    for c in constraints:
        if c["max_shares"] == final and final < shares:
            c["binding"] = True
            binding = c["type"]
    return final, constraints, binding


def calculate_position(p: SizingParameters) -> dict:
    validate_parameters(p)
    result: dict = {
        "schema_version": "1.0",
        "skill": "india-position-sizer",
        "exchange": "NSE",
        "currency": "INR",
        "product": "delivery (CNC)",
    }
    if p.win_rate is not None:
        if p.avg_win is None or p.avg_loss is None:
            raise ValueError("Kelly mode needs win_rate, avg_win and avg_loss")
        kelly = calculate_kelly(p)
        budget = _d(p.account_size) * _d(kelly["half_kelly_pct"]) / 100
        result.update(
            {
                "mode": "budget",
                "parameters": {
                    "account_size": p.account_size,
                    "win_rate": p.win_rate,
                    "avg_win": p.avg_win,
                    "avg_loss": p.avg_loss,
                },
                "calculations": {"kelly": kelly},
                "recommended_risk_budget": _money(budget),
                "recommended_risk_budget_pct": kelly["half_kelly_pct"],
                "note": "Re-run with --entry, --stop and --risk-pct to size shares.",
            }
        )
        return result

    if p.entry_price is None or p.risk_pct is None:
        raise ValueError("Shares mode needs entry_price and risk_pct")
    entry = _d(p.entry_price)
    if p.stop_price is not None:
        stop = _d(p.stop_price)
        method = "fixed_fractional"
    elif p.atr is not None:
        stop = round_down_to_tick(entry - _d(p.atr) * _d(p.atr_multiplier), p.tick_size)
        if not 0 < stop < entry:
            raise ValueError("ATR-derived stop must be positive and below entry_price")
        method = "atr_based"
    else:
        raise ValueError("Shares mode needs a stop_price or an atr")

    budget = _d(p.account_size) * _d(p.risk_pct) / 100
    risk_shares = size_for_budget(p, budget, stop)
    final, constraints, binding = apply_constraints(risk_shares, p)

    costs = (
        delivery_costs(
            final,
            p.entry_price,
            float(stop),
            brokerage_per_order=p.brokerage_per_order,
            dp_charge=p.dp_charge,
        )
        if p.include_costs
        else delivery_costs(0, p.entry_price, float(stop))
    )
    price_risk = _d(final) * (entry - stop)
    total_risk = price_risk + _d(costs["total"])
    result.update(
        {
            "mode": "shares",
            "parameters": {
                "account_size": p.account_size,
                "entry_price": p.entry_price,
                "stop_price": float(stop),
                "risk_pct": p.risk_pct,
                "include_costs": p.include_costs,
                "lot_size": p.lot_size,
                "tick_size": p.tick_size,
                **(
                    {"atr": p.atr, "atr_multiplier": p.atr_multiplier}
                    if method == "atr_based"
                    else {}
                ),
            },
            "calculations": {
                "method": method,
                "risk_budget": _money(budget),
                "risk_per_share": float(entry - stop),
                "shares_before_constraints": risk_shares,
            },
            "constraints_applied": constraints,
            "final_recommended_shares": final,
            "final_position_value": _money(_d(final) * entry),
            "price_risk_inr": _money(price_risk),
            "costs_at_stop": costs,
            "final_risk_inr": _money(total_risk),
            "final_risk_pct": _money(total_risk / _d(p.account_size) * 100),
            "binding_constraint": binding,
            "charges_assumptions": {
                **{k: float(v) for k, v in CHARGES.items()},
                "brokerage_per_order": p.brokerage_per_order,
                "dp_charge": p.dp_charge,
                "note": CHARGES_NOTE,
            },
        }
    )
    return result


# --------------------------------------------------------------------------- report


def generate_markdown_report(result: dict) -> str:
    lines = [
        "# India Position Sizing Report (NSE, delivery)",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Mode:** {result['mode']}",
        "",
    ]
    if result["mode"] == "budget":
        kelly = result["calculations"]["kelly"]
        lines += [
            "## Kelly Criterion",
            f"- Full Kelly: {kelly['kelly_pct']}%",
            f"- Half Kelly (recommended): {kelly['half_kelly_pct']}%",
            f"- **Recommended risk budget:** {format_inr(result['recommended_risk_budget'])}",
            "",
            f"*{result['note']}*",
        ]
        return "\n".join(lines) + "\n"

    prm, calc = result["parameters"], result["calculations"]
    lines += [
        "## Inputs",
        f"- Account size: {format_inr(prm['account_size'])}",
        f"- Entry / stop: {format_inr(prm['entry_price'])} / {format_inr(prm['stop_price'])} "
        f"({calc['method'].replace('_', ' ')})",
        f"- Risk per trade: {prm['risk_pct']}% = {format_inr(calc['risk_budget'])}",
        f"- Lot size: {prm['lot_size']}",
        "",
    ]
    if result["constraints_applied"]:
        lines.append("## Constraints")
        for c in result["constraints_applied"]:
            flag = " **[BINDING]**" if c["binding"] else ""
            lines.append(f"- {c['type']}: limit {c['limit']}%, max shares {c['max_shares']}{flag}")
        lines.append("")
    lines += [
        "## Final Recommendation",
        f"- **Shares:** {result['final_recommended_shares']}",
        f"- **Position value:** {format_inr(result['final_position_value'])}",
        f"- **Price risk at stop:** {format_inr(result['price_risk_inr'])}",
        f"- **Charges if stopped out:** {format_inr(result['costs_at_stop']['total'])}",
        f"- **Total risk:** {format_inr(result['final_risk_inr'])} ({result['final_risk_pct']}% of account)",
    ]
    if result.get("binding_constraint"):
        lines.append(f"- **Binding constraint:** {result['binding_constraint']}")
    if prm["include_costs"] and result["final_recommended_shares"]:
        buy, sell = result["costs_at_stop"]["buy"], result["costs_at_stop"]["sell"]
        lines += [
            "",
            "## Charges at Stop (round trip)",
            "| Charge | Buy | Sell |",
            "|---|---:|---:|",
            f"| STT | {format_inr(buy['stt'])} | {format_inr(sell['stt'])} |",
            f"| Exchange transaction | {format_inr(buy['exchange_txn'])} | {format_inr(sell['exchange_txn'])} |",
            f"| SEBI fee | {format_inr(buy['sebi_fee'])} | {format_inr(sell['sebi_fee'])} |",
            f"| Stamp duty | {format_inr(buy['stamp_duty'])} | {format_inr(sell['stamp_duty'])} |",
            f"| Brokerage | {format_inr(buy['brokerage'])} | {format_inr(sell['brokerage'])} |",
            f"| GST | {format_inr(buy['gst'])} | {format_inr(sell['gst'])} |",
            f"| DP charge | — | {format_inr(sell['dp_charge'])} |",
            "",
            f"*{result['charges_assumptions']['note']}*",
        ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI


def finite_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from None
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"{value!r} must be finite")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Risk-based position size for a long NSE delivery trade (₹, whole shares, costs included)"
    )
    parser.add_argument(
        "--account-size", type=finite_float, required=True, help="Account value in ₹"
    )
    parser.add_argument("--entry", type=finite_float, help="Entry price in ₹")
    parser.add_argument("--stop", type=finite_float, help="Stop-loss price in ₹")
    parser.add_argument(
        "--risk-pct", type=finite_float, help="Risk per trade, %% of account (e.g. 1.0)"
    )
    parser.add_argument(
        "--atr", type=finite_float, help="ATR in ₹ (stop = entry - ATR x multiplier)"
    )
    parser.add_argument("--atr-multiplier", type=finite_float, default=2.0)
    parser.add_argument(
        "--tick-size",
        type=finite_float,
        default=DEFAULT_TICK_SIZE,
        help="Tick for ATR stops (default 0.05)",
    )
    parser.add_argument(
        "--lot-size", type=int, default=1, help="Trading lot (SME stocks trade in lots)"
    )
    parser.add_argument(
        "--win-rate", type=finite_float, help="Kelly budget mode: historical win rate (0-1)"
    )
    parser.add_argument("--avg-win", type=finite_float, help="Kelly budget mode: average win")
    parser.add_argument("--avg-loss", type=finite_float, help="Kelly budget mode: average loss")
    parser.add_argument(
        "--max-position-pct", type=finite_float, help="Max single position, %% of account"
    )
    parser.add_argument(
        "--max-sector-pct", type=finite_float, help="Max sector exposure, %% of account"
    )
    parser.add_argument("--sector", help="Sector / industry name for the concentration check")
    parser.add_argument(
        "--current-sector-exposure",
        type=finite_float,
        default=0.0,
        help="Current sector exposure, %%",
    )
    parser.add_argument(
        "--no-costs", action="store_true", help="Ignore delivery charges when sizing"
    )
    parser.add_argument(
        "--brokerage-per-order",
        type=finite_float,
        default=0.0,
        help="Brokerage per order in ₹ (default 0)",
    )
    parser.add_argument(
        "--dp-charge",
        type=finite_float,
        default=DEFAULT_DP_CHARGE,
        help="DP charge per sell in ₹ (default 15.93)",
    )
    parser.add_argument(
        "--output-dir", default="reports/", help="Report directory (default reports/)"
    )
    return parser


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.win_rate is not None and args.risk_pct is not None:
        parser.error(
            "use either --risk-pct (shares mode) or --win-rate (Kelly budget mode), not both"
        )
    if args.win_rate is None:
        if args.risk_pct is None or args.entry is None:
            parser.error("shares mode needs --entry and --risk-pct")
        if args.stop is None and args.atr is None:
            parser.error("shares mode needs --stop or --atr")
    elif args.avg_win is None or args.avg_loss is None:
        parser.error("Kelly mode needs --win-rate, --avg-win and --avg-loss")

    params = SizingParameters(
        account_size=args.account_size,
        entry_price=args.entry if args.win_rate is None else None,
        stop_price=args.stop,
        risk_pct=args.risk_pct,
        atr=args.atr,
        atr_multiplier=args.atr_multiplier,
        win_rate=args.win_rate,
        avg_win=args.avg_win,
        avg_loss=args.avg_loss,
        max_position_pct=args.max_position_pct,
        max_sector_pct=args.max_sector_pct,
        sector=args.sector,
        current_sector_exposure=args.current_sector_exposure,
        include_costs=not args.no_costs,
        brokerage_per_order=args.brokerage_per_order,
        dp_charge=args.dp_charge,
        tick_size=args.tick_size,
        lot_size=args.lot_size,
    )
    try:
        result = calculate_position(params)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = os.path.join(args.output_dir, f"india_position_sizer_{stamp}.json")
    md_path = os.path.join(args.output_dir, f"india_position_sizer_{stamp}.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(generate_markdown_report(result))

    if result["mode"] == "shares":
        print(
            f"Final: {result['final_recommended_shares']} shares @ {format_inr(params.entry_price)}"
        )
        print(f"Position: {format_inr(result['final_position_value'])}")
        print(
            f"Total risk incl. charges: {format_inr(result['final_risk_inr'])} ({result['final_risk_pct']}%)"
        )
    else:
        print(f"Recommended risk budget: {format_inr(result['recommended_risk_budget'])}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
