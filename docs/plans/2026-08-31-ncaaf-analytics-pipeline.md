# NCAA FBS Football Analytics Pipeline — Implementation Plan

Status: **PLAN — awaiting confirmation. No implementation code written.**
Author: agent `claude`, 2026-08-31.
Target: new standalone repository `cfb-analytics` (separate `.git`, no code shared with `outlier`).

## 0. Decisions taken before planning

| # | Decision | Chosen | Consequence |
|---|---|---|---|
| 1 | Module home | **Separate repo, shared patterns** | Ledger, settlement, calibration, promotion gates, CI are re-implemented here, modelled on `outlier`'s equivalents but importing nothing from it. |
| 2 | Data backbone | **CFBD + Outlier odds + ESPN availability + Open-Meteo** | Four-source ingestion layer with a uniform `Source` protocol and per-source health/staleness flags. **Outlier probe completed 2026-08-31 — league token is `NCAAFB`, confirmed usable; see §14.** |
| 3 | Math stack | **Pure stdlib Python** | IRLS logistic, ridge via conjugate gradient, Elo, PAVA isotonic, Gauss–Hermite quadrature. No numpy/scipy/sklearn/pandas. |
| 4 | First milestone | **Backtest harness first** | No CORE-tier parlay is emitted until the backtest clears the promotion gate in §9. |

Patterns deliberately mirrored from `outlier` (re-implemented, not imported):
`paths.league_paths` directory discipline; `registry.SportConfig` alias tables; `feedback_db` SQLite ledger with
settlement + CLV; `probability_blend`'s **two-key promotion** (sample-size floor **and** demonstrated
out-of-sample skill) and its `DEFAULT_MARKET_WEIGHT_FLOOR` shrinkage-toward-market safety margin;
`team_strength.py`'s pure-Python ridge and its explicit "infrastructure, not a validated forecast" stance.

---

## 1. The three-model split (architectural core)

The single most important structural rule, and the one that stops this becoming a picks generator:

| Model | Question | Inputs | Forbidden inputs |
|---|---|---|---|
| **S — Safety** | Who is least likely to lose? | Fundamentals, roster, context, weather | **Any market price, spread, or total** |
| **M — Market** | What does the market think? | Odds ladder across books, movement, hold | Fundamentals |
| **V — Value** | Is the price worth paying? | `P_S`, `P_M`, movement, hold, staleness | — |
| **T — Totals** | Where is the total mispriced? | Pace, PPP, matchup, script, weather | Market total (comparison only) |

`S` never sees a price. If it did, it would regress to the market and every "edge" would be an artifact.
Two probabilities are therefore published per game, and they are **not interchangeable**:

- `P_S_cal` — calibrated fundamentals-only win probability. Drives **edge**: `edge = P_S_cal − P_M_vigfree`.
- `P_blend = sigmoid( w * logit(P_S_cal) + (1 − w) * logit(P_M_vigfree) )` — best available estimate of
  reality. Drives **tiering, parlay probability, and every survival number reported**.

`w` is learned per segment (odds range × week-of-season × data-quality tier) and **floored so the market
dominates** (`w <= 0.25` until a segment demonstrates out-of-sample Brier improvement), exactly as
`probability_blend.DEFAULT_MARKET_WEIGHT_FLOOR = 0.75` does for the market side in `outlier`.

Reporting a parlay's survival probability from `P_S_cal` would be self-flattery. It uses `P_blend`.

---

## 2. Repository layout

```
cfb-analytics/
├── pyproject.toml            # py>=3.11, deps: python-dateutil only. dev: pytest, ruff, mypy
├── config/
│   ├── settings.json         # tiers, thresholds, decay budget, HFA priors
│   ├── promotion.json        # two-key gate state (§9)
│   └── sources.json          # base URLs, TTLs, rate limits (keys via env only)
├── data/                     # gitignored
│   ├── cfb.sqlite3
│   └── cache/                # HTTP cache, replayable in tests
├── artifacts/                # fitted models, calibration knots, backtest reports
└── cfb_analytics/
    ├── config.py  paths.py  db.py  errors.py
    ├── sources/    __init__ (Source protocol)  cfbd  outlier_odds  espn  weather  cache
    ├── ingest/     games  lines  ratings  advanced  roster  availability  weather_ingest  backfill
    ├── features/   spec (FEATURE_SPEC)  asof (AsOfReader)  team_form  elo  matchup  context
    │               market  availability_features  build
    ├── models/     linalg  logistic  ridge  elo_model  normal  ensemble  calibrate
    │               mismatch  safety  market  value  totals  confidence
    ├── parlay/     eligibility  correlation  optimizer  fragility
    ├── report/     sections  render_md  render_csv  render_html
    ├── backtest/   harness  metrics  parlay_sim  baselines  report
    └── cli.py
```

---

## 3. Database schema (SQLite, `db.py` with numbered migrations)

Every table carries `as_of_utc` where the value can change during a week. Nothing is overwritten;
history is append-only so the backtest can reconstruct any point in time.

### 3.1 Dimensions

```sql
teams(team_id PK, cfbd_id, school, mascot, abbreviation, conference, division,
      classification, venue_id, first_season, last_season)

venues(venue_id PK, name, city, state, latitude, longitude, elevation_m,
       surface, dome BOOL, capacity, timezone)

coaches(coach_id PK, name)
coach_tenure(team_id, season, coach_id, role CHECK(role IN ('HC','OC','DC')),
             first_season_at_school, years_at_school, PRIMARY KEY(team_id, season, role))

players(player_id PK, cfbd_id, team_id, name, position, class_year, height, weight)
```

