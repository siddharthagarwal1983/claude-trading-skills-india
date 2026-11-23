# India Weekly Digest Metrics

## Trade selection

A trade belongs to a week when its thesis `status` is `CLOSED` and the IST date
of `exit.actual_date` falls inside the window. The default window is Monday to
Sunday of the week holding the latest NSE session whose bhavcopy is final
(after 19:00 IST). IST matters: a timestamp of `2026-11-15T19:00:00Z` is
00:30 IST on Monday 16 November, so it belongs to the week of 16 November.

## Headline metrics (same as weekly-performance-digest)

| Metric | Definition |
|---|---|
| Win rate | winners / closed trades; `pnl > 0` wins, `< 0` loses, `0` breakeven |
| Expectancy | mean net P&L per closed trade (₹) |
| Profit factor | gross wins / gross losses; undefined with no losses |
| R-multiple | net P&L / ((entry price − recorded stop) × shares); blank when that risk is not positive |
| MAE / MFE | lowest and highest close from entry to exit vs the entry price (%) |
| Pattern buckets | wins, losses and win rate by signal source, exit reason, thesis type, sector, mechanism tag, screening grade |

For paper trades the thesis entry and exit prices are charge-inclusive, so P&L
and R-multiples are net of charges and the risk in R includes the buy-side
charges.

## India additions

| Field | Definition |
|---|---|
| `entry_price`, `exit_price` | quantity-weighted raw NSE fill prices from the ledger journal |
| `charges` | statutory delivery charges on every fill of the trade (both legs) |
| `gross_pnl` | Σ sell value − Σ buy value at raw prices; equals net P&L + charges |
| `dividends` | dividends the ledger credited while the trade was open |
| `net_pnl_incl_dividends` | net P&L + dividends |
| `holding_sessions` | NSE sessions after the entry session up to and including the exit session |
| `benchmark_pct` | benchmark close on the exit session / close on the entry session − 1 |
| `excess_pct` | trade P&L % − `benchmark_pct` |
| `week_activity` | all fills and dividends dated inside the week, including trims of open positions |
| `realized_equity_*` | starting capital + realized P&L of sells + dividends before / after the week |
| `charges_drag` | charges ÷ gross P&L over the week's closed trades |

## Caveats

- **NIFTY 50 is a price index.** It excludes dividends, so excess returns
  slightly flatter the trades; a NIFTY ETF would also have paid dividends.
- **MAE/MFE use closes.** An intraday low below the stop can trigger a stop
  without the close ever showing it. Bars are unadjusted; the paper ledger pauses
  positions through splits and bonuses, so held periods do not span one.
- **Realized equity ignores open positions.** Use `india-paper-ledger status`
  for equity marked to the latest close.
- **Taxes are not modelled.** STCG/LTCG on realized gains and tax on dividends
  are left out.

## Interpreting

- An excess return below zero on most trades means the selection added nothing
  over holding the index for the same sessions.
- A charges drag above about 10% of gross profit points to positions too small
  or holds too short for 0.1% STT on each leg.
- `stop_hit` clusters with MAE close to the stop distance suggest entries that
  were extended; small MAE with stop hits suggests stops that were too tight.
