# India Signal Outcomes

## What counts as a signal

| Report | Rows used | Signal date | Grade / score / state |
|---|---|---|---|
| india-vcp-screener | `results[]` | `metadata.as_of` | `rating` / `composite_score` / entry_ready or not |
| india-stockbee-momentum-burst-screener | `candidates[]` except state REJECTED | row `date` | `rating` / `setup_score` / `state` |
| india-earnings-trade-analyzer | `results[]` | `metadata.as_of` | `grade` / `composite_score` |
| india-dividend-screener | `results[]` | row `price_date` | `grade` / `score` |
| signals file | `signals[]` in signal-postmortem format | `signal_date` | as given |

All signals are LONG. Reports in a currency other than INR are refused. The
signal id is `<prefix>_<SYMBOL>_<YYYYMMDD>` (`india_vcp`, `india_mbs`,
`india_etr`, `india_div`; `&` becomes `AND`, so M&M is `MANDM`), which makes a
repeated run of the same screen on the same day a duplicate.

## Measuring

- **Entry** is the official close of the last NSE session on or before the
  signal date (a weekend date uses Friday's close). Bars are adjusted for
  splits and bonuses, so a corporate action inside the window does not fake a move.
- **Horizon N** is the close of the N-th session of that stock after the entry
  session. A suspended stock's missing days are skipped, not counted.
- **Benchmark** is NIFTY 50's close on the same two dates; excess = stock
  return − index return.
- **MAE/MFE (5)** are the lowest low and highest high of sessions 1–5 against
  the entry close, in percent.
- Returns are before charges. A round trip in delivery costs about 0.25%
  (0.1% STT per leg plus stamp duty, exchange fees, GST and DP charge), so a
  NEUTRAL or small TRUE_POSITIVE outcome is a loss after costs.

## Classification (from signal-postmortem)

| Class | Rule (5-session return r, LONG) |
|---|---|
| REGIME_MISMATCH | regimes at signal and exit differ, exit regime known, r < −2% |
| NEUTRAL | abs(r) < 0.5% |
| TRUE_POSITIVE | r ≥ 0.5% |
| FALSE_POSITIVE | −2% ≤ r ≤ −0.5% |
| FALSE_POSITIVE_SEVERE | r < −2% |

Regimes are the india-exposure-coach `recommendation` (NEW_ENTRY_ALLOWED,
REDUCE_ONLY, CASH_PRIORITY) from the newest posture report on or before the
date, if it is at most 7 days old; otherwise UNKNOWN. When the signal regime is
UNKNOWN the exit regime is recorded as UNKNOWN too, so missing reports never
create a regime mismatch. `excess_outcome_category` applies the same table to
the excess return with no regime check.

## Summary metrics

- **Accuracy** = TRUE_POSITIVE / (TRUE_POSITIVE + FALSE_POSITIVE*), as in the US skill.
- **False-positive rate** = FALSE_POSITIVE* / all signals.
- **Beat NIFTY 5** = share of signals with positive 5-session excess return.
- **Weight feedback** (≥ 20 signals): 1 + (accuracy − 0.55) × 0.5, clamped to
  0.3–2.0, shown only when it moves at least 0.05. Advisory only.
- **Improvement backlog** (≥ 15 signals): false-positive cluster above 15%,
  regime sensitivity above 25% mismatches, overconfidence above 10% severe
  false positives.

## Caveats

- The sample is the set of saved reports. Screens run only on good days, or
  re-run until they found something, bias the result.
- Overlapping signals (the same stock flagged on consecutive days) are not
  independent; treat large counts from one week as one market episode.
- Five sessions is a short window for VCP and dividend ideas; give the
  20-session columns more weight for them once records complete.
