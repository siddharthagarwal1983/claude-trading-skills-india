# India Delivery Costs and Position Sizing

## Why costs belong inside the risk budget

A fixed-fractional plan risks a set percentage of the account per trade (commonly 1%).
On NSE delivery trades, Securities Transaction Tax (STT) alone is 0.1% of turnover on
*both* the buy and the sell. On a tight stop the round-trip charges can be 4-5% of the
planned risk, so a plan that ignores them overshoots its own rule. The sizer therefore
finds the largest share count where:

```
shares x (entry - stop) + round-trip charges at the stop <= account x risk%
```

## Charges modelled (equity delivery / CNC)

| Charge | Rate | Leg | Notes |
|---|---|---|---|
| STT | 0.1% of turnover | Buy and sell | Statutory |
| Exchange transaction charge | 0.00297% | Buy and sell | NSE cash segment |
| SEBI turnover fee | ₹10 per crore (0.0001%) | Buy and sell | Statutory |
| Stamp duty | 0.015% | Buy only | Statutory, uniform across states |
| GST | 18% | Both | On brokerage + exchange charge + SEBI fee |
| Brokerage | `--brokerage-per-order` (default ₹0) | Both | Zero for delivery at most discount brokers |
| DP charge | `--dp-charge` (default ₹15.93) | Sell, once per scrip per day | Broker-specific; includes GST |

Rates change through budgets and exchange circulars. Check them against a recent
contract note and override `--brokerage-per-order` / `--dp-charge` for your broker.
Intraday (MIS) and F&O trades have different STT and stamp rates and are out of scope.

## Worked example

Account ₹10,00,000, risk 1% (₹10,000), entry ₹1,000, stop ₹950:

| Item | Value |
|---|---|
| Shares ignoring costs | 200 (₹10,000 / ₹50) |
| Variable charges per share at the stop | ≈ ₹2.17 (₹1.19 buy + ₹0.98 sell) |
| Fixed charges | ₹15.93 DP |
| Shares with costs | 191 |
| Price risk | ₹9,550 |
| Charges if stopped | ≈ ₹430 |
| Total risk | ≈ ₹9,981 (0.998%) |

## Indian market mechanics that affect sizing

- **Whole shares only.** NSE cash equities do not trade in fractions.
- **SME platform lots.** NSE Emerge (SME) stocks trade in fixed lots (often ₹1-2 lakh
  per lot). Pass `--lot-size`; the sizer rounds down to whole lots and may return 0.
- **Tick size.** Most stocks tick in ₹0.05; price-band based tick sizes apply to some
  scrips. ATR-derived stops are rounded *down* to `--tick-size` so the stop is never
  tighter than intended.
- **Price bands (circuits).** A stock locked at its lower circuit cannot be sold at the
  stop. Gap risk through a 5%, 10% or 20% band is real: size smaller on stocks with
  narrow bands or a history of circuit hits.
- **T+1 settlement.** Delivery shares credit the next trading day; sale proceeds are
  available T+1. Plan cash for overlapping trades.
- **Margin and pledging.** This sizer assumes fully paid delivery. Leverage via MTF or
  pledged collateral raises effective risk and is not modelled.

## Methods

- **Fixed fractional** (`--stop`): primary method.
- **ATR-based** (`--atr`, `--atr-multiplier`): stop = entry - ATR x multiplier, rounded
  down to the tick.
- **Kelly** (`--win-rate`, `--avg-win`, `--avg-loss`): returns a half-Kelly risk budget
  only; re-run in shares mode with `--risk-pct` derived from it. Half Kelly or less is
  the norm, and many traders cap it at 1-2%.

## Portfolio constraints

- `--max-position-pct`: caps position value at entry as a share of the account.
- `--max-sector-pct` with `--current-sector-exposure`: caps new exposure to the room left
  in that NSE industry. Use the industry names from the Nifty constituent files so
  sector checks line up with the India screeners.
