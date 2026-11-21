# Stockbee Momentum Burst Methodology

## Purpose

This skill operationalizes a Stockbee-style short-term Momentum Burst review process. It is designed to find candidates for manual chart review, not to issue automatic buy signals.

## Core Hypothesis

Some stocks produce short, sharp momentum bursts after a period of range contraction. The useful part of the move often occurs over the next several sessions rather than over a long trend-following window.

The script looks for three trigger families:

1. **4% Breakout**
   - Close is at least 4% above the previous close
   - Volume is higher than the previous day
   - Volume exceeds the configured liquidity floor

2. **Rupee Breakout** (India replacement for Stockbee's dollar breakout)
   - Close minus open is at least `--rupee-threshold` (default ₹25) AND at least `--rupee-breakout-min-pct` of the open (default 1.5%)
   - The percentage guard stops a ₹25 move on a ₹10,000 stock (0.25%) from counting; Indian share prices span ₹50 to ₹1,00,000+

3. **Range Expansion**
   - Current daily range is larger than each of the prior three daily ranges
   - Prior day was not already extended
   - Volume confirms the expansion

## What Makes a Better Candidate

The screener scores the following qualities:

- Clean prior base or short consolidation
- Prior-day narrow range or down day
- Breakout volume expansion
- Close near the high of day
- Entry-day low close enough to define risk
- No recent 4% breakdown
- Not already up three days before the trigger
- Market regime allows new swing risk

## What the Script Does Not Do

The script does not:

- Place orders
- Guarantee follow-through
- Replace manual chart review
- Verify news/catalysts
- Model intraday slippage or liquidity at the level-2/order-book level
- Decide final position size

## Recommended Downstream Flow

```text
stockbee-momentum-burst-screener
        ↓
technical-analyst
        ↓
position-sizer
        ↓
breakout-trade-planner optional
        ↓
trader-memory-core
```

The strongest use case is a daily routine after the market closes or near the close, followed by a manual chart review and a pre-planned stop.
