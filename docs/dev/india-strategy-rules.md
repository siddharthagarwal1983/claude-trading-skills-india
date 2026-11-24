# India Swing Strategies: the Rules in Plain English

As backtested by `india-swing-backtester` (written 2026-11-24). Every rule below
is taken from the code that ran the backtest: the screeners, the
`india-breakout-trade-planner` and the `india-paper-ledger` fill rules. Both
strategies are long only, use end-of-day NSE data, and place orders in the
evening for the next session.

---

## Strategy 1: Momentum Burst

Catch a stock on the day it suddenly jumps out of a quiet patch, ride it for
about a week, and get out.

### 1. Which stocks are looked at

- Stocks in the NIFTY 500. NIFTY 100 and NIFTY 50 were also tested.
- The stock must close at ₹50 or more.
- At least 1 lakh shares and at least ₹2 crore must trade that day.

### 2. What counts as a "burst" (the trigger day)

After the close, a stock qualifies if **at least one** of these happened today:

- **4% jump:** it closed 4% or more above yesterday's close, on more volume than yesterday.
- **Rupee jump:** it closed at least ₹25 above today's open, and that is at least 1.5% of the open.
- **Range expansion:** today's high-to-low range is bigger than any of the last
  3 days. Volume is up, and yesterday's gain was no more than 2%, so it wasn't
  already running.

There is one hard limit: if the distance from today's close down to today's low
is more than 10%, the stock is rejected because the stop would be too far away.

### 3. Scoring: only the best bursts are traded

| Points for… | Max |
|---|---|
| How strong the jump was (bigger move, several triggers) | 20 |
| Volume much higher than yesterday and the 20-day average | 15 |
| A quiet, tight sideways patch for 3–20 days before the jump (range within 15%, small daily moves, low volume) | 25 |
| Closing near the day's high | 10 |
| A small gap between the close and the day's low | 15 |
| Avoiding bad signs: 3 up days in a row before the jump, a 4% drop in the last 5 days, loose and choppy action | 10 |
| Market mood (always set to "neutral", so only a few points) | 5 |

- **A trade is taken only when the score is 80 or more.**
- A stock that may have closed locked at its upper circuit is skipped, because
  you probably couldn't buy it the next day.
- There is **no market filter**. Trades are taken even when NIFTY is falling.

### 4. Buying

- The order is a **limit buy at today's close + 2%**, valid for the **next session only**.
- If the stock opens at or below that price, you buy at the open.
- If it opens higher but dips back to the limit during the day, you buy at the limit.
- If it never comes down to the limit, there is no trade and the order is dropped.
- If the stock trades at only one price all day (locked in a circuit), nothing fills.
- You can't hold the same stock twice or have two orders in it at once.

### 5. Stop-loss

- **The stop is the low of the burst day**, rounded down to the NSE price step.
- From the day after you buy: if the stock opens below the stop, you sell at the
  open. Otherwise you sell at the stop if the price touches it.
- Every stop sale loses an extra 0.1% for slippage.
- If buying at the limit price would put the stop more than 10% below it, the trade is skipped.
- The stop is **not checked on the day you buy**. That's a simplification in the backtest.

### 6. Selling if the stop isn't hit

- **There is no profit target.**
- You sell at the **close of the 5th session after the day you bought**, whatever the price.

### 7. How much to buy

- **Risk 0.5% of the account per trade.** On ₹10 lakh that is ₹5,000 lost if
  the stop is hit, charges included.
- The share count is cut down further if any of these is reached: 10% of the
  account in one stock, 30% in one sector, 1% of the stock's average daily
  traded value over the last 20 days, or the cash available.
- **Total risk across all open trades is capped at 6%**, so roughly 12 trades
  at most. New signals beyond that are skipped.

### 8. Costs included

- Indian delivery charges: STT, exchange fees, SEBI fee, stamp duty and GST.
- ₹15.93 DP charge on each sale. Brokerage is set to ₹0.
- Dividends are credited if you hold through an ex-date.

### 9. Test setup and result

₹10 lakh starting capital, 4 June 2024 to 20 November 2026, using only data
that would have been known each day.

**Result:** about −46% on NIFTY 500 stocks, while the NIFTY 500 index rose about
18.5% (NIFTY 100 stocks −28%, NIFTY 50 stocks −21%). The average trade lost
money even before charges, and all 12 parameter variants lost.

---

## Strategy 2: VCP Breakout

Find a stock in a strong uptrend that has paused and is swinging up and down
less and less, a sign that sellers are drying up. Buy when it breaks above the
top of the last swing, with a stop just under the last low and a target at
twice the risk.

### 1. First filter: liquid stocks near their highs

Each evening, every NIFTY 500 stock is checked. To pass, a stock must:

