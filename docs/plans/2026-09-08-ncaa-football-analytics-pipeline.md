# NCAA College Football Analytics Pipeline

Standalone module under `outlier_scrapers/ncaa/`. Two products: high-probability
moneyline parlays of 3-10 legs for a target Saturday slate, and game-total
Over/Under analysis. It shares this repository's conventions (stdlib-only model
primitives, fail-closed on missing data, promotion-gated calibration) and none
of its MLB/WNBA code paths.

## Outcome

A quantitative decision engine, not a picks generator. It answers two separate
questions and never blends them into one number:

1. Which favorites are genuinely hard to upset, and which combination of them
   produces the strongest probability-adjusted moneyline parlay?
2. Which game totals look mispriced relative to pace, efficiency, matchup,
   game script, weather, and roster availability?

## The architectural decision that shapes everything

**Three independent models, not one score.**

| Model | Question | Sees the moneyline? |
|---|---|---|
| `safety` | Who is least likely to lose? | **No** |
| `value` | Is the price worth paying? | Yes |
| `totals` | Is the market's number wrong? | Total price only |

Conflating the first two is the standard way a betting model goes wrong. A 94%
favorite priced at 97% is an excellent prediction and a poor wager; a single
blended rating cannot express that. `safety` is therefore structurally unable
to see the moneyline -- it is not passed in -- so the edge `value` computes is
a real comparison rather than a restatement of the price.

## Stage graph

```
raw inputs
  -> roster / injury layer          features.RosterStatus
  -> team efficiency ratings        features.TeamEfficiency
  -> matchup features               features.build_matchup_features
  -> market data                    features.MarketQuote
  -> win probability                safety.estimate_win_probability
  -> probability calibration        calibrate.Calibrator   (promotion-gated)
  -> candidate filtering            tiers.assign_tier
  -> parlay optimizer               parlay.build_parlay_ladder
  -> totals model                   totals.project_total   (parallel branch)
  -> reporting / backtesting        report.py / backtest.py
```

`totals` hangs off the feature layer rather than the moneyline chain: it
consumes the same inputs but shares none of the moneyline machinery, so a
change to tiering cannot silently move a totals number.

Calibration enters at exactly one point, before `value` and `tiers`, so every
downstream consumer sees the same probability and a calibrated number can never
be compared against an uncalibrated one.

## Module map

| Module | Responsibility |
|---|---|
| `stats.py` | Normal CDF/quantile, logit/expit, Gauss-Hermite quadrature, rank-1 factor fit |
| `odds.py` | American/decimal conversion, four devig methods, EV, Kelly, Kelly growth |
| `config.py` | Every threshold in the spec, strictly loaded from `config/ncaa_pipeline.json` |
| `features.py` | ~180-field input model and favorite-relative matchup differentials |
| `mismatch.py` | Team Mismatch Score 0-100 (spec components A-N) |
| `safety.py` | Win probability: margin -> normal CDF -> spread blend -> log-odds penalties |
| `value.py` | Devigged market comparison, two edges, EV, Kelly, CLV |
| `tiers.py` | CORE / SUPPORTING / AVOID admission |
| `parlay.py` | Correlation model, copula joint probability, optimizer, fragility |
| `totals.py` | Possessions x points-per-drive, archetypes, Over/Under/PASS |
| `calibrate.py` | Platt and isotonic, applied only on demonstrated holdout gain |
| `backtest.py` | Brier, log loss, calibration curve, ROI, CLV, parlay survival |
| `report.py` | Sections A-G, plus mechanical enforcement of the risk-language rule |
| `pipeline.py` | Slate orchestration |
| `sources.py` | Adapter protocols and the data-source mapping. No network code. |

## Model formulas

### Win probability (`safety`)

