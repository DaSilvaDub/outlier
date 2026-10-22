# NBA Phase 2: team model, backtest and shadow pricing

Branch `claude/nba-phase2-team-model`. Design of record:
`docs/plans/2026-10-20-nba-master-pipeline.md` (M4, M5, M8 steps 1-4, M10, M11, M12, section 6).
Everything here is **SHADOW**: units 0, never actionable, model blend weight 0.

## What shipped

| Piece | Where |
|---|---|
| ESPN history (10 seasons), cache, SQLite store, copy-and-verify | `outlier_nba/ingest/espn.py`, CLI `backfill` |
| Possessions, dataset, ridge per-100 (home term, decay, prior, stress games) | `outlier_nba/ratings/{possessions,ridge,model}.py` |
| Elo with MOV multiplier | `outlier_nba/ratings/elo.py` |
| Log-additive pace, overtime ratio | `outlier_nba/pace.py` |
| Team simulation (possessions, correlated efficiencies, regulation, OT) | `outlier_nba/sim/engine.py` |
| Push-aware ML / spread / total / team total / alt-line pricing, blend | `outlier_nba/pricing/` |
| Walk-forward backtest, tuning, metrics | `outlier_nba/track/{backtest,tune,metrics}.py` |
| Promotion evaluator stub (hash-guarded config) | `outlier_nba/track/promotion.py`, `config/nba_promotion.json` |
| Daily steps | CLI `ratings`, `run`, `backtest` (`outlier_nba/team_run.py`) |

`daily_job` is untouched: default leagues stay `MLB,WNBA`.

## Data and gaps (store: 12,850 games, 2016-17 to 2025-26)

* Every regular season is complete: 1230 / 1230 / 1230 / 1059 / 1080 / 1230 x5 (0 missing). Postseason and
  play-in games are included. The NBA Cup final (ESPN files it as regular season) is kept and counted separately.
* **Box scores:** 12,500 games have exact possessions (FGA + 0.44 FTA - OREB + TOV). 350 do not: ESPN returns
  empty team statistics for 340 games in 2016-17/2017-18 (player lines exist, not cached), plus 10 others
  (6 play-in 2020-21, 4 playoff 2025-26). These use FGA + 0.44 FTA from the scoreboard plus a *causal* running
  mean of (TOV - OREB). Measured on 1,141 exact games, that fallback is unbiased (+0.24) but noisy (sd 3.6
  possessions, about 4 points per 100), which is why the box-score backfill was worth about 13k extra requests.
* Not final: 71 postponed games (recorded, rescheduled copies appear on their new dates). Skipped: 725
  preseason/other events and 18 All-Star-type events with non-NBA "teams".
* 2019-20 bubble (172 games from 2020-07-30): ESPN does not flag them neutral; derived from the date and
  treated as neutral-site stress games. 2020-21: 578 games with attendance 0, whole season a stress slice.
  Stress games use a separate home column (`h_stress`), so they never move the home-court term used elsewhere.
* Fetch was 5 requests per burst with a pause, repo User-Agent, stop on 403/429. Total about 16k requests. The store
  was built in local temp, copied in, and re-counted from a fresh interpreter (integrity ok, counts match).

## First-time setup on a new checkout or machine

The history is **not** in git: the games store (`data/NBA/store/nba_games.sqlite3`, 2 MB) and the ESPN cache (about
16k small files, 31 MB, default `%LOCALAPPDATA%/outlier_nba/espn_cache`) are local. A checkout without them cannot fit
the model: `ratings` then finds no out-of-sample games, and (before this was fixed) it installed a 5-day store that hid
the gap. Now:

* `python -m outlier_nba.cli backfill --stage all` builds everything (scoreboard, then box scores, then the store);
  about 16k polite requests, several hours, resumable, stops on 403/429.
* or copy the store and the cache from a machine that has them and point at the cache with `OUTLIER_NBA_CACHE`
  (the *parent* of `espn_cache`), and at the store with `--store`.
* `ratings` refuses (exit 3, `history_missing`) to install a first store holding fewer than 2,500 games, and the
  `team_state` stage message names the game count and the command above.

## Backtest

Daily refit; a game is predicted only from games on earlier Eastern dates. Hyperparameters tuned on
2016-17/2017-18 only (scored on 2017-18; coordinate descent). The scoring window is 2018-19 to 2025-26.
The simulator's noise parameters come from earlier out-of-sample predictions. The pooled forecast weights
the simulation and Elo with a weight fit on strictly earlier seasons (first fit uses 2017-18, which is also the
tuning season, so 2018-19 is slightly flattered; its weight came out 0).
4,000 draws per game. **No closing lines exist for this history, so there is no market comparison and none is claimed.**

