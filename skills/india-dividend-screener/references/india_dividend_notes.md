# Indian Dividend Notes

## Data

- NSE corporate actions list each dividend as text, e.g. "Interim Dividend - Rs 6.50 Per
  Share" or "Dividend - Rs 6.75 Per Share /Special Dividend - Rs 2.75 Per Share". The
  screener parses the rupee amounts and separates special dividends.
- NSE keeps about five years of actions, giving four complete fiscal years plus the
  current year to date.
- **Restating:** dividends paid before a bonus or split are multiplied by the action's
  factor (a 1:1 bonus halves earlier per-share dividends; a 10-for-1 split divides them by
  ten). Example: Angel One paid ₹23 in January 2026 and split 10-for-1 in February; per
  today's share that dividend is ₹2.30. Yields computed without this step are inflated.

## Scoring

| Component | Points | Rule |
|---|---:|---|
| Yield (regular, TTM) | 30 | ≥5% 30 · ≥4% 25 · ≥3% 20 · ≥2% 12 · ≥1% 5 |
| Consistency | 25 | paid in every complete FY 25 · missed one 12 |
| Growth | 20 | CAGR ≥10% 20 · ≥5% 14 · ≥0% 8; −5 per cut (FY total down > 5%) |
| Valuation (P/E, if known) | 15 | ≤12 15 · ≤18 11 · ≤25 6 · else 2 |
| Pullback (RSI 14) | 10 | ≤40 10 · ≤50 5 |

Without P/E the total is rescaled from 85 to 100. Grades: A ≥ 75, B ≥ 60, C ≥ 45, D below.

## Indian payout patterns

- **PSUs** (Coal India, ONGC, PFC, REC, NTPC, oil marketers) pay high, regular dividends,
  partly to meet government payout guidelines. Yields move with government policy and
  commodity cycles.
- **Metals and mining** (Vedanta, Hindustan Zinc, NMDC) pay large but volatile dividends
  that track commodity prices and group cash needs; check for cuts.
- **IT services and FMCG** (TCS, Infosys, HCL Tech, ITC, Hindustan Unilever) pay
  steady, growing dividends with high payout ratios, plus occasional specials and buybacks.
- **Banks** usually pay once a year after the annual results; private banks have low
  yields.
- Interim dividends are common; fiscal-year totals, not single payments, show the trend.

## Taxation (resident individuals)

- Since FY2020-21 dividends are taxed in the shareholder's hands at the **income-tax slab
  rate**, not at a concessional rate. High-bracket investors keep much less of a high yield.
- Companies deduct **TDS of 10%** when a shareholder's dividends from that company exceed a
  per-financial-year threshold (₹5,000, raised to ₹10,000 from FY2025-26); verify the
  current threshold. Submitting Form 15G/15H can avoid TDS when total income is below the
  taxable limit.
- Interest paid to borrow for the investment is deductible against dividend income only up
  to 20% of that dividend income.
- Non-residents face 20% withholding (lower under a tax treaty with a Tax Residency
  Certificate).
- This is general information, not tax advice; the user should confirm with a tax adviser.