### 3.2 Games and context

```sql
games(game_id PK, season, week, season_type, kickoff_utc, neutral_site BOOL,
      conference_game BOOL, home_team_id, away_team_id, venue_id,
      home_points, away_points, completed BOOL, ingested_utc)

game_context(game_id PK, home_rest_days, away_rest_days, rest_diff,
             home_travel_km, away_travel_km, tz_shift_home, tz_shift_away,
             altitude_delta_m, is_rivalry BOOL, is_short_week BOOL,
             is_bye_return_home BOOL, is_bye_return_away BOOL,
             lookahead_home BOOL, lookahead_away BOOL,
             letdown_home BOOL, letdown_away BOOL,
             bowl_implication, title_implication, surface, dome BOOL)

weather(game_id, as_of_utc, hours_to_kick, temp_c, wind_kph, wind_gust_kph,
        wind_dir_deg, precip_mm, precip_prob, humidity, is_forecast BOOL,
        forecast_spread REAL,          -- dispersion across model runs -> confidence deduction
        source, PRIMARY KEY(game_id, as_of_utc))
```

`is_rivalry` is a curated static table (~180 pairs), not inferred. `lookahead_*` / `letdown_*` are
deterministic: opponent-quality rank of the *adjacent* game relative to this one, thresholded in config.

### 3.3 Market (append-only)

```sql
odds_snapshots(snapshot_id PK, game_id, book, captured_utc,
               market CHECK(market IN ('ML','SPREAD','TOTAL')),
               side, line REAL, price_american INT, source)

market_consensus(game_id, market, side, as_of_utc, n_books, consensus_price,
                 best_price, best_book, vig_free_prob, hold,
                 PRIMARY KEY(game_id, market, side, as_of_utc))

line_movement(game_id, market, as_of_utc, open_line, open_price, current_line,
              current_price, move_magnitude, move_direction, rlm_flag BOOL,
              rlm_basis TEXT,  -- always 'line_only' until a ticket%/money% source exists
              PRIMARY KEY(game_id, market, as_of_utc))
```

Devig: two-sided **multiplicative (proportional)** removal as the default, `p_i = q_i / sum(q)`,
plus **Shin** and **power/worst-case** computed alongside. All three are stored and the backtest
selects by out-of-sample log loss. Heavy CFB favourites are exactly where devig method choice moves
the answer by 1–3 pp, so this is not cosmetic.

**Sharp-anchor rule — CONDITIONAL, not yet supported by observed data (see §14.2).** If sharp books
(`PS3838`/Pinnacle, `CIRCA`, `BETONLINE`, `BOOKMAKER`) are reliably present, `P_M_vigfree` anchors on
that subset rather than a mean across all books where soft books would drown them out, and
`soft_book_divergence` (sharp price vs soft consensus) becomes a feature in its own right.

**Book coverage is time-varying and the sharp books were absent on re-measurement**, so this rule is
implemented as a fallback chain rather than an assumption:

1. sharp subset, when >= 2 sharp books are priced;
2. otherwise full-book consensus across whatever is present, with the row flagged
   `no_sharp_anchor` and confidence reduced;
3. never a single book silently standing in for "the market".

`cfb-analytics coverage` reports books-per-capture and sharp presence for every ingest, so this is
settled by accumulated measurement rather than by one snapshot. Best-available price for EV always
scans every book present.

### 3.4 Performance

```sql
team_game_stats(game_id, team_id, plays, drives, seconds_per_play,
  epa_per_play, epa_pass, epa_rush, success_rate, explosiveness,
  early_down_sr, third_down_conv, redzone_td_rate, points_per_drive,
  finishing_drive_pts, havoc_rate, sack_rate, stuff_rate, line_yards,
  turnovers, turnover_margin, garbage_excluded BOOL,
  PRIMARY KEY(game_id, team_id))

team_season_advanced(season, week, team_id, side CHECK(side IN ('off','def')),
  epa_per_play, success_rate, explosiveness, ppd, havoc, sack_rate,
  line_yards, stuff_rate, finishing_drives, opponent_adjusted BOOL,
  PRIMARY KEY(season, week, team_id, side))

team_ratings(season, week, team_id,
  source CHECK(source IN ('sp','srs','elo_cfbd','fpi','internal_ridge','internal_elo')),
  rating, off_rating, def_rating, st_rating, sos,
  PRIMARY KEY(season, week, team_id, source))
```

### 3.5 Roster / availability

```sql
returning_production(season, team_id, overall_ppa, passing_ppa, rushing_ppa,
  receiving_ppa, total_usage, returning_starters_off, returning_starters_def,
  ol_continuity_starts, PRIMARY KEY(season, team_id))

talent(season, team_id, talent_composite, recruit_rank_3yr, blue_chip_ratio,
       PRIMARY KEY(season, team_id))

portal(season, team_id, additions_rating, departures_rating, net_rating,
       PRIMARY KEY(season, team_id))

qb_status(game_id, team_id, as_of_utc, starter_player_id,
  status CHECK(status IN ('confirmed','probable','questionable','doubtful','out','unknown')),
  starter_career_attempts, starter_epa_per_play, starter_games_started,
  backup_player_id, backup_quality_score, source, source_confidence,
  PRIMARY KEY(game_id, team_id, as_of_utc))

-- designation enum matches the values the Outlier NCAAFB feed actually emits (§14.3)
availability(game_id, team_id, player_id, as_of_utc, position_group,
  designation CHECK(designation IN ('Out','Out for Season','Doubtful','Questionable','Probable')),
  injury_type,          -- 'Undisclosed' dominates (99/177); Knee/Ankle/Leg/... otherwise
  return_date, last_updated_utc, has_news BOOL,
  starter BOOL, snap_share_prior REAL, source,
  PRIMARY KEY(game_id, team_id, player_id, as_of_utc))
```