```
margin  = quality_gap
        + home_field_points            (home side, skipped at neutral sites)
        + travel_miles/1000 * 0.4      (away side pays)
        + |tz_shift| * 0.3             (away side pays)
        + clamp(rest_edge * 0.15, +/-1.5)
        + altitude_points              (home side, >= 4000 ft)

p_fundamental = Phi(margin / margin_sigma)            margin_sigma = 16.0
p_spread      = Phi(-favorite_relative_spread / margin_sigma)
p_blended     = expit((1-w)*logit(p_fund) + w*logit(p_spread))    w = 0.55
p_model       = expit(logit(p_blended) - sum(penalties))
```

Blending happens in log-odds space; a linear blend of two probabilities near
0.98 distorts badly. Penalties are subtracted in log-odds so the same
qualitative worry costs an 85% favorite more probability than a 97% one, which
is the correct shape.

Only the **spread** enters, never the moneyline. `p_fundamental` is always
reported separately so `value` can compute a fully market-free edge, and the
two edges are compared: a large divergence means the apparent edge comes from
the spread-vs-moneyline relationship rather than model skill, and is flagged.

**One spec item is deliberately not a probability penalty.** "Inflated prices
unsupported by matchup fundamentals" is raised as a flag and priced in `value`.
Docking a team's win probability because its price is long would feed the
market back into the model it is meant to be measured against.

### Mismatch Score

Fourteen components, each mapped to 0-100 by `100 * expit(diff / scale)` with
50 neutral, then a weighted mean over *available* components only, renormalized
by observed weight. Below 60% observed weight the game is left unscored rather
than scored on a fragment. Components clearing 72 are reported as
`structural_advantages`, which is what distinguishes a structurally dominant
favorite from a merely highly-ranked one.

### Totals

```
drives   = clamp(12.0 + 0.9 * mean(tempo_z), 8, 15)
ppd      = clamp(league * (off/league)^a * (allowed/league)^a, 0.4, 4.5)   a = 0.75
total    = drives * (ppd_home + ppd_away) + environmental + game_script
p_over   = 1 - Phi((line - total) / total_sigma)                total_sigma = 13.5
```

The damping exponent matters. The undamped product (`a = 1`) is the textbook
log-additive adjustment and it over-extrapolates badly: an elite offense against
a poor defense projected 4.64 points per drive and a 120-point game total in
testing. `a = 0.75` plus physical bounds brings that to a plausible range while
leaving the league-average anchor exact (average vs average projects 49.2,
which is where FBS totals sit -- this is the regression test that catches
adjustment drift).

Red-zone efficiency and finishing drives are **not** applied as a separate
multiplier. Points per drive already contains them; a red-zone term on top
would count the same skill twice. Red-zone strength appears as an archetype
driver instead.

Integer market totals get an explicit push probability from the `+/-0.5` ladder
(continuity correction), consistent with the MLB house rule, rather than being
treated as two-way.

### Parlay optimizer

**Correlation, with the sign stated correctly.** For the event "every leg
wins", positive correlation makes the parlay **more** likely than the naive
product, not less -- in the limit of perfect correlation the parlay wins
whenever its weakest leg does. So `p_correlated >= p_independent` always, and
the difference is reported as `correlation_lift`. This is not good news to
bank: correlation concentrates risk, fattening both tails and removing the
diversification that makes a sequence of independent edges survivable.
`correlated_risk_tags` names the shared exposures so the concentration stays
visible even though the headline number moved up.

Pairwise correlations are assembled from shared risk tags (weather system,
conference, kickoff window, injury-news regime, shared data source, same game),
compressed to one common factor by a principal-factor rank-1 fit, and
integrated as a one-factor Gaussian copula:

```
z_i = Phi^-1(p_i),  V_i = a_i*M + sqrt(1-a_i^2)*e_i,  leg i wins iff V_i <= z_i
P(all win) = Integral phi(m) * Product_i Phi((z_i - a_i*m)/sqrt(1-a_i^2)) dm
```

evaluated by 24-node Gauss-Hermite quadrature.

**Model risk is handled separately, and not as correlation.** The real hazard
in a multi-leg parlay is that one model produced every probability and is
overconfident. That is a level shift affecting all legs at once, not an outcome
correlation, and routing it through the copula would *raise* the estimate --
exactly backwards. It is applied as a log-odds haircut, and every parlay reports
`p_stressed`: what the ticket is worth if each leg is slightly overconfident.

