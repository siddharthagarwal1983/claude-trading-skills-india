---
name: india-ibd-distribution-day-monitor
description: Detect IBD-style Distribution Days on NSE indexes (NIFTY 50 and NIFTY 500 by default) from free official NSE data with real index volume, track 25-session expiry and 5% invalidation, count d5/d15/d25 clusters, classify market risk (NORMAL/CAUTION/HIGH/SEVERE), and recommend equity exposure for an NSE swing book or a NIFTY index ETF. Use after the Indian market close, before adding NSE swing positions, or as input to india-exposure-coach. No API key required.
---

# India IBD Distribution Day Monitor

## Overview

Count institutional selling days on NSE indexes: a close down at least 0.2% on higher
volume than the prior session. Distribution days expire after 25 sessions or once the
index rallies 5% above the day's close. Clusters within 5, 15 and 25 sessions set a risk
level, which maps to an exposure target. Method identical to the US
`ibd-distribution-day-monitor`; data, sessions and exposure policy are Indian (see
`references/india_market_notes.md`).

## When to Use

- After 19:00 IST, to check whether institutions are distributing NSE stocks.
- Before adding new swing positions in Indian stocks.
- As the distribution input to `india-exposure-coach`.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-ibd-distribution-day-monitor/requirements.txt`.
- Network access to NSE (`mcp.nseindia.in`). No API key.

## Workflow

### Step 1: Run the monitor

```bash
# Default: NIFTY 50 + NIFTY 500, exposure policy for an NSE stock swing book
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py --output-dir reports/

# 1x index ETF holder at 80% exposure
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py \
  --instrument NIFTY_ETF --current-exposure 80 --output-dir reports/

# Other indexes, or a past session (holidays snap to the previous session)
python3 skills/india-ibd-distribution-day-monitor/scripts/india_ibd_monitor.py \
  --symbols "NIFTY 50,NIFTY MIDCAP 150" --as-of 2026-10-23 --output-dir reports/
```

### Step 2: Interpret

Load `references/ibd_distribution_methodology.md` for the rule details and
`references/india_market_notes.md` for NSE specifics (Muhurat removal, expiry and
rebalancing days, peer risk combination, exposure table). Report per-index d5/d15/d25,
the overall level, the portfolio action and any audit flags.

### Step 3: Feed the exposure decision

Pass the JSON to `india-exposure-coach --distribution`. A SEVERE level forces
CASH_PRIORITY there.

## Output Format

`reports/india_ibd_distribution_day_monitor_YYYY-MM-DD_HHMMSS.json` and `.md`.

JSON: `market_distribution_state` (as_of, overall_risk_level, primary_signal_symbol,
index_results with d5/d15/d25 counts, active and removed distribution days, trend filters,
explanation), `portfolio_action` (instrument, recommended_action, target_exposure_pct,
trailing_stop_pct, alternative_action, rationale), `rule_evaluation`, `audit`
(data_source, expected_session, muhurat_sessions_removed, audit_flags).

## Resources

- `scripts/india_ibd_monitor.py` — CLI.
- `scripts/data_loader.py` — NSE index loading with Muhurat removal.
- `scripts/risk_classifier.py`, `scripts/exposure_policy.py` — India risk combination and policy.
- `scripts/distribution_day_tracker.py`, `scripts/models.py` — rule engine.
- `scripts/_india_data.py` — vendored NSE data module (generated; do not edit).
- `config/default.yaml` — indexes, thresholds, strategy context.
- `references/ibd_distribution_methodology.md`, `references/india_market_notes.md`.

## Key Principles

1. Distribution clusters are a risk gauge, not a sell signal for any single stock.
2. Treat a lone expiry-day or rebalancing-day count as weaker evidence than a cluster.
3. The user changes exposure manually; this skill never places orders.
