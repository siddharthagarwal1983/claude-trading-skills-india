---
name: india-ftd-detector
description: Detect Follow-Through Day (FTD) market-bottom confirmations on NSE indexes using William O'Neil's rally-attempt state machine, tracking NIFTY 50 with a second index (default NIFTY 500) on free official NSE data, with post-FTD health checks and a 0-100 quality score. Use when the user asks whether the Indian market has bottomed, about follow-through days or rally attempts on Nifty, or whether it is safe to increase NSE equity exposure after a correction. No API key required.
---

# India FTD Detector

## Overview

Track NIFTY 50 and a second NSE index through O'Neil's cycle: correction → swing low →
rally attempt (day 1) → follow-through day (day 4-7, strong gain on higher volume) →
post-FTD health (distribution, invalidation, power trend). Produces a combined state and
a quality score. Engine identical to the US `ftd-detector`; indexes and session handling
are Indian (see `references/india_market_notes.md`).

States: `NO_SIGNAL`, `CORRECTION`, `RALLY_ATTEMPT`, `FTD_WINDOW`, `FTD_CONFIRMED`,
`RALLY_FAILED`, `FTD_INVALIDATED`.

## When to Use

- After a correction in Indian equities, to judge whether a bottom is confirmed.
- Before raising NSE exposure from defensive levels.
- As the bottom-confirmation input to `india-exposure-coach`.

## Prerequisites

- Python 3.9+ and `pip install -r skills/india-ftd-detector/requirements.txt`.
- Network access to NSE (`mcp.nseindia.in`). No API key.

## Workflow

### Step 1: Run the detector

```bash
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --output-dir reports/

# Mid caps as the second lens
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py \
  --secondary-index "NIFTY MIDCAP 150" --output-dir reports/

# A past session
python3 skills/india-ftd-detector/scripts/india_ftd_detector.py --as-of 2026-10-30 --output-dir reports/
```

### Step 2: Interpret

Load `references/ftd_methodology.md` for the state machine and scoring,
`references/post_ftd_guide.md` for health checks after a confirmation, and
`references/india_market_notes.md` for NSE specifics. Report the combined state, each
index's state, swing low, rally day count, FTD date and gain, and the quality score.
A `data_gap` means the state is unavailable, not bullish.

### Step 3: Feed the exposure decision

Pass the JSON to `india-exposure-coach --ftd`.

## Output Format

`reports/india_ftd_detector_YYYY-MM-DD_HHMMSS.json` and `.md`.

JSON: `metadata` (instruments primary/secondary, index_prices, expected_session,
muhurat_sessions_removed), `market_state` (combined_state, dual_confirmation, ftd_index,
data_gap), `primary` and `secondary` (state, swing_low, rally_attempt, ftd, lifecycle),
`quality_score` (total_score, signal, guidance, exposure_range), `post_ftd_distribution`,
`ftd_invalidation`, `power_trend`.

## Resources

- `scripts/india_ftd_detector.py` — CLI and NSE loading.
- `scripts/rally_tracker.py`, `scripts/post_ftd_monitor.py`, `scripts/ftd_lifecycle.py` — engine.
- `scripts/report_generator.py` — reports.
- `scripts/_india_data.py`, `scripts/_price_bars.py` — vendored modules (generated; do not edit).
- `references/ftd_methodology.md`, `references/post_ftd_guide.md`,
  `references/india_market_notes.md`.

## Key Principles

1. An FTD confirms a bottom attempt; most still need breadth and leadership to follow.
2. Scale exposure up gradually after a confirmation; respect invalidation levels.
3. Unavailable data is reported as unavailable, never as a signal.
