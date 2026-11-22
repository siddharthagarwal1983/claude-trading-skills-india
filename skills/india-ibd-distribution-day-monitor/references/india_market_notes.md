# India (NSE) Notes for Distribution Day Monitoring

## Indexes and volume

- **NIFTY 50** (large caps) and **NIFTY 500** (broad market) are read from NSE's official
  bhavcopy index history. NSE index volume is the total traded volume of the
  constituents, so the IBD "higher volume than the prior session" test applies directly
  without an ETF proxy.
- Any NSE index can be monitored with `--symbols`, e.g. `"NIFTY 50,NIFTY BANK"` or
  `"NIFTY MIDCAP 150"`. Sector indexes are noisier; use them as context, not as the
  primary signal.

## Sessions that distort volume

- **Muhurat trading (Diwali):** a one-hour evening session with a fraction of normal
  volume. The next ordinary session always looks like "higher volume", which would
  manufacture a distribution day. Muhurat sessions are removed before analysis and listed
  under `audit.muhurat_sessions_removed`.
- **F&O expiry days:** index derivative expiries raise cash-market volume. A down close
  on expiry still counts (as IBD counts quarterly expiries), but treat a lone expiry-day
  distribution day as weaker evidence than a cluster.
- **Index rebalancing days** (Nifty semi-annual rebalances, MSCI/FTSE reviews): closing
  auction volume spikes. Check the date before reading too much into one count.
- **Holidays:** `--as-of` on a holiday or weekend snaps to the previous NSE session.

## Risk combination

NIFTY 50 and NIFTY 500 are treated as peers:

| Condition | Overall level |
|---|---|
| Either index SEVERE | SEVERE |
| Both indexes at CAUTION or worse | at least HIGH (distribution confirmed across large caps and the broad market) |
| Otherwise | the higher of the two |

## Exposure policy (no leveraged ETFs in India)

| Risk | SWING_BOOK (individual stocks) | NIFTY_ETF (1x index ETF) |
|---|---|---|
| NORMAL | hold / follow strategy, 100% | hold / follow strategy, 100% |
| CAUTION | avoid new adds, 75%, stop ≤ 8% | avoid new adds, 100%, stop ≤ 8% |
| HIGH | reduce to 50%, stop ≤ 6%; tighten stops, sell laggards | reduce to 75%, stop ≤ 6% |
| SEVERE | raise cash to 75% (25% invested), stop ≤ 4% | reduce to 50%, stop ≤ 5% |

Single NSE stocks usually fall further than the index once institutions distribute, and
small caps can gap through their price bands, hence the faster cuts for SWING_BOOK.

## Thresholds

The IBD rule (close down at least 0.2% on higher volume; expires after 25 sessions or a
5% rally) and the NORMAL / CAUTION / HIGH / SEVERE cluster thresholds are unchanged from
the US version. They have not been re-fitted to NSE history; treat the levels as a
discipline framework, not a calibrated probability.
