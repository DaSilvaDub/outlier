# NBA Phase 2b: model probabilities in the ledger, and promotion evidence

Stacked on PR #292 (`claude/nba-phase2-team-model`). Still SHADOW: nothing here sizes a bet.

## What it adds

| Piece | Where |
|---|---|
| `nba_model_probs` table (one row per event, outcome, run) | `outlier_nba/track/model_ledger.py` |
| `nba_model_eval` view: the first pre-tip model forecast of each outcome joined to the market snapshot that was current when it was made (latest capture at or before the forecast, at most 15 minutes earlier; `snapshot_gap_seconds` is recorded), with that snapshot's settlement and verified CLV | same |
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
  the market probability is the one the model run itself read (so it is time-aligned by construction);
* **the two sides of one quoted line count once** for calibration and log loss (they sum to 1 and settle
  oppositely); CLV is taken from the side the model *backs*, chosen before any de-duplication;
* `n_settled`, `distinct_dates` (Eastern date of tip);
* `calibration_gap`: largest |predicted - observed| over closed buckets holding at least 100 outcomes;
  `None` if no bucket is that large;
* `oos_logloss_beats_market`: True only if the paired log-loss gain over the market has a 95% interval clear of
  zero when resampling whole **games** *and* when resampling whole **dates** (the lines of one game, and the games
  of one night, stay together); `None` below 30 observations, 30 games or 10 dates;
* `median_clv` over backed lines with a *verified* close (within 60 minutes of tip), plus `clv_n`, `clv_dates` and
  `clv_coverage` (verified share of backed lines); promotion needs at least 100 verified observations on 20 dates
  covering half of the backed lines, so one lucky close cannot pass;
* `n_games`: promotion needs at least 300 distinct games, not just 1,500 lines;
* `fitted_weight`: fitted on the earlier 60% of dates (grid capped at 0.25) and reported only if blending it in beats
  the market alone on the later 40%, which the fit never saw; otherwise 0.0.

Anything uncomputable is `None`, and the evaluator treats `None` as a failed key. `promote` without `--apply`
only previews; `--apply` is the only path that writes `config/nba_promotion.json`, and it goes through the
evaluator.

## Daily collection (started 2026-10-22)

`scripts/nba_shadow_daily.ps1` runs one shadow day: `ratings` (refresh results, fit as of today), `capture`
(market snapshots), `run` (price the slate, record model probabilities), `settle` (prior nights), then a
`promote` preview. Everything is SHADOW and no reasoning model is called.

**Ledger location.** It writes a dedicated local ledger, `C:\Users\dasil\outlier_nba_data\ledger\feedback.sqlite3`
(same schema as the shared one), **not** the shared `calibration/feedback.sqlite3`. That file is 281 MB, sits behind
the sync filter that has corrupted it three times (latest 2026-10-21) and is written nightly by the daily job; an
extra writer from here is the risk the repository's own notes warn about. The decision D3 (one shared ledger with
`sport='NBA'`) stands as the end state: pointing any command at the shared ledger is `--db`, and merging later is a
row copy done in local temp and verified from a fresh process. Until then the evidence accumulates in the local file.

First run (2026-10-22, three stages against live Outlier): 14,580 snapshots over 70 games (9,554 player-prop
rows), 4,977 model rows for the 14 games inside the 36-hour horizon, 4,603 of them joined to a market snapshot
73 seconds older, integrity check ok, zero non-zero units and zero actionable decisions. Settlement and `promote`
had nothing to do yet because no captured game had finished. Expect real counts after the first full night, and
nothing close to the promotion thresholds for months.

Login: run from a checkout that has `config/.outlier_session`, or pass `-SessionDir` (the script links the folder in
for the run and removes the link afterwards; it never reads or copies the credentials).

## What this does and does not show

It makes promotion *possible*, not likely. A family needs 1,500 settled lines on 45 distinct dates, a clear
log-loss win over the vig-free market, calibration within 0.03 in well-populated buckets and non-negative
verified CLV. The ledger has had NBA captures only since Phase 1, so no family can reach those counts before
the season has run for months. The Phase 2 backtest has no market comparison, so this prospective evidence is the
only route to one.

Caveats: CLV here is the change in the consensus probability of the captured outcome, not a price realised at a
book. The weight validation is a single time split; a rolling scheme would be stronger once there is a season of
data. A stacked PR does not trigger the repository's CI workflows (they run for PRs into `master`), so retarget
after #292 merges.