### 3.6 Model output and ledger

```sql
feature_rows(game_id, team_id, as_of_utc, feature_spec_version,
             features_json TEXT, null_mask_json TEXT, imputed_mask_json TEXT,
             PRIMARY KEY(game_id, team_id, as_of_utc))

model_predictions(game_id, model_name, model_version, as_of_utc,
  home_win_prob_raw, home_win_prob_cal, blend_weight_market,
  home_win_prob_blend, proj_home_pts, proj_away_pts, proj_total, proj_margin,
  sigma_margin, sigma_total, confidence, feature_spec_version,
  PRIMARY KEY(game_id, model_name, model_version, as_of_utc))

mismatch_scores(game_id, favorite_team_id, as_of_utc, mismatch_score,
                components_json, PRIMARY KEY(game_id, as_of_utc))

candidates(candidate_id PK, slate_date, game_id, team_id, market, as_of_utc,
  model_prob_safety, model_prob_blend, market_prob_vigfree, edge_pct,
  mismatch_score, confidence, tier CHECK(tier IN ('CORE','SUPPORTING','AVOID')),
  upset_risk_score, primary_upset_path, secondary_upset_path,
  flags_json, primary_reason, primary_concern, price_american, book)

parlays(parlay_id PK, slate_date, n_legs, independent_prob, adjusted_prob,
  correlation_rho_avg, book_implied_prob, edge, decimal_price, risk_score,
  weakest_candidate_id, second_weakest_candidate_id, avg_confidence,
  rejected_leg_json,      -- why leg n+1 was refused: the optimizer's audit trail
  construction_reason, created_utc)

parlay_legs(parlay_id, leg_index, candidate_id, PRIMARY KEY(parlay_id, leg_index))

settlements(candidate_id PK, outcome CHECK(outcome IN ('win','loss','push','void')),
  settled_utc, closing_price, clv_bps, actual_margin, actual_total)

calibration_artifacts(artifact_id PK, model_name, fit_utc, method,
  knots_json, n_train, n_holdout, holdout_brier, baseline_brier,
  holdout_logloss, baseline_logloss, promoted BOOL)

promotion_state(model_name PK, status CHECK(status IN ('shadow','promoted')),
  changed_utc, evidence_json)

runs(run_id PK, started_utc, finished_utc, command, git_sha,
     source_versions_json, status, error)
```

---

## 4. Source mapping, with honest availability grades

`FEATURE_SPEC` grades every requested input `available | proxy | unavailable`. **A field graded
`unavailable` renders as `n/a (no public source)` in every report. It is never imputed and never
silently dropped.**

| Requested input | Source | Endpoint / method | Grade |
|---|---|---|---|
| Power rating | CFBD | `/ratings/sp`, `/ratings/srs`, `/ratings/elo` | available |
| Off / Def / ST efficiency | CFBD | `/stats/season/advanced?excludeGarbageTime=true` | available |
| Points per drive, finishing drives | CFBD | `/drives`, `/stats/season/advanced` | available |
| EPA/play, success rate, explosiveness | CFBD | `/ppa/teams`, `/ppa/games`, `/stats/game/advanced` | available |
| Early-down / 3rd-down / red-zone | CFBD | `/stats/season/advanced` | available |
| Havoc, sack rate, stuff rate, line yards | CFBD | `/stats/season/advanced` | available |
| **Pressure rate** | — | closest public proxy is sack rate + havoc | **proxy** |
| **OL / DL grades** | — | proxy: line yards, stuff rate, sack rate allowed | **proxy** |
| **Turnover-worthy play rate** | — | proxy: INT + fumbles per drive (noisier, less predictive) | **proxy** |
| Strength of schedule | CFBD | `/ratings/sp` (`sos`), internal ridge SOS | available |
| Returning production / starters | CFBD | `/player/returning` | available |
| Recruiting / composite talent | CFBD | `/talent`, `/recruiting/teams` | available |
| Transfer portal in/out | CFBD | `/player/portal` | available |
| QB experience / backup quality | CFBD | `/roster`, `/stats/player/season`, `/player/usage` | available |
| **Starting QB status this week** | Outlier (negative only) + ESPN depth chart | Outlier lists QBs *when injured* (5 rows / 120 teams); it cannot confirm a healthy starter | **partial — see §14.3** |
| Injury designations (Out / Doubtful / Questionable / Probable / Out for Season) | **Outlier `NCAAFB`** | `fetch_team_injuries`, structured `injury` object with `status`, `injury`, `returnDate`, `lastUpdated` | **available** (upgraded from `proxy` by the probe) |
| **Offensive-line injury / continuity** | — | Outlier injury feed carries **zero OL rows** (WR/RB/TE = 170 of 177) | **unavailable → `n/a`** |
| Coaching / continuity / coordinators | CFBD | `/coaches` + curated coordinator table | available (HC), proxy (coordinators) |
| Home/away/neutral, conference | CFBD | `/games` | available |
| Travel, timezone, altitude, surface, dome | CFBD | `/venues` + haversine + tz lookup | available |
| Rest, short week, bye | derived | from `games` schedule | available |
| Rivalry, lookahead, letdown, bowl/title stakes | curated + derived | static rivalry table; adjacency rules | available |
| Weather / wind / temp / precip | Open-Meteo | forecast API + ERA5 archive for backtests | available |
| Opening + current ML / spread / total | CFBD `/lines` (`spreadOpen`, `overUnderOpen`) + Outlier | multi-book | available |
| Consensus, best price, hold, implied prob | derived | devig across books | available |
| Line movement | derived | first vs latest `odds_snapshots` | available |
| **Reverse line movement (true)** | — | requires ticket%/money%, not freely available | **unavailable → `line_only` inference, labelled** |

