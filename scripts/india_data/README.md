# India data foundation

`india_data.py` is the shared end-of-day data layer for the `india-*` skills
(NSE equities, swing horizon). Standard library only, Python 3.9+; the trading
calendar needs `pandas-market-calendars==5.2.2`. Tests:
`scripts/tests/test_india_data.py` (no network).

## What it does

| Piece | Source | Notes |
|---|---|---|
| Daily stock bars | NSE bhavcopy MCP (`mcp.nseindia.in`, free, no key) | 3-month chunks; archive holes (much of 2023) are skipped, not treated as end of history |
| Bonus/split adjustment | NSE corporate actions | NSE history is unadjusted. Prices before an ex-date × factor, volume ÷ factor; same-day factors multiply |
| Jump warnings | computed | Moves > 20% with no adjustment factor, e.g. demergers (NSE gives them a neutral factor) |
| Index bars | NSE bhavcopy MCP | Real volume, turnover (₹ cr), P/E, P/B, dividend yield |
| Calendar | `XBOM` + Muhurat sessions | Bhavcopy treated as final after 19:00 IST |
| Universe | NSE index CSVs (`nsearchives.nseindia.com`) | nifty50/100/200/500, midcap150, smallcap250, microcap250, totalmarket; cached 7 days |
| Credit ledger + metered cache | local files | Guardrails for DalalOS (500 credits/month) and Drishti (1,000 lifetime credits) |

Cache: `./.cache/india-data/` (gitignored), or `$INDIA_DATA_CACHE_DIR`. Raw
bars and actions are stored; adjustment happens on read, so a newly announced
split re-adjusts history without refetching it.

## CLI

```bash
python3 scripts/india_data/india_data.py session                       # latest final NSE session
python3 scripts/india_data/india_data.py bars RELIANCE --start 2024-01-01   # adjusted (add --raw for as-traded)
python3 scripts/india_data/india_data.py index "NIFTY 50" --start 2026-01-01
python3 scripts/india_data/india_data.py universe nifty500
python3 scripts/india_data/india_data.py update --universe nifty500 --limit 50  # warm the cache
python3 scripts/india_data/india_data.py ledger record --source dalalos --tool get_financials --credits 1
python3 scripts/india_data/india_data.py ledger status
python3 scripts/india_data/india_data.py metered get --source dalalos --tool get_financials --args '{"query": "TCS"}'
```

Exit codes: 0 success, 1 data/input error (message on stderr), 2 missing
`pandas-market-calendars`.

A cold fetch is ~10 s per symbol for history from 2024; afterwards each
session costs one short call per symbol. Warm the whole Nifty 500 once with
`update --universe nifty500` (roughly 80 minutes).

## Metered sources (DalalOS, Drishti)

These are Claude connectors, so scripts cannot call them. Within a Claude
session, check `metered get` before a call, save the response with `metered
put --ttl-days N`, and log it with `ledger record`. Routing rule: free sources
first (NSE, cm-market, Dhan, WebSearch), then Drishti's zero-credit calendar
calls, then DalalOS for shortlists only, and Drishti paid calls only for data
nothing else has.