**The leg-rejection rule.** The default objective is expected log growth at the
Kelly stake, not EV. EV is linear in the payout, so maximizing it will bolt on
a ninth leg purely to lengthen the price; log growth is concave, so a leg must
genuinely improve the probability-adjusted position. On top of that, a
candidate leg is rejected when:

1. its upset risk exceeds `max_leg_upset_risk`;
2. the parlay would fall below the leg-count probability floor;
3. it costs more than `max_probability_decay_per_leg` of the parlay's win
   probability (the binding rule in practice);
4. parlay EV would fall below `min_parlay_ev`;
5. it lowers the objective at all.

Rule 3 is also a **feasibility** condition, not just advice: every leg already
in a parlay must survive removal-and-readd. Without that the ladder could
return an eight-leg parlay containing a leg the marginal rule would have
refused, and the rejection rule would be decorative. Rejections are recorded on
the parlay with their reason and the price the ticket would have paid, which is
the auditable form of "this leg increased the payout but damaged the
probability profile too much".

Search is exhaustive while `C(pool, k) <= 200_000` and a deterministic beam
search of width 24 beyond that.

**Keep `max_probability_decay_per_leg` aligned with `core_min_win_prob`.** They
express the same judgement at different levels and the parlay bound is the
binding one. A 0.06 decay limit alongside a 0.90 CORE floor silently makes
every 90-93% leg unusable, so the tier admits legs the optimizer can never
place -- this was caught in testing and the defaults now satisfy
`decay ~= 1 - core_min_win_prob`.

## Data-source mapping

`sources.py` defines protocols; no adapter implementation lives in this
package, because vendor response shapes change far more often than the model
does.

| Block | Supplier | Protocol |
|---|---|---|
| Schedule / results | season schedule endpoint by season+week | `ScheduleSource` |
| Efficiency ratings | advanced-stats provider (EPA, success rate, explosiveness, points/drive, havoc, opponent-adjusted) | `EfficiencySource` |
| Roster / injuries | depth charts plus availability report | `RosterSource` |
| Recruiting / talent | composite roster-talent rating | `RosterSource` |
| Coaching | curated table -- no public feed grades staffs | `CoachingSource` |
| Weather | forecast API keyed by venue and kickoff | `ContextSource` |
| Market | odds feed: open, current, best, closing for ML/spread/total | `MarketSource` |

Two rules every adapter must follow:

1. **Return `None` for anything not observed.** A league average substituted
   for a missing field is indistinguishable from real data once it reaches the
   model, and would raise confidence on exactly the games the pipeline knows
   least about.
2. **Never let the betting line become a model input.** An efficiency rating
   fitted against the spread reproduces the spread, and the edge it appears to
   find is an artifact.

## Missing-data policy

Every field is `| None`; nothing is imputed. Two coverage measures are carried:

- **Critical coverage** over 17 foundational fields (power rating, offensive
  and defensive EPA, games played, quarterback status, players out, injury
  report completeness, per side; plus spread and both moneylines). This is the
  dominant confidence term.
- **Total coverage** over the full ~180-field surface, weighted lightly.

Separating them was a correction found in testing: penalising confidence on raw
field count made every game fall short of the CORE confidence floor, because no
real feed populates 180 fields. Missing
`garbage_time_scoring_tendency` should not cost what missing a starting
quarterback costs.

Fail-closed behaviour, by model: `mismatch` returns an unscored game below 60%
observed weight; `safety` returns `model_prob = None` when neither ratings nor a
spread exist, and flags `fundamental_unavailable` (a CORE blocker) when it has
only the spread; `value` refuses to devig a one-sided quote; `totals` returns
PASS without points-per-drive data; the optimizer returns `{}` rather than
forcing a ticket.

## Backtesting framework

