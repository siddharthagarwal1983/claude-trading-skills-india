# India (NSE) Notes for Follow-Through Day Detection

## Indexes

| Role | Default | Alternatives |
|---|---|---|
| Primary | NIFTY 50 (fixed) | — |
| Second index | NIFTY 500 (broad market) | NIFTY MIDCAP 150 (risk appetite), NIFTY NEXT 50, NIFTY BANK |

An FTD on either index triggers the signal; both confirming adds +15 to the quality
score. NIFTY MIDCAP 150 often bottoms and follows through before NIFTY 50 when domestic
flows lead a recovery, so it is a useful second lens.

## Volume

NSE index volume is the traded volume of the constituents, so the FTD requirement
("volume above the prior session") applies directly. Diwali **Muhurat sessions** (one
hour, a fraction of normal volume) are removed first: otherwise the next session would
always count as a higher-volume day and could fake a follow-through. Removed dates are
listed under `metadata.muhurat_sessions_removed`.

## Thresholds

O'Neil's day 4-7 window, the 1.25% / 1.5% / 2% gain tiers, the 3% minimum correction and
the post-FTD distribution and invalidation rules are unchanged from the US version.
NIFTY 50's daily volatility is comparable to the S&P 500's, so the tiers transfer
reasonably; they have not been re-fitted to NSE history.

## India-specific cautions

- **Event gaps:** budget day, RBI policy days and state or general election results can
  produce large one-day moves. An FTD on such a day is still valid by the rules, but
  confirm with follow-through over the next sessions and with breadth.
- **FII-driven reversals:** sharp foreign outflow days can invalidate young FTDs. Read
  the result alongside FII/DII flows in `india-exposure-coach`.
- **Data quality:** a missing or zero-volume bar inside the window makes the state
  unavailable (`data_gap`), never silently bullish.

## Hand-off

Feed the JSON to `india-exposure-coach --ftd`. The coach uses the FTD quality score only
while the market is in a correction or rally attempt; in an established uptrend
(`NO_SIGNAL`) the FTD input is treated as not applicable rather than as a zero score.
