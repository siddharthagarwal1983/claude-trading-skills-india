# Indian Results Calendar Notes

## How results are disclosed

- Listed companies must give the exchanges advance notice of the board meeting that will
  consider quarterly results (SEBI LODR). That board-meeting date is the "results date"
  in this skill.
- Results are filed within the regulatory deadline: 45 days after quarter end for Q1-Q3
  and 60 days for Q4 (annual). This creates four results seasons:

| Quarter (FY Apr-Mar) | Quarter end | Main season |
|---|---|---|
| Q1 | 30 Jun | mid-July to mid-August |
| Q2 | 30 Sep | mid-October to mid-November |
| Q3 | 31 Dec | mid-January to mid-February |
| Q4 / annual | 31 Mar | mid-April to end of May |

- Quarter labels from Drishti look like `q2_27` (Q2 of FY2027 = July-September 2026).
- NSE does not publish announcement times. Many companies announce during market hours,
  others after the close or late at night, so the price reaction can land on the meeting
  day or the next session.

## Data sources and cost

| Source | Call | Cost | Use |
|---|---|---|---|
| Drishti | `get_upcoming_earnings` (symbols filter, paged) | free | daily upcoming fetch |
| DalalOS | `get_upcoming_results` (days ≤ 90) | ~1 credit | fallback |
| DalalOS | `get_market_calendar` types=results (past window) | ~1 credit per ~250 rows | narrow backfills only |

The local archive turns free upcoming fetches into a results-date history: once a date
passes it stays in `results_calendar.json`, where `india-earnings-trade-analyzer` reads it.

## Holding through results

- A results surprise can open the stock far beyond its stop; NSE price bands (5%, 10%,
  20%) can then lock it, so the stop may not fill for a session or more.
- Common practices: exit or trim before the date, hold only a smaller position, or hold only
  with a profit cushion larger than a typical results move for that stock.
- Avoid opening new swing positions in the last few sessions before a results date unless
  the trade is specifically a results play.
