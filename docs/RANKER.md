# Alpha ranker (2.4.0)

## Why it exists

The calibrated probability models answer "how likely is a gain?". A report from a live run showed they were well
calibrated but had no demonstrated skill: holdout Brier scores did not beat the base rate, and historical top picks
won about half the time. Calibration says the numbers are honest; it does not say the stock ordering is good.
Profit comes from ranking: which securities beat the market. The ranker targets exactly that.

## What it does

- **Target:** net return over the label horizon minus the SPY return, converted to a per-session percentile rank.
- **Features:** price, advanced price and sector-proxy families (plus fundamentals/earnings only when at least 50%
  covered), each converted to per-session percentile ranks. This removes market-wide level shifts, so the model
  learns relative strength instead of market regime.
- **Models:** ridge regression and a shallow gradient-boosted regressor. The choice is made on development folds
  inside the training period; the holdout never influences it.
- **Horizons:** 30, 60 and 90 calendar days (`ranker_horizons`). The composite alpha score is the mean z-score.
- **Selection:** common stocks only, adequate data, price floor (`min_price`), and a downside cap: the 30D
  probability of losing more than 10% must not exceed `ranker_max_p_loss_10`. Then the top `ranker_top_n` by alpha.

## How it is judged (pre-declared, not tuned)

The last `ranker_holdout_fraction` of sessions is held out, purged by the label horizon plus a 5-day embargo.
On that holdout, non-overlapping cohorts (spaced by horizon + embargo) are scored. A horizon is **VALIDATED** only if:

1. at least `ranker_min_cohorts` independent cohorts exist,
2. mean rank correlation is positive with t >= 1,
3. mean top-N excess return over SPY is positive with t >= 1, and
4. top-N beats random selection.

Momentum (`momentum_120`), random and universe-average baselines are shown beside the result. The overall ranker is
VALIDATED only when every horizon passes. Otherwise the report says so, loudly.

## Reading the output

- **Alpha score:** relative ranking only. It is not a forecast of percentage gain.
- **Model median 30/60/90D, P(gain), P(loss>10%):** the probability models' estimates, shown for context. They are
  unvalidated unless the report says otherwise.
- **Holdout evidence line:** the measured excess return per period on data the model never trained on. That is the
  number to trust, and it is an upper bound because the universe is today's active securities (survivorship bias).

## Holdings alerts

For each holding in the report (alerts sorted first, directly under the top 10):

- **PRICE:** last close is below the trailing stop (highest close since purchase minus `stop_atr_multiple` x ATR14).
- **MODEL:** 30D P(gain) below 45% with negative median, a deteriorating/exit review status, or the ranker placing
  the holding in the bottom 20% of scored securities.
- **EVENT:** an 8-K in the last 30 days with an adverse item (1.03, 2.04, 2.06, 3.01, 4.01, 4.02, 5.02).

A desktop notification is sent through `notify-send` when a live run raises alerts (`desktop_notifications`).
These are warnings that deterioration may be under way. They do not predict declines and will miss some.

## Limits

- No free-data model is guaranteed to beat an index fund. Judge this by the holdout evidence and by the prospective
  forecast tracker over several months, not by any single report.
- Survivorship bias, approximate historical universes and free-provider data gaps remain.
- Earnings dates from 8-K item 2.02, sector caps and insider-cluster features are not included in this release.