`sources/cache.py` writes every HTTP response to `data/cache/` keyed by a URL+params hash with an ETag
and TTL, and exposes a `replay` mode so the entire test suite runs offline against recorded fixtures.
Rate limiting and backoff live in the `Source` protocol, not in callers.

---

## 5. Feature layer

`features/spec.py` holds the single registry. Each entry:

```python
FeatureDef(
    name="off_epa_pp_adj_diff",
    dtype=float,
    availability="weekly",           # preseason | weekly | pregame  <- leakage class
    tier=2,                          # 1..6, prior weight band
    sources=("cfbd:/ppa/teams",),
    null_policy="shrink_to_prior",   # never mean-impute a differential
    used_by=("safety", "totals"),
)
```

`availability` is the leakage class and it is **enforced, not documented**: `features/asof.py`
provides `AsOfReader(game)`, which raises `LeakageError` if any row it returns has
`as_of_utc >= game.kickoff_utc`, and refuses `weekly`/`pregame` features sourced from a
season-final aggregate. A test asserts every `FEATURE_SPEC` entry is reachable only through
`AsOfReader`. **This is the highest-risk area of the build** — a backtest that leaks looks excellent
and is worthless.

Tiers (starting prior weights, refit in Phase 4):

- **T1** `sp_rating_diff`, `ridge_rating_diff`, `elo_diff`, `hfa_venue`
- **T2** `off_epa_pp_adj_diff`, `def_epa_pp_adj_diff`, `success_rate_diff`, `explosiveness_diff`, `ppd_diff`, `finishing_drives_diff`, `early_down_sr_diff`
- **T3** `line_yards_diff`, `stuff_rate_diff`, `sack_rate_net`, `havoc_diff`, `qb_epa_pp_diff`, `qb_experience_diff`, `qb_status_penalty`, `backup_quality_gap`
- **T4** `talent_diff`, `returning_prod_diff`, `returning_starters_diff`, `portal_net_diff`, `hc_tenure_diff`, `new_coordinator_flag`
- **T5** `rest_diff`, `travel_km_away`, `tz_shift`, `altitude_delta`, `is_rivalry`, `lookahead`, `letdown`, `neutral_site`, `wind_kph`, `precip_mm`, `temp_c`, `dome`
- **T6 (V and M only, never S)** `spread_move`, `total_move`, `rlm_line_only`, `hold`, `n_books`, `price_staleness_min`

---

## 6. Model formulas

### 6.1 Opponent-adjusted ratings (ridge)

For metric `m`, one observation per team-side per game:

```
y_i   = mu + O[team(i)] - D[opp(i)] + h * home_i + eps_i
minimise  sum_i w_i (y_i - yhat_i)^2  +  lambda * (sum O^2 + sum D^2)
w_i   = exp(-delta_days_i / tau),   tau = 45
```

Solved by conjugate gradient on the normal equations in `models/linalg.py` (pure Python; the approach
`outlier/team_strength.py` already proved out). `lambda` chosen by walk-forward CV, seeded at 25 and
retuned for ~134 FBS teams.

**Early-season prior** — this is where CFB parlay hunting actually happens, so it matters most:

```
O_prior = a * O_prev_season_final + b * z(talent) + c * z(returning_ppa)
O_final = (n * O_fit + k * O_prior) / (n + k),    k ~ 4 team-games
```

`a, b, c, k` fitted on historical weeks 1–5 by minimising out-of-sample margin error.

### 6.2 Elo

```
E      = 1 / (1 + 10^(-(R_a - R_b + HFA) / 400))
K_eff  = K * ln(|margin| + 1) * (2.2 / (0.001 * (R_win - R_lose) + 2.2))
R'     = R + K_eff * (S - E)
preseason:  R_0 = 0.75 * R_final + 0.25 * 1500 + g * z(talent) + d * z(returning_prod)
```

`K` seeded at 25, `HFA` at 60 Elo (~2.6 pts), venue-specific with shrinkage toward the league mean by
games observed. FCS opponents enter as a single pooled synthetic team with a wide prior.

### 6.3 Margin to win probability

```
M       = (O_home - D_away) - (O_away - D_home) + HFA_venue + sum(context_adj)
sigma_M = sigma_0 + beta * (proj_total - mean_total)     # heteroskedastic; CFB sigma_0 ~ 16.5
P_home  = Phi(M / sigma_M)
```

### 6.4 Ensemble

Three members: `P_elo` (logistic on Elo diff), `P_ridge` (`Phi(M/sigma)`), `P_logit` (IRLS L2 logistic
on the full T1–T5 vector). Pooled in log-odds space:

```
logit(P_ens) = sum_k v_k * logit(P_k),    v on the simplex
```