Tuned values: ridge lam 10, tau 90 d, carry 0.7; pace lam 5, tau 30 d, carry 0.9; Elo K 16, HFA 70, carry 0.75,
MOV exponent 0.8; noise half-life 500 games. Final overtime ratio 1.76 (see below).

### Win probability, all scored games (n = 10,229)

| Model | Brier | Log loss |
|---|---|---|
| Constant home court | 0.2469 | 0.6870 |
| Elo only | 0.2187 | 0.6270 |
| Ridge only (Gaussian on ridge margin) | 0.2181 | 0.6258 |
| Simulation (ridge + pace + noise) | 0.2181 | 0.6256 |
| Pool (sim + Elo, nested weight) | 0.2180 | 0.6253 |

Paired log-loss gain of the pool (positive = pool better), 95% bootstrap CI:
vs constant home court +0.0617 [0.0561, 0.0679]; vs Elo +0.0017 [0.0009, 0.0024];
vs ridge +0.0005 [-0.0014, 0.0021]; vs sim +0.0004 [-0.0015, 0.0021].

**What this says.** Everything beats the constant baseline decisively. The simulation and the ridge are
indistinguishable from each other, and the pool beats Elo by 0.0017 log loss: statistically positive but tiny.
For the moneyline the ridge/pace/simulation stack buys almost nothing over Elo. Its value is that it prices
spread, total and team totals consistently from one set of draws, which Elo cannot.

Excluding the two stress seasons (n = 7,917): sim 0.6220, Elo 0.6232, pool 0.6212. Regular season (9,522) pool 0.6235;
postseason (670) pool 0.6494; play-in (37) pool 0.6377 (too small to read).

Per season (pool log loss / Elo log loss): 2018-19 .6157/.6157, 2019-20 .6299/.6299, 2020-21 .6483/.6494,
2021-22 .6357/.6376, 2022-23 .6466/.6483, 2023-24 .6141/.6156, 2024-25 .6125/.6167, 2025-26 .6027/.6054.
Stress slices: 2019-20 bubble restart (172 games) pool .6874, essentially a coin flip, ECE 0.10;
2020-21 pool .6483, ECE 0.052 (the model prices a home court that no longer existed).

### Calibration (pool, closed buckets, ECE 0.020 overall)

Predicted vs observed home-win rate by bucket: 0.1-0.2 .167/.126 (n 95); 0.2-0.3 .259/.235 (494);
0.3-0.4 .355/.319 (1028); 0.4-0.5 .452/.429 (1598); 0.5-0.6 .552/.530 (2173); 0.6-0.7 .648/.633 (2259);
0.7-0.8 .747/.736 (1653); 0.8-0.9 .839/.822 (864). The pool is a little overconfident toward the home side
in nearly every bucket (it over-predicts home wins by 1 to 4 points in every bucket), and the ECE is worse
in individual seasons (0.043 in 2018-19, 0.050 in 2025-26). The bucket tables in `phase2_backtest.json` have
Wilson intervals.

### Margin, total, overtime

* Margin MAE: sim 10.89, ridge 10.89, constant (expanding mean) 12.03.
* Total MAE: sim 14.78, ridge 14.86, trailing 1,000-game mean 16.06.
* **Total bias (before the 2026-10-22 fix): the simulation under-predicts totals by 1.0 points** (-0.98
  excluding stress seasons). Diagnosis on the walk-forward predictions: pace is unbiased (mean surprise -0.09
  possessions), but points per 100 possessions are under-predicted by about 0.7 in nearly every season (+1.4
  points a game at actual possessions); the rest is overtime, which the analytic regulation total omits. The sign
  persisted within seasons, and an expanding mean of past residuals overshot later seasons (+2.7 in 2023-24), so the
  bias is not stationary.
  **Fix:** a level correction equal to the exponentially weighted mean of the model's own earlier out-of-sample
  per-100 residuals, added to both teams (half-life 200 games, tuned on 2016-17 to 2017-18 only, applied once 200
  games of history exist, identical in the backtest and in live pricing). It cannot change who is favoured. Result on
  2018-19 to 2025-26 (n = 10,229): total bias -1.02 to +0.28, total MAE 14.79 to 14.77, win-probability log loss and
  margin MAE unchanged. The accuracy gain is tiny (a 1-point bias is small against a 14.8-point error); what changes is
  that the model's total is centred, so its over/under probabilities are no longer shifted. Seasons still differ
  (2018-19 -1.5, 2023-24 +1.6).
* **Total spread.** The simulation treated pace and efficiency as independent; empirically a game that runs faster than
  expected is less efficient per possession (correlation -0.16, about -31 points per 100 per unit of log-possession
  surprise). Adding that term (estimated out of sample) narrows the simulated total's standard deviation from 20.2 to
  19.2 against a realised 18.7; the mean and the margin are unchanged.
* Margin spread: the tracker's sigma 13.74 against a realised residual sd of 13.90 (about 1% narrow).
* Overtime: actual 5.3% of games; predicted 5.2% (by season 4.5-6.6% actual vs 4.7-5.8% predicted).