- close at ₹50 or more
- trade at least ₹5 crore a day on average over the last 50 days
- be at least 20% above its 52-week low
- be within 30% of its 52-week high

The 150 stocks closest to their highs go to the next step. Stocks that barely
move (average daily range under 1%) are skipped, and so are stocks with an
unexplained jump of more than 20% (for example, a demerger not yet adjusted in
the data).

### 2. Must be in a strong uptrend (6 of these 7)

1. Price above its 150-day and 200-day averages
2. 150-day average above the 200-day average
3. 200-day average higher than it was a month ago
4. Price above its 50-day average
5. Price at least 25% above its 52-week low
6. Price within 25% of its 52-week high
7. Has beaten the NIFTY 500 more than about 70% of stocks have (recent months
   count most: 40% of the weight is on the last 3 months)

### 3. Must show the "VCP" pattern (within the last 120 days)

- At least **2 pullbacks**, each lasting 5 or more days.
- The first pullback is at least 10% deep.
- **Each pullback is smaller than the one before**, at most 70% as deep
  (for example 20%, then 12%, then 6%).
- The tops of the swings are roughly level, within about 5% of each other.
- **Pivot** = the high of the last pullback, which is the breakout level.
- **The price today must still be below the pivot.** A stock that has already
  broken out is not bought.
- The price must not have fallen below the last pullback's low or below the
  50-day average, because that breaks the pattern.

### 4. Scoring: only good patterns are traded

| Part | Weight |
|---|---|
| Uptrend strength (step 2) | 25% |
| Pullback quality: more pullbacks and a tighter last one score higher | 25% |
| Volume drying up near the pivot | 20% |
| Closeness to the pivot (just below scores highest) | 15% |
| Strength against NIFTY 500 | 15% |

- Only the **top 20 stocks by score** that day are considered.
- **A trade needs a score of 70 or more:** 70–79 is "Good", 80–89 "Strong", 90+ "Textbook".
- A score of 60–69 only goes on a watchlist and is never bought.
- Sloppy patterns, where the last pullback is more than 15% deep and under 10
  days long, are capped below 70, so they are never traded.

### 5. Buying

- The order is a **buy-stop** for the **next session only**: buy only if the
  price rises through **pivot + 0.1%**.
- There is a price cap at **pivot + 2%**. It is never bought above this.
- If the stock opens between the trigger and the cap, you buy at the open.
- If it opens below the trigger and later rises through it, you buy at the trigger.
- If it opens above the cap (a gap-up), there is no trade.
- Each buy pays an extra 0.1% for slippage, but never more than the cap.
- If the price never reaches the trigger, the order is dropped.
- If the stock trades at only one price all day (locked in a circuit), nothing fills.
- You can't buy a stock you already hold or have an order in.

### 6. Stop-loss

- **The stop is 1% below the low of the last pullback**, rounded down to the NSE price step.
- **If the gap from the price cap down to the stop is more than 8%, the trade
  is skipped.** This rule removed most setups.
- From the day after you buy: if the stock opens below the stop, you sell at the
  open. Otherwise you sell at the stop if the price touches it. Stop sales lose
  an extra 0.1% for slippage.

### 7. Profit target

- **Target = cap price + 2 × (cap price − stop)**, which is 2R.
- If the stock opens above the target, you sell at the open. Otherwise you sell
  at the target when the price reaches it.
- If one day touches both the stop and the target, the stop is assumed to have
  come first. This is the cautious choice.
- **There is no time exit.** You hold until the stop or the target is hit.

### 8. How much to buy

- The normal risk is 0.5% of the account, adjusted by the score:
  - **Textbook (90+): 0.875%** (×1.75)
  - **Strong (80–89): 0.5%** (×1)
  - **Good (70–79): 0.375%** (×0.75)
- Risk is measured from the price cap (the worst price you might pay) down to
  the stop, with charges included.
- The same caps as momentum burst apply: 10% of the account per stock, 30% per
  sector, 1% of the stock's average daily traded value, available cash, and a
  **6% limit on total open risk**.

### 9. Costs, test setup and result

- Costs are the same as momentum burst: Indian delivery charges, the ₹15.93 DP
  charge on each sale, ₹0 brokerage, and dividends credited.
- Starting capital was ₹10 lakh. The test ran from 13 January 2025 to
  20 November 2026, using only data that would have been known each day.

**Result:** in almost 2 years there were only **7 buy orders, and 2 of them
filled. Both were stopped out.** The strategy returned −0.76% while NIFTY 500
rose 13.4%.

It barely trades because two rules remove almost everything: the **8% maximum
stop distance** and the **score of 70 or more**. Loosening them (up to a 12% stop
distance, a score of 60, and a 3R target) gave at most 22 trades, which is still
too few to show an edge. On NIFTY 50 and NIFTY 100 it made **0 trades**, because
large caps almost never form a valid pattern.