`v` fitted by coordinate search minimising walk-forward out-of-sample log loss. No gradient boosting:
with ~800 FBS games/season and ~40 features, a GBM's variance exceeds its bias gain — the stdlib
constraint and the statistically correct choice coincide here.

### 6.5 Calibration

PAVA isotonic regression on out-of-sample predictions with linear interpolation between knots;
Platt scaling (single-parameter logistic on the logit) as the low-variance alternative.
**Selection rule: isotonic where the bucket has n >= 200, Platt otherwise.**

The 95%+ bucket will always be data-poor — it contains few losses by construction, which is exactly why
it is the bucket the parlay product depends on. Every bucketed win rate is therefore reported as a
**Wilson interval with n**, never a point estimate. "Does a stated 90% win 90% of the time" is answered
with a confidence interval or it is not answered.

### 6.6 Totals

```
pace_mult   = (pace_home_off / L) * (pace_away_def / L)     # four-factors style, per side
drives_team = L_drives * pace_mult                          # L_drives ~ 12.0 FBS
PPD_home    = PPD_off_home_adj + PPD_def_away_adj - PPD_league
proj_home   = drives_home * PPD_home
proj_total  = proj_home + proj_away
```

Then two script adjustments, both **fitted, not assumed**:

- **Clock drain:** if `|M| > 21`, `proj_total -= beta_drain * (|M| - 21)`.
- **Garbage-time add-back:** scaled by the underdog's offensive-viability percentile.
- **Weather:** `wind_kph` enters as a fitted coefficient estimated from ERA5-matched historical games.
  Folk wisdom overstates wind in CFB; if the coefficient is not significant out-of-sample, it is dropped.

`P(Over) = 1 - Phi((line - proj_total) / sigma_T)`, `sigma_T ~ 10.5–11`, with a discreteness correction
for scoring increments and explicit push handling on integer totals.

### 6.7 Mismatch score (descriptive index, 0–100)

Components A–N z-scored and clipped to +/-3:

```
MS = 100 * Phi( sum_j w_j * z_j / sqrt(sum_j w_j^2) )
```

Starting weights: quality gap .22, QB .14, trench .10, off eff .08, def eff .08, explosiveness .06,
depth .05, talent .07, coaching .04, HFA .06, injury .06, SOS-adj .04.

**The mismatch score is never used for sizing and is never treated as a probability.** It is a
human-readable explanation of *why* the model likes a side, and the component vector that drives
upset-path derivation.

### 6.8 Confidence (0–100, a data-quality score)

Starts at 100; deductions are explicit and additive:

| Condition | Deduction |
|---|---|
| QB status `unknown` | −18 |
| QB status `questionable` / `doubtful` | −10 |
| Availability data older than 48 h | −8 |
| Team has < 4 games this season | −15 (< 2 games: −25) |
| First-year head coach | −8 |
| New OC or DC | −4 each |
| Portal turnover above 60th pct | −5 |
| Weather forecast dispersion high | −6 |
| Odds older than 6 h | −7 |
| Single book only | −10 |
| Feature null rate `r` | −40 * r |

Floored at 0. Confidence gates tiers; it is never multiplied into a probability.

---

## 7. Parlay construction

### 7.1 Eligibility (configurable, recalibrated in Phase 4)

| Tier | Gate |
|---|---|
| CORE | `P_blend >= 0.90` **and** `confidence >= 85` **and** QB status in {confirmed, probable} **and** no critical injury cluster **and** no red-flag matchup **and** `edge >= 0` |
| SUPPORTING | `P_blend >= 0.85` **and** `confidence >= 78` |
| AVOID | anything below, or any upset-risk flag, or `edge < −0.02` (price materially worse than the model), or unresolved QB/injury state |

### 7.2 Correlation — a one-factor Gaussian copula, with rho *fitted*

Independence is wrong and pairwise-correlation guessing is unfalsifiable. Instead, one latent factor
`F ~ N(0,1)` represents "the model is systematically wrong today":

```
Z_i = sqrt(rho_i) * F + sqrt(1 - rho_i) * eps_i,    leg i wins iff Z_i < InvPhi(p_i)

P(all win) = integral over F of
             product_i Phi( (InvPhi(p_i) - sqrt(rho_i) * F) / sqrt(1 - rho_i) ) * phi(F) dF
```

Evaluated with 32-node Gauss–Hermite quadrature (nodes hardcoded — stdlib only).

```
rho_i = rho_0
      + rho_conf * [same conference cluster]
      + rho_wx   * [shared weather system]
      + rho_qb   * [unresolved QB situation]
      + rho_pub  * [public-favourite cluster]
```

**`rho_0` is fitted, not chosen.** Run every historical slate through the optimizer, compare realised
n-leg survival rates against the independence prediction, and solve for the rho that reconciles them.
If independence predicts 43% survival for 8-leg parlays and history delivers 36%, rho is the number
that explains the gap. That makes the correlation model a testable claim rather than a disclaimer.

### 7.3 Optimizer and the marginal-leg rejection rule

Beam search (width 50) over subset sizes 3–10 from the eligible pool, ranked by:
adjusted joint probability → average confidence → weakest-leg floor → price efficiency → risk-adjusted EV.

Leg `n+1` is **accepted only if all four hold**:

1. `log P_adj(n+1) - log P_adj(n) >= -theta`  (theta = configurable probability-decay budget)
2. `EV(n+1) > EV(n)` where `EV = P_adj * (decimal_price - 1) - (1 - P_adj)`
3. `p_{n+1} >= weakest_leg_floor`
4. the added rho contribution keeps the correlation penalty under its cap

