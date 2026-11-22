# India Paper Ledger — Fill, Timing and Charge Rules

Paper fills are simulated from one official NSE end-of-day bar per session (open, high,
low, close from the bhavcopy, unadjusted). A daily bar cannot show the order of events
inside the session, so every ambiguous case takes the outcome that is worse for the
trader.

## Order Eligibility (IST)

| Order type | Placed | First session that can fill it |
|------------|--------|--------------------------------|
| close | before 15:00 IST on a session day | that session |
| close | 15:00 IST or later | the next session |
| limit / stop / stop-limit | before 09:15 IST | that session |
| limit / stop / stop-limit | 09:15 IST or later | the next session |

The 15:00 cutoff reflects how NSE sets the closing price: the volume-weighted average of
the last 30 minutes (15:00-15:30). Validity counts sessions the stock actually traded
(a suspended stock does not use up validity). Diwali Muhurat sessions are skipped.

## Entry Fills

- **close** — the official closing price.
- **limit L** — if open ≤ L, fill at the open (the market gapped through the limit);
  else if low ≤ L, fill at L; else no fill.
- **stop T** (buy-stop / breakout) — if open ≥ T, fill at the open; else if high ≥ T,
  fill at T; else no fill. Slippage (`--slippage-bps`) is added.
- **stop-limit T / cap L** — as stop, but if the open is already above L there is no
  fill that session (a later fill inside the cap cannot be proved from a daily bar).
  Slippage never pushes the price above L.

A buy that fills at or below its own stop (for example a close order on a sharp down
day) is recorded honestly, flagged as a gap fill, and the stop exits it next session.

## Exit Fills

Exits are evaluated from the session after the entry session.

- **Stop, intraday basis** — if open ≤ stop, fill at the open (gap down); else if
  low ≤ stop, fill at the stop. Slippage is deducted.
- **Stop, close basis** — only if the close ≤ stop, fill at the close.
- **Target** — if open ≥ target, fill at the open (gap up); else if high ≥ target,
  fill at the target.
- **Both in one bar** — the stop wins, unless the session opened at or above the target.
- **Manual sell** — fills at the next eligible close (all shares or `--qty`); if a
  stop or target closes the position first, the sell order is cancelled.

## Circuit-Locked Sessions

When high = low the stock traded at one price all day, usually locked at its upper or
lower price band. Neither buyers (upper circuit) nor sellers (lower circuit) could
reliably trade, so no paper fill happens that session and a warning is raised.

## Charges (Equity Delivery, NSE)

| Charge | Rate | Leg |
|--------|------|-----|
| STT | 0.1% of turnover | buy and sell |
| NSE transaction charge | 0.00297% | buy and sell |
| SEBI fee | ₹10 per crore | buy and sell |
| Stamp duty | 0.015% | buy only |
| GST | 18% of (brokerage + transaction charge + SEBI fee) | both |
| DP charge | ₹15.93 per sell (configurable) | sell only |
| Brokerage | per order (configurable, default ₹0) | both |

The rates come from the shared India data module (`delivery_leg_charges`), the same table `india-position-sizer` uses (a parity test guards drift). Example: buying
40 RELIANCE at ₹1,307.90 (turnover ₹52,316) costs ₹52.32 STT + ₹7.85 stamp + ₹1.55
transaction + ₹0.05 SEBI + ₹0.29 GST = ₹62.06.

## Accounting

- Cash = starting capital + Σ journal cash movements (buys: −turnover − charges;
  sells: +turnover − charges; dividends: +amount).
- trader-memory-core receives charge-inclusive prices: buy = price + charges/qty,
  sell = price − charges/qty. Its `pnl_dollars` field therefore holds net rupee P&L.
- Unrealized P&L in the report deducts the estimated sell charges at the last close.
- Open risk = qty × (effective entry − stop) + sell charges at the stop, floored at 0.

## Corporate Actions

NSE corporate actions in the bar cache are checked every session:

- **Dividend** (action type DIVIDEND) — rupees per share × shares held, credited on the
  ex-date. In practice the payment arrives weeks later and is taxable.
- **Bonus / split** (factor ≠ 1) and **demerger, rights, scheme, buyback, consolidation**
  (type OTHER with those words) — a held position is blocked (exits paused) because the
  share count and price scale changed; a pending entry order is cancelled. Reconcile by
  hand in trader-memory-core and re-create the paper position.