**Which predictor is live.** Shadow pricing uses the ridge + pace + noise *simulation* only; the
Elo pool is a backtest ensemble and is not used for pricing. The out-of-sample figure that
corresponds to the live predictor is the `sim` row (log loss 0.6256, Brier 0.2181), not the pool's
0.6253. The two differ by 0.0004 (CI includes zero).

## Plan items that proved wrong or needed changing

1. **Overtime cannot be left to ties.** A Gaussian margin ties about 43% less often than NBA games actually go to
   overtime (observed/Gaussian ratio 1.76). The simulator pulls one-point margins to ties to match, and the ratio is
   estimated out of sample (`pace.ot_scale`, `NoiseTracker.ot_ratio`).
2. **ESPN's scoreboard lacks turnovers and offensive rebounds**, so exact possessions need a per-game summary call,
   and 340 early games have none. The scoreboard-only estimate adds about 4 points per 100 of target noise.
3. **The NCAA ridge structure does not beat Elo for win probability** (above). The prior (carry 0.7/0.9) and
   short decay windows (90 d ratings, 30 d pace) that the tuning chose show the NBA signal is mostly recent form.
4. The plan's `h * home` term is coded as a +1/-1/0 sign per team row, so h is a half-advantage per 100
   (the fitted value sits near 0.75 at the start of 2026-27, where lam_h pulls it to last season's).
5. The plan's team-level "features-logit" ensemble member and rest/travel terms are **not** built (no schedule/rest
   table yet); the pool has two members.
6. Hyperparameters were tuned on a single season (1,230 regular-season games); expect the tuned values to be noisy.

## Shadow pricing and promotion

`python -m outlier_nba.cli run` prices every quoted game-line and team-total outcome (main lines and alt rungs) from
one simulation per game, push-aware, and writes `team_shadow.csv`, `team_fair_ladders.csv`,
`team_ratings.csv` and `team_model_state.json` into the run bundle. `final_prob` is the vig-free market
probability (weight 0). A row without a usable market probability gets a blank `final_prob` and a
`MISSING_MARKET_PROB` flag. Stale results (newest result more than 7 days old), a missing store or a cold noise
tracker fail the required `team_state` stage and withhold publication (exit 3). The 7-day rule is
season-aware: a season opener with no result yet from its own season (first 45 days after Oct 1)
runs on last season's final ratings and is marked `prior_only_season_opener`. The local store alone
cannot tell "not started" from "feed stopped", so this is allowed only when a refresh receipt
(`refresh_receipt.json`, written by every successful `ratings` refresh) shows a refresh within a day
of the slate that covered the previous day and saw no final game of the new season. With no such
receipt, or once any result from the season exists, or after the window, stale results fail.

Team identity uses every field the normalizer accepts (`alias`, `abbr`, `abbreviation`, `teamName`,
`name`, market + name) and the normalizer's own resolution of each outcome record wins. The
`price_team_markets` stage is `FAILED` if no quoted outcome got a model probability and
`INCOMPLETE` if any quoted game could not be simulated; both withhold publication.

Venue: `event_draws` takes the schedule's neutral-site flag when the feed carries one
(`neutralSite`, `neutral_site`, `neutral`, `isNeutral`), parsed strictly (real booleans, 0/1 and
true/false-style strings; anything else is treated as unknown, logged, and flagged). The Outlier schedule's fields are
unverified, so when none is present the row is flagged `HOME_COURT_ASSUMED` rather than
silently treated as verified. One simulation per game feeds the quoted rows and the fair ladders.

`ratings`/`backfill --stage build` refuse to install a store that has fewer games or box scores in
any (season, season type) than the installed one (`StoreRegressionError`; the installed file is
left untouched; `backfill build` exits 5).

`config/nba_promotion.json` is read only through `outlier_nba.track.promotion` and written only by its
`evaluate`. It carries a content hash, so an accidental or casual hand edit makes every reader raise. The
hash is tamper-evident, not tamper-proof (anyone who can write the file can recompute it); the real
controls are code review of the config and the fail-closed default. A family is promoted only if
every key passes on supplied evidence; unknown evidence (for example no market comparison) counts as failing.
Even a promoted family keeps units 0 because sizing is not wired.

## Not verified

* `run` against the live Outlier API was not exercised (this session has no Outlier credentials); it was run
  against the fixture client. `ratings` was run live against ESPN (refresh of the last 4 days) and the 2026-27 fit
  has 3 games, so it is mostly the 2025-26 prior.
* The NBA full-game `periodLabel` values on Outlier remain unverified (inherited from Phase 1).
* Model probabilities are not written to the shared ledger; they live in the run bundle. Joining them to
  `nba_outcome_eval` for the promotion evidence is the next step.