Otherwise the search stops **and records why** in `parlays.rejected_leg_json`:

> `rejected: Team X (+$41 payout, −3.8 pp win probability, decay budget theta = 2.0 pp) — leg increases price but degrades the probability profile beyond budget.`

That printed rejection is the feature that distinguishes this from a payout maximiser.

### 7.4 Fragility

`upset_risk = f(1 - P_blend, flags)`. Primary and secondary upset paths come from a **deterministic
lookup table** keyed on which mismatch component is weakest and which opponent percentile is highest —
`{turnover_variance, explosive_pass, trench_collapse, qb_injury, environment, pace_shortening, letdown_spot}` —
so the reasoning is testable rather than generated prose.

An alternative parlay dropping the weakest leg is emitted automatically when
`p_weakest < mean(p_others) - 1.5 * sd` or `p_weakest < p_second_weakest - 0.04`.

---

## 8. Backtest harness

- **Walk-forward by week.** Train strictly on `as_of_utc < kickoff_utc`; predict week W; never refit forward.
- **Leakage guards are tests**, not conventions (see §5). Seasons 2014–2025, with 2020 (COVID schedule)
  excluded from fitting and retained as a stress slice.
- **Moneyline metrics:** Brier, log loss, reliability curve, win rate by the specified buckets
  (80–84.9 / 85–89.9 / 90–92.4 / 92.5–94.9 / 95+), each with a Wilson interval and n; ROI at realised
  prices; CLV in bps vs closing; upset frequency; simulated parlay survival by leg count.
- **Totals metrics:** MAE, RMSE, hit rate vs closing total, CLV, sliced by edge size, wind bucket,
  conference, and week-of-season.
- **Baselines the model must beat out-of-sample, or it is not promoted:**
  1. vig-free market probability, 2. SP+-only `Phi(M/sigma)`, 3. Elo-only.

---

## 9. Promotion gate (two-key, mirroring `outlier`)

`config/promotion.json`:

```json
{
  "min_settled_games": 1500,
  "min_seasons_backtested": 3,
  "max_abs_calibration_gap": 0.03,
  "min_bucket_n_for_gap_test": 100,
  "require_oos_logloss_beat_market": true,
  "require_median_clv_non_negative": true,
  "status": "shadow"
}
```

While `status` is `shadow`: probabilities are published, **no CORE tier is emitted**, no parlay is
presented as decision-grade, and every artifact is stamped
`UNPROMOTED — shadow output, not decision-grade`.

---

## 10. Reports (Sections A–G) and CLI

`report/sections.py` builds A–G exactly as specified; `render_md`, `render_csv`, `render_html` emit them.
Every cell without a source prints `n/a (no public source)`.

Language rule enforced by a unit test over rendered output: the tokens
*guaranteed, lock, safe, certain, sure thing, can't lose* must never appear.

```bash
python -m cfb_analytics backfill --seasons 2014-2025
python -m cfb_analytics ingest   --season 2026 --week 3
python -m cfb_analytics features --season 2026 --week 3
python -m cfb_analytics train    --through 2025
python -m cfb_analytics backtest --seasons 2016-2025 --report
python -m cfb_analytics slate    --date 2026-09-05 --format md,csv,html
python -m cfb_analytics parlay   --date 2026-09-05 --min-legs 3 --max-legs 10
python -m cfb_analytics settle   --season 2026 --week 3
```

---

## 11. Phases

| Phase | Deliverable | Size |
|---|---|---|
| 0 | Repo scaffold, `config`, `paths`, `db` migrations, CI (pytest + ruff + mypy) | S |
| 1 | `sources/` + cache/replay + `ingest/` + backfill 2014–2025 into SQLite | L |
| 2 | `FEATURE_SPEC`, `AsOfReader` leakage guard + tests, feature build | M–L |
| 3 | `linalg`, `ridge`, `elo`, `logistic`, `ensemble`, `calibrate` | L |
| **4** | **Backtest harness, metrics, baselines, promotion gate — first decision point** | **L** |
| 5 | Totals model + its own backtest slice | M–L |
| 6 | Mismatch, confidence, tiering, upset-path table | M |
| 7 | Correlation (rho fitted from history), optimizer, fragility | M–L |
| 8 | Sections A–G renderers | M |
| 9 | Settlement, CLV, live calibration loop, shadow-mode operations | M |

TDD throughout: tests first, >= 80% coverage, `code-reviewer` pass after each phase.

---

## 12. Risks

| Sev | Risk | Mitigation |
|---|---|---|
| **HIGH** | **Backtest leakage.** Season-aggregate stats leak future results; a leaked backtest looks excellent and is worthless. | `AsOfReader` raises on violation; `availability` class on every feature; leakage tests are blocking. |
| **HIGH** | **Confirming a healthy starting QB.** The probe upgraded *injury* data to `available`, but an injury feed is not a depth chart: absence from it does not confirm who starts. CORE depends on exactly this. | `unknown` QB status stays a hard CORE blocker. Outlier gives high-quality *negative* signal (QB listed Out/Questionable); ESPN depth charts are still required for the positive confirmation, and remain brittle. |
| **MED** | **No OL injury coverage** — the probe found zero OL rows in 177. Trench mismatch is a T3 feature and OL attrition is invisible to it. | `n/a (no public source)`; OL continuity falls back to preseason `returning_production.ol_continuity_starts` only, and the trench feature carries a widened prior. |
| **HIGH** | **Calibration in the 95%+ regime is data-poor** — and that is precisely where parlays live. | Wilson intervals on every bucket; Platt below n = 200; refuse CORE claims the sample cannot support. |
| **MED** | CFBD key, rate limits, terms of use; some endpoints are Patreon-tier. | Env-var key, disk cache, backoff; weather via Open-Meteo rather than the paid CFBD weather endpoint. |
| **MED** | ESPN unofficial endpoints break without notice. | Isolated behind the `Source` protocol; failure degrades confidence rather than crashing the run. |
| **MED** | ~800 FBS games/season is small data. | Regularisation, ensembling and priors over model complexity; no GBM. |
| **MED** | rho fitting needs many historical slates to be stable. | Fit on 8+ seasons; report rho's own interval; default to the conservative (higher-rho) end. |
| **LOW** | Pure-Python ridge over 12 seasons. | ~10k observations — conjugate gradient converges in well under a second. |

