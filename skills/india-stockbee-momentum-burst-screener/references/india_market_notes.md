# India (NSE) Notes for Momentum Burst Screening

## What changes from the US screener

| Element | US screener | India screener | Reason |
|---|---|---|---|
| Calendar | NYSE sessions, US close | NSE sessions (XBOM schedule + Muhurat), final after 19:00 IST | NSE bhavcopy publishes in the evening |
| Dollar breakout | close - open >= $0.90 | **Rupee breakout:** close - open >= ₹25 **and** >= 1.5% of open | Indian prices span ₹50-₹1,00,000+; a fixed rupee amount alone is meaningless |
| 9M volume tag | >= 9,000,000 shares | **High-volume tag:** >= 5,000,000 shares (`--high-volume-threshold`) | Informational only, never an executable trigger |
| Liquidity | price >= $5, volume >= 100k | price >= ₹50, volume >= 100k shares **and turnover >= ₹2 crore** on the trigger day | Rupee turnover is the meaningful liquidity measure |
| Circuit check | n/a | Soft tag `possible_upper_circuit`; ACTIONABLE_DAY1 is downgraded to MANUAL_REVIEW | A close at the high on a 5/10/20% move may be a locked upper circuit |
| Data | FMP (paid key) | Free official NSE bhavcopy, adjusted for bonuses/splits | No key needed |

Scoring weights, base detection, failure filters, states and ratings are unchanged.

## Upper circuits

NSE caps daily moves with price bands (commonly 2%, 5%, 10% or 20%; F&O stocks use
dynamic bands). When buyers exceed sellers at the band the stock freezes at the upper
circuit with pending buy orders unfilled. A momentum burst that closes exactly at the
day's high with a gain equal to a band is flagged because:

- next-day entry often happens on a gap, far above the reference price;
- risk to the day's low widens sharply after a gap;
- stocks with narrow bands can lock for several sessions.

The flag is heuristic (EOD data does not carry the band). Check the stock's band on the
NSE quote page before acting.

## Entry and exit in India

- Stockbee bursts last 3-5 days. On NSE, plan the exit within that window and expect
  gaps at 09:15 after the pre-open auction.
- Use the trigger day's low as the reference stop, as in the US version. Charges matter
  for short holds: STT is 0.1% on each delivery leg, so size with `india-position-sizer`.
- Intraday (MIS) execution changes charges and margins and is out of scope here.

## Data notes

- History is adjusted on read, so a bonus inside the 80-bar window does not create a fake
  breakdown or breakout.
- Bars are rejected as stale when the latest bar is older than the expected NSE session
  (`--max-bar-age-sessions` relaxes this deliberately).
- `metadata.fetch_errors` lists symbols NSE could not serve (renamed, suspended,
  recently listed). Treat a large count as an incomplete scan.
