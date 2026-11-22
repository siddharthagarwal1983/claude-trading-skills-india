# India Breakout Rules — Entry, Stop, Sizing and Orders

These rules adapt Mark Minervini's VCP breakout entry (as implemented by the US
`breakout-trade-planner`) to NSE cash-market delivery trades.

## Trade Prices

| Level | Formula | Rounding |
|-------|---------|----------|
| Signal entry (buy-stop trigger) | pivot × (1 + pivot buffer, default 0.1%) | up to the tick |
| Worst entry (limit / chase cap) | pivot × (1 + max chase, default 2%) | down to the tick, never below the trigger |
| Stop loss | last contraction low × (1 − stop buffer, default 1%) | down to the tick |
| Target | worst entry + R-multiple (default 2) × (worst entry − stop) | nearest tick |

Size and risk use the **worst** entry: if the order fills at the trigger, the
real risk is smaller than planned.

## NSE Tick Bands (from 15 April 2025)

| Share price | Tick |
|-------------|------|
| below ₹250 | ₹0.01 |
| ₹250 to ₹1,000 | ₹0.05 |
| above ₹1,000 to ₹5,000 | ₹0.10 |
| above ₹5,000 to ₹10,000 | ₹0.50 |
| above ₹10,000 to ₹20,000 | ₹1.00 |
| above ₹20,000 | ₹5.00 |

Source: NSE circular NSE/CMTR/67133 (13 March 2025). NSE reviews each stock's band
monthly using past closing prices.

## Sizing

1. Risk budget = account × base risk % × band multiplier
   (good 0.75, strong 1.0, textbook 1.75; developing and weak get no plan).
2. Shares = the largest whole number whose loss at the stop fits the budget:
   shares × (worst entry − stop) + buy-leg charges + sell-leg charges at the stop.
3. Caps (the smallest wins and is reported as the binding constraint):
   - max position % of the account (default 10%)
   - remaining sector room (default 30% per sector, counting held paper positions
     and plans already made in this run)
   - max % of average daily rupee turnover (default 1%) for liquidity
   - cash: position value + buy charges must fit the paper ledger's cash
4. Heat: a plan that would lift total open risk (ledger open risk + plans so far)
   above the heat cap (default 6%) is deferred.

Delivery charges per leg: STT 0.1%, NSE transaction 0.00297%, SEBI ₹10 per crore,
GST 18% on brokerage + transaction + SEBI; stamp duty 0.015% on the buy leg; DP
charge (default ₹15.93) on the sell leg.

## Worked Example (BHEL, 20 Nov 2026 data)

Pivot ₹435.95, last contraction low ₹408.15, account ₹10,05,787, base risk 0.5%,
band "good" (score 72.5), max chase 1%, stop buffer 0%:

- Trigger ₹436.40 (436.39 rounded up), cap ₹440.30, stop ₹408.15, 2R target ₹504.60.
- Budget = ₹10,05,787 × 0.5% × 0.75 = ₹3,771.70.
- 113 shares: price risk ₹3,632.95 + charges ≈ ₹122.74 = ₹3,755.69 ≤ budget;
  114 shares would exceed it.

With the default 2% chase and 1% stop buffer the same stock needs 9.1% risk from
the cap to the stop, so the gate rejects it (maximum 8%).

## Orders

- **Paper:** STOP_LIMIT buy (trigger, limit = cap) with stop and target, valid for
  `--valid-sessions` sessions, placed with `india-paper-ledger place-plan`. The
  ledger fills it from the session's open/high (gap above the cap → no fill).
- **Broker (manual):** NSE `SL` buy order, product `CNC`, trigger = signal entry,
  price = cap, validity DAY. NSE has no bracket order for delivery: after the fill,
  place the stop (SL-M sell or a GTT) and the target sell separately.

## Plan Validity

A plan is for one NSE session: the same day if generated before 09:15 IST on a
trading day, otherwise the next trading day (XBOM calendar). A plan made from a
screener report older than the latest completed session carries a
`STALE_SCREENER` warning.