---

## 13. One honest expectation to set before building

Heavy CFB moneyline favourites are among the most efficiently priced markets available. At −2000 the
market implies 95.2%, and the vig-free number sits near 94%; a fundamentals model needs to be *better
calibrated than the closing line* to find real edge there. The historically likely finding is that
large-favourite ML parlays carry **negative** expected value at market prices even when every
individual leg is correctly assessed.

That does not make the system pointless — it makes it honest. Its expected value concentrates in:

1. **Totals**, materially less efficient than sides in CFB, especially early season and in wind.
2. **Telling you when not to bet** — Part 14's PASS, backed by a calibrated number rather than a hunch.
3. **Weak-link detection** — identifying the leg quietly carrying the entire parlay's risk.

If the Phase 4 backtest shows no moneyline edge, that is a successful result, and the pipeline should
say so plainly rather than manufacture a CORE tier to justify its own existence.

---

## 14. Pre-Phase-1 gate results (probed 2026-08-31, read-only)

### 14.1 Outlier API — **PASS, league token is `NCAAFB`**

Probed `api.outlier.bet` against ten candidate tokens using the existing authenticated session.
`NCAAF`, `CFB`, `FBS`, `NCAAFOOTBALL`, `COLLEGEFOOTBALL`, `NCAA_FB`, `NCAAF_FBS`, `CFP` all return
HTTP 502 (unknown league). `NCAAFB` returns **137 pregame events spanning 2026-09-03 → 2026-12-12**,
including **30 games on Saturday 2026-09-05**. `NFL` also resolves (260 events), which confirms 502
means "unknown league" rather than a transport failure. League-index endpoints
(`/sportsdata/leagues`, `/sports`) are 403 — enumeration is closed, so the token list stays curated.

Event shape: `eventId`, `home`/`away` (`teamId`, `name`, `alias`, `market`), `scheduledTime`,
`venue`, `network`, `season`, `status`, `timezone`, `dayOfWeek`.

### 14.2 Market depth — **usable, but coverage is time-varying and the sharp books did not persist**

Two measurements of the same endpoint, hours apart on 2026-08-31:

| | 23:27Z probe (10 games) | 01:53Z ingest (30 games, cache bypassed) |
|---|---|---|
| distinct books | 19–20 per event | 10–12 per event |
| sharp books (PS3838, CIRCA, BOOKMAKER, BETONLINE) | present | **absent** |
| offshore/exchange (BOVADA, BETUS, MYBOOKIE, BETFAIR) | present | absent |
| retail (DRAFTKINGS, FANDUEL, CAESARS, FANATICS, …) | present | present |

Re-probing the identical games (CLEM@LSU, BAY@AUB, MIA@CLEM, UNT@IND) with the HTTP cache bypassed
confirmed the second reading: 10–12 books, no sharp books, and the `books` coverage lists exactly
equal the set of books carrying prices — so this is not a parsing miss. **The cause is not
established**; candidates include line-posting timing, account entitlement, or per-book availability
windows. It is not diagnosed here and should not be guessed at.

**Follow-up survey (8 captures across 7 slates, 2026-09-01):** the sharp books did not reappear in
any capture, and a second capture of 2026-09-05 reproduced 12 books exactly. The original 20-book
reading has not replicated once and is treated as unreliable.

| slate | days to kickoff | games | books | prices/game | sharp |
|---|---|---|---|---|---|
| 2026-09-04 | 3 | 5 | 12 | 859 | NONE |
| 2026-09-05 | 4 | 30 | 12 | 628 | NONE |
| 2026-09-05 (2nd capture) | 4 | 30 | 12 | 630 | NONE |
| 2026-09-06 | 5 | 3 | 11 | 613 | NONE |
| 2026-09-11 | 10 | 2 | 8 | 43 | NONE |
| 2026-09-12 | 11 | 42 | 9 | 10 | NONE |
| 2026-09-19 | 18 | 6 | 7 | 24 | NONE |
| 2026-09-26 | 25 | 3 | 1 | 22 | NONE |

### 14.2c The operating window: coverage collapses beyond ~5 days

Per-slate book *totals* badly overstate usable coverage, in the same way the slate-count did in
§14.2b. Moneyline coverage per **game** (median books/game):

| slate | days out | games | median ML books/game | games below the 3-book consensus floor |
|---|---|---|---|---|
| 2026-09-04 | 3 | 5 | 12 | 0 |
| 2026-09-05 | 4 | 30 | 12 | 0 |
| 2026-09-06 | 5 | 3 | 11 | 0 |
| 2026-09-11 | 10 | 2 | 8 | 1 of 2 |
| 2026-09-12 | 11 | 42 | **1** | **36 of 42** |
| 2026-09-19 | 18 | 6 | 2 | 4 of 6 |
| 2026-09-26 | 25 | 3 | 1 | 3 of 3 |