`backtest.py` measures, for moneylines: Brier, log loss, the calibration curve
over the spec's buckets (80-84.9, 85-89.9, 90-92.4, 92.5-94.9, 95+), win rate,
ROI, mean CLV, CLV beat rate, upset frequency, and arbitrary segment
breakdowns. For totals: MAE, RMSE, win rate, ROI, push count, and segments by
weather, conference, or season period. `parlay_survival` reports realised hit
rate by leg count against what was estimated.

Three refusals, all of them ways backtests flatter themselves:

- A bucket below `min_bucket_sample` (default 50) is marked
  `sufficient_sample = False`. A 3-for-3 record in the 95%+ bucket is not
  evidence, and printing "100%" next to it invites the wrong conclusion.
- CLV is reported separately from realised ROI. Over any sample a betting
  operation can plausibly collect, CLV is the lower-variance estimate of whether
  the edge is real.
- Pushes are excluded from the win rate rather than counted as half-wins, and
  the push count is stated.

`calibrate.fit_calibrator` fits Platt and isotonic on a **chronological**
holdout -- randomly splitting time-ordered betting data leaks future information
and reliably overstates calibration gains -- and returns the identity map unless
the holdout log loss improves by `min_improvement`. Verified in tests: it
recovers a known 0.7 logit-slope overconfidence, and declines to touch an
already-calibrated model.

## Risk-language enforcement

Spec Part 14 forbids describing any wager as guaranteed, safe, a lock, or
certain. `report.certainty_language_violations` scans rendered output for that
vocabulary and `render_slate_report` raises `CertaintyLanguageError` rather than
returning a report that trips it. The patterns are word-boundary anchored, so
ordinary football language ("lockdown corner", "free safety") is unaffected. A
rule that lives only in a style guide eventually gets broken by a template
change; this one fails loudly.

## What is NOT validated

Every numeric constant in `config.py` and the archetype thresholds in
`totals.py` are **priors, not fitted values**. Live web access was disabled in
the session that built this, so no historical NCAA data was ingested and no
backtest has been run. Specifically unvalidated:

- `margin_sigma = 16.0` -- sets how fast probability saturates; the single most
  consequential number in the moneyline model.
- `market_spread_weight = 0.55` -- should be fitted the way
  `probability_blend.py` fits the MLB market/model weight.
- `opponent_adjustment_damping = 0.75` and `total_sigma = 13.5`.
- Every penalty magnitude in `PenaltyConfig`.
- Every tier threshold, which the spec itself calls a starting framework.
- Correlation tag weights in `ParlayConfig.correlation_tags`.

The pipeline is therefore **shadow-only** until backtested. It writes no
`model_prob`, `edge_pct`, or sizing into any existing pack, and nothing in it is
wired into `daily_job`, `refresh`, or the desk runners.

## Promotion path

Mirrors the two-key pattern `probability_blend` and `team_strength` already use.

1. **Phase 1 (implemented).** Models, optimizer, reporting, and the validation
   harness. Deterministic, offline, 59 tests.
2. **Phase 2.** Write adapters against the `sources.py` protocols; collect
   dated slate snapshots and settle them. No recommendations.
3. **Phase 3.** Run `backtest.backtest_moneylines` and `backtest_totals` over
   multiple seasons. Recalibrate `margin_sigma`, `market_spread_weight`, the
   damping exponent, `total_sigma`, and the tier thresholds against the
   measured calibration curve.
4. **Phase 4.** Fit `calibrate.fit_calibrator` on a chronological holdout and
   promote only on demonstrated log-loss improvement, gated by a
   `config/ncaa_promotion.json` artifact with a sample-size floor, as
   `fundamentals_promotion.json` does.
5. **Phase 5.** Only then consider wiring output into the pack/portfolio path.

## House-rule compliance

- No reasoning-model or provider call anywhere in the package; nothing imports
  `openai`, `anthropic`, or `google-genai`.
- Stdlib-only model primitives, matching `projections.py` and
  `team_strength.py`. No numpy.
- The MLB/WNBA prop whitelists and side restrictions are untouched; this module
  shares no code path with them.
- `verdicts/`, `PackIndex`, and the desk snapshot contract are not modified.
