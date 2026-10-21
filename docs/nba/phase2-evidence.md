# NBA Phase 2b: model probabilities in the ledger, and promotion evidence

Stacked on PR #292 (`claude/nba-phase2-team-model`). Still SHADOW: nothing here sizes a bet.

## What it adds

| Piece | Where |
|---|---|
| `nba_model_probs` table (one row per event, outcome, run) | `outlier_nba/track/model_ledger.py` |
| `nba_model_eval` view: first pre-tip model forecast joined to `nba_outcome_eval` (first pre-tip market capture, settlement, verified CLV) | same |
| Evidence per market family from settled outcomes | `outlier_nba/track/evidence.py` |
| `python -m outlier_nba.cli run` records model rows in the ledger (`--db`, `--no-ledger`) | `team_run.py`, `cli.py` |
| `python -m outlier_nba.cli promote [--apply] [--config FILE]` | `cli.py` |
| `promotion.preview` (what the evaluator would decide, writes nothing) | `track/promotion.py` |

`run` writes to the ledger as its **last** stage and only when every earlier stage passed and the run publishes
(live/fixture), the same rule the Phase 1 capture follows. A fixture run needs an explicit non-default `--db` (or
`--no-ledger`). Capture and model runs keep separate bundle roots (`data/NBA` and `data/NBA/team`).

## How evidence is computed

From `nba_model_eval`, per family:

* only settled W/L outcomes with both a model and a market probability (a push or void is not an observation);
  probabilities are the two-way, push-excluded ones, the same basis as a de-vigged two-way price;
* **the two sides of one quoted line count once** (they sum to 1 and settle oppositely);
* `n_settled`, `distinct_dates` (Eastern date of tip);
* `calibration_gap`: largest |predicted - observed| over closed buckets holding at least 100 outcomes;
  `None` if no bucket is that large;
* `oos_logloss_beats_market`: True only if the paired log-loss gain over the market has a 95% interval clear of
  zero; `None` below 30 observations;
* `median_clv`: only the side the model would back (model probability above the market's) and only against a
  *verified* close (within 60 minutes of tip); `None` if there is none;
* `fitted_weight`: the log-loss-minimising blend weight on a grid capped at 0.25.

Anything uncomputable is `None`, and the evaluator treats `None` as a failed key. `promote` without `--apply`
only previews; `--apply` is the only path that writes `config/nba_promotion.json`, and it goes through the
evaluator.

## What this does and does not show

It makes promotion *possible*, not likely. A family needs 1,500 settled lines on 45 distinct dates, a clear
log-loss win over the vig-free market, calibration within 0.03 in well-populated buckets and non-negative
verified CLV. The ledger has had NBA captures only since Phase 1, so no family can reach those counts before
the season has run for months. The Phase 2 backtest has no market comparison, so this prospective evidence is the
only route to one.

Caveats: lines inside one game (spread, total, team totals, alt rungs) are correlated, so the `n` counts overstate
the independent information; `n_settled` is a floor on data, not a significance guarantee. CLV here is the change in
the consensus probability of the captured outcome, not a price realised at a book.