2026-09-12 reports 9 distinct books at slate level while its *median game* has one — the books
concentrate on a few marquee games. Every game has *some* moneyline, so a naive "is it priced?"
check passes on all 42 while 36 are unusable.

**Operating rule: generate slate reports within ~5 days of kickoff.** Beyond that the market is not
posted and `min_books_for_consensus = 3` correctly rejects most of the slate. This is a property of
the market, not a defect to engineer around — a 25-day-out line from one book is not a market
consensus and must never be devigged as though it were.

Consequences, all already implemented:

* The sharp-anchor devig in §3.3 is downgraded from a design assumption to a **conditional rule with
  an explicit fallback chain** and a `no_sharp_anchor` flag.
* `cfb-analytics coverage` records books-per-capture, days-to-kickoff, prices/game and sharp-book
  presence for every ingest, so the question stays settled by accumulated measurement.
* Outlier remains the **primary live market source** inside the operating window — 12 books and
  ~19k prices for a 30-game slate is ample for consensus devig — but **CLV must be measured against
  best-available price, not a Pinnacle close**, unless sharp books reappear. CFBD `/lines` remains
  the historical-backfill source; Outlier carries no history.

Gameline propositions observed: `MONEYLINE`, `SPREAD`, `TOTAL` (ingested), plus
`MONEYLINE_THREE_WAY`, `DOUBLE_RESULT`, `WINNING_MARGIN` (out of scope, skipped).

### 14.2a Two parsing traps, both verified and both regression-tested

1. **`outcomes[].books` is not parallel to `outcomes[].odds`.** In a sampled moneyline market
   `books[0]` was `FLIFF` while `odds[0]["book"]` was `FANATICS`. Index-zipping mis-attributes every
   price to the wrong book, silently. Each odds entry names its own book and is read that way.
2. **A proposition spans several market rows per event** (~3 `MONEYLINE`, ~5.6 `SPREAD`), each with a
   different book subset. Reading one row samples 3 books and calls it the market; consensus unions
   all rows and de-duplicates on `(book, side, line)`.

### 14.2b Slate date is the US Eastern date, not the UTC date

`scheduledTime` is UTC (`+00:00` on all 137 events) and `dayOfWeek` is numeric (Mon=0 … Sun=6;
Saturday=5 on 119 events). Grouping the slate by UTC date is wrong **in both directions** for the
2026-09-05 Saturday slate:

* wrongly **includes** 4 Friday-night games kicking 00:00–01:00Z on the 5th (UTEP@OKLA, TOL@MSU,
  FRES@USC, MIA@STAN);
* wrongly **excludes** 4 Saturday-night West Coast games kicking 02:00–02:30Z on the 6th (CMU@UNM,
  UNLV@HAW, WKU@NEV, UCLA@CAL).

8 of a 34-game slate misassigned — and because it loses four and gains four, **the game count is
unchanged at 30, so the error is invisible by count**. The slate is therefore defined by
`utils.football_date` (US Eastern calendar date), which reproduces the feed's own `dayOfWeek` exactly;
that agreement is asserted per event and any mismatch is surfaced in the ingest summary.

### 14.3 Injuries — **upgraded to `available`, with two documented gaps**

`fetch_team_injuries("NCAAFB", team_id)` returned **177 structured rows across 120 teams**.
Row schema: `playerId, firstName, lastName, jerseyNumber, position, injury{status, injury,
startDate, returnDate, lastUpdated, hasNews, shortText, headline, analysis}`.

- `status`: Questionable 89, Probable 42, Out 18, Doubtful 14, Out for Season 14
- `injury`: Undisclosed 99, then Knee 12, Ankle 8, Leg 8, Lower Body 7, …
- `lastUpdated` is populated on every row → the §6.8 staleness deduction runs on real timestamps.

This is a genuine improvement over the assumption that no usable CFB injury data exists. **Two gaps
survive and both are load-bearing:**

1. **It is an injury feed, not a depth chart.** QBs do appear (5 rows), so a QB listed
   Out/Questionable is trustworthy *negative* signal — but absence from the feed does not confirm a
   healthy starter. `qb_status = 'confirmed'` still requires ESPN, so **the hard CORE blocker stands
   unchanged.**
2. **Zero offensive-line rows** (WR 78, RB 57, TE 35, QB 5, K 2 of 177). OL attrition is invisible;
   `n/a (no public source)` per §4.

### 14.4 CollegeFootballData — **BLOCKED, no key configured**

Checked Process, User and Machine environment scopes for `CFBD_API_KEY`, `CFBD_KEY`,
`COLLEGE_FOOTBALL_DATA_API_KEY`, `COLLEGEFOOTBALLDATA_API_KEY`, `CFB_API_KEY`, `CFBD_BEARER_TOKEN`
— all missing. The canonical `.env` holds only `OUTLIER_*` and the three model-provider keys; no
CFBD-shaped entry exists. No value was read or printed at any point.

**Required: `CFBD_API_KEY`** (free at collegefootballdata.com/key), set as a user environment
variable or added to `C:\Users\dasil\Dev\GitHub\outlier\.env`. Phase 1 ingestion, the 2014–2025
backfill, and every fundamentals feature are blocked on it. Phases 0 and the Outlier-only slice of
Phase 1 can proceed without it.
