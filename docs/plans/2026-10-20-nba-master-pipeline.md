# Implementation Plan: NBA Master Pipeline (`outlier_nba`)

**Status:** PLAN ONLY — no production code changed. Planned 2026-10-20 on branch
`claude/nba-master-pipeline-fusion-cc1443` (worktree `frosty-mclaren-fd6326`, HEAD `9cf0a31`;
`origin/master` is `a1cbcc4`, the 7 missing commits are NFL legacy-archive fixes only and do not
touch anything this plan depends on).

**Planning mode:** Claude-only. `/multi-plan` asks for Codex + Gemini analysis through
`~/.claude/bin/codeagent-wrapper`; that wrapper (and `~/.claude/.ccg/prompts/`) is not installed on
this machine, so no external-model sessions exist. Evidence comes from five read-only code audits
(NCAA, NFL, MLB + shared core, WNBA, existing NBA assets) run in this session.

**User decisions recorded (2026-10-20):**
- New package `outlier_nba/` (not another sport inside `outlier_scrapers`).
- Scope: moneyline, spreads, game totals, team totals, player props, alt lines, player-prop alt
  ladders, cross-game parlays, same-game parlays (SGP), live betting, player-prop correlations.
- Player-prop projection layers requested: minutes, usage, matchups, pace, rest, injuries,
  home/away, recent form, strength of schedule, regular season vs playoffs, opponent adjustments,
  uncertainty, matchup history, referees, coaching tendencies, lineups, defensive matchups, shot
  profiles, rebound chances, assist chances, free-throw attempts, turnovers, steals & blocks,
  three-pointers made, PRA-family combos.

---

## 0. Executive summary

1. **What the four pipelines actually prove.** Across all four, the only component with
   out-of-sample evidence of beating anything is the NCAA moneyline ensemble (Brier 0.1803 vs CFBD
   Elo 0.1839, n = 7,853 — but with in-sample σ and weights, a calibration gap of 0.044 that fails
   its own 0.03 gate, and no market comparison). In the Outlier shared core, **the de-vigged market
   beats every independent model it has been tested against** (market Brier 0.2418 n=22,302 vs
   independent model 0.2520 n=823; blend holdout `improved: false` in every segment; market beats
   L10 totals, paired CI [0.0144, 0.0330], n=1,225). NFL's one positive slate (Brier 0.205 vs
   market 0.209, n=236) reversed on the next (0.214 vs 0.136, n=29). WNBA has no model in
   production at all. `nba-props-pipeline` has 221 in-sample playoff rows with 4 of 6 features
   constant.
2. **Therefore the NBA design is market-anchored.** The market's vig-free consensus is the
   baseline every NBA component must beat, out of sample, before it can move a stake. The model
   starts at blend weight 0 and earns weight through a coded two-key promotion gate.
3. **What is genuinely strong and reusable** is infrastructure, not models: NFL's as-of run
   context, stage receipts, immutable run bundles, fail-closed injury admission and CLV-timing
   discipline; NCAA's devig suite, market consensus, leakage guard, ridge/Elo/ensemble machinery,
   walk-forward harness and promotion-gate *design*; the shared core's Outlier client, feedback
   ledger, settlement, sizing, portfolio caps and push-aware totals ladder; WNBA's basketball market
   aliases, NB2 distribution code, results mapping and ESPN adapters.
4. **Core architecture:** one joint game simulation (possessions → team efficiency → margin/total →
   player minutes → usage → stat lines) prices every market. That is the only way the requested
   scope (spreads, totals, team totals, props, alt ladders, combos, correlations, SGP) stays
   internally consistent.
5. **Timing matters.** The 2026-27 NBA regular season is starting now (verify the exact opening
   date). Historical NBA *prop* odds cannot be backfilled from any source found, so prop promotion
   evidence can only accumulate prospectively. **Phase 1 (shadow capture into the ledger) should
   ship first and fast**, before any model work, so the season's market data is not lost.

---

## 1. Inventory: where the four pipelines live

| Pipeline | Location | Sport | State |
|---|---|---|---|
| NCAA | `C:\Users\dasil\Dev\GitHub\cfb-analytics` (`cfb_analytics/`), design `outlier/docs/plans/2026-08-31-ncaaf-analytics-pipeline.md` | **NCAA FBS football** — there is no college *basketball* pipeline | Shadow. HEAD `fb6ffb3` on unpushed local branch `fix/settlement-audit-week2-4`; 15 uncommitted files |
| NFL | `outlier/outlier_nfl/` + `scripts/nfl_*` | NFL | Live publishing (props, best bets, alt floors, matchup rankings) |
| MLB | `outlier/outlier_scrapers/` (shared core + MLB branches) | MLB | Live; market-devig sizing; SO model shadow |
| WNBA | `outlier/outlier_scrapers/` (WNBA branches) | WNBA | Live; market-devig sizing; projection audit-only |
| Existing NBA (reference only) | `nba-props-pipeline`, `Sports_Analytics` repos; NBA stub at `outlier_scrapers/registry.py:339-346` | NBA | Not to be imported (house rule). Audited for ideas only |

**Flag (non-negotiable rule "do not treat NCAA and NBA as interchangeable"):** the NCAA source is
football. Nothing statistical transfers from it to basketball; only sport-agnostic machinery
(devig, consensus, leakage guard, ridge/Elo/logit/ensemble fitting, walk-forward harness, promotion
gate design) is adopted, and every constant is re-fit on NBA data.

---

## 2. Phase 1 deliverable — Pipeline Comparison / Fusion Matrix

Legend — **Decision:** RETAIN (use as is / import), ADAPT (port and re-fit), COMBINE (merge with a
peer), EXCLUDE (with reason). **Evidence:** V = validated OOS somewhere, W = weak/in-sample/anecdotal,
N = none.

### 2.1 Run harness, data integrity, publication

| Component | Source (file:line) | Ev. | Decision | NBA adaptation |
|---|---|---|---|---|
| As-of run context, run modes (live/replay/retrospective/fixture), only live+fixture publish | NFL `run_context.py:69,151,170-179` | N (mechanics tested: `test_nfl_asof_leakage` 12 tests) | ADAPT | Cutoff is a timestamp, not a week. `before_week` → `as_of_utc`; game-date cutoffs |
| Stage receipts + publication gate (`gate_reason`, `withhold_publication`) | NFL `stage_receipts.py:40,108,117`; `run_writer.py:48,73` | N (13+12 tests) | ADAPT | **Fix NFL finding B:** every stage's FAILED receipt must re-gate publication (NFL appends rankings FAILED at `pipeline.py:819` *after* the publish decision) |
| Immutable run bundle, sha256 manifest, pointer written last | NFL `run_writer.py:1-14` | N | ADAPT | Same |
| Season-phase gate | NFL `season_phase.py:97` | N | ADAPT | Phases: PRE (never publish), REG, CUP, PLAYIN, PO_R1..FINALS |
| As-of reader (raise on stamp ≥ tip, drop late rows) | NCAA `features/asof.py:52,71,96` | N (21 tests) | COMBINE with NFL run context | One `AsOfReader` keyed on `as_of_utc` |
| Feed health (max source age, coverage %, row-scoped integrity flag) | Shared `feed_health.py:16-18,1026` | N | RETAIN (import) | Thresholds per NBA source |
| Strict pack date / post-start drop | Shared `pack.py:358,516` | N | RETAIN pattern | Tip time from schedule |
| HTTP cache + live/refresh/replay modes | NCAA `sources/http.py:42,51` | N | ADAPT | Needed for reproducible NBA backtests |
| Degrade-don't-abort daily job | NCAA `daily.py:609` | N | ADAPT | Non-required stages degrade; required stages withhold |

### 2.2 Market / odds layer

| Component | Source | Ev. | Decision | NBA adaptation |
|---|---|---|---|---|
| Outlier REST client, auth, pagination guard | Shared `api.py`, `auth.py`; NFL `api.py` (`IncompletePaginationError`) | N | RETAIN (import `outlier_scrapers.api`) | League `NBA`; endpoints previously used by `nba-props-pipeline` (`/sportsdata/leagues/NBA/playerProps`, `/schedule`) — **re-verify live** |
| Basketball market aliases (PTS…DD/TD, ML/SPREAD/TOTAL) | Shared `registry.py:206-258` | N | RETAIN | Already NBA-ready |
| NBA SportConfig stub | Shared `registry.py:339-346` | — | ADAPT | Fill 30 team aliases + display names; set `opp_rank_applicable=False` (API returns none); update `tests/test_registry_paths.py:18-23` |
| Devig methods: multiplicative, Shin (with arb fallback), power, odds-ratio | NCAA `models/devig.py:115-238` (75 tests) | N (math) | ADAPT (port, stdlib) | Note: additive == Shin for 2-way markets (memory), keep excluded |
| Provider devig priority (Outlier `calculatedEV`, AVERAGE first) | Shared `line_movement.py:43-49` | N | COMBINE | Use provider devig as one input; local consensus as cross-check |
| Market consensus: per-book devig → median → renormalise; sharp anchor ≥2 sharp books; `thin_market` <3 books; placeholder drop [0.0005, 0.995] | NCAA `features/market.py:145-306` | N | ADAPT | Sharp list to verify for NBA books available via Outlier |
| Balanced consensus line from ladder (both sides in [−220,+180], min |o+110|+|u+110|) | NFL `consensus.py:21,72-74` | N | ADAPT | Props main-line selection |
| Totals ladder devig + integer-line push from adjacent half-lines | Shared `game_totals.py:437-516` | N | RETAIN | Game totals, team totals, prop ladders |
| Line movement / snapshots (append-only, as-of filtered) | NFL `snapshots.py:79,137,168`; NCAA `market.py:402` | N | COMBINE | One snapshot store per market key |
| CLV timing policy: close must be captured ≤60 min pre-tip; refuse untimed close | NFL `fetch_odds_close.py:164-196`; `enrich_close.py:346-371` | N | ADAPT | **Do not repeat NFL's snapshot-as-close (CLV ≡ 0 by construction)** |
| CLV repair (bogus close == take) | Shared `feedback_settlement.py:232-305` | N | RETAIN | — |

### 2.3 Team-level models

| Component | Source | Ev. | Decision | NBA adaptation |
|---|---|---|---|---|
| Ridge off/def: `pts = μ + h·home + O_t − D_o`, time-decay weights, shrink to preseason prior | NCAA `models/ridge.py:63-216`, `shrinkage.py:41-44`; shared `team_strength.py:122-213` (unwired) | **V-ish** (NCAA ridge Brier 0.1869, n=7,853, football) | ADAPT | Per-100-possession target (pace removed); λ, τ, k re-fit by NBA walk-forward |
| Elo with MOV multiplier, preseason regression | NCAA `models/elo.py:129-139` | V-ish (internal Elo 0.1851) | ADAPT | Use published 538 NBA form as starting structure (K, MOV, HFA, carryover all re-fit); no FCS pool |
| Features logit (IRLS, L2) | NCAA `models/logistic.py:89`, `features/ensemble.py:67` | W (0.2036, worst member) | ADAPT | Rest/travel/availability features |
| Logit-pooled ensemble with simplex-grid weights | NCAA `models/ensemble.py:25`, `backtest/ensemble_fit.py:43` | V-ish but **in-sample weights** | ADAPT | **Fix:** nested walk-forward — weights fit only on prior seasons |
| Margin → probability Φ(M/σ) | NCAA `backtest/calibration.py:23,35` | W (σ in-sample; σ inconsistent 13.5/14.5/16.5 vs fitted 19.95 across modules) | REPLACE | NBA margin comes from the joint simulation; one fitted σ source |
| Pace × efficiency Monte Carlo for totals | `nba-props-pipeline/analysis/ctg_game_totals.py:307-433` | N | ADAPT (clean-room) | **Drop the 90/95 floors** (bias totals upward); efficiency from ridge, not CTG |
| Weather | NCAA, NFL | — | EXCLUDE | Indoor sport |
| NFL matchup script engine (trench/dome/pass-rush) | NFL `matchup.py` | W (signals 33-44% vs consensus) | EXCLUDE | Football-specific |

### 2.4 Player-level models

| Component | Source | Ev. | Decision | NBA adaptation |
|---|---|---|---|---|
| Minutes × per-minute rate × components | WNBA `projections.py:351-359,1101-1155,1402` | N (audit-only; no shrinkage; zero-minute games drag mean; ≤48-min filter would drop NBA OT) | ADAPT (rebuild) | Real minutes model (§5 M6); shrinkage; OT-safe |
| NB2 distribution + push-aware win/push/loss | WNBA/MLB `projections.py:65-82,129-138` | N | RETAIN machinery | Dispersion fitted per stat × role (WNBA fixes 8.0 for everything) |
| Poisson/Gaussian v2 projection, push at integer lines | NFL `projection.py:266-326` | W (hierarchy Brier 0.217 vs market 0.209) | COMBINE | Used only as fast-path fallback; sim is primary |
| Beta/Laplace shrinkage, market-prior Beta (κ) | NFL `calibration.py:246,333-335` | **V-ish** (shrunk Laplace 0.205 beat raw 0.238 and market 0.209 on one slate, n=236; α grid best α=4) | ADAPT | General empirical-Bayes rate shrinkage; κ/α fit per stat |
| Vacated-volume redistribution | NFL `usage.py:43-55` | W (VACATED_TARGETS 52% n=21; VACATED_CARRIES 0/4) | ADAPT concept | With/without usage redistribution, shrunk (§5 M7) |
| Absence-tree (capped teammate bump + sample tiers) | `nba-props-pipeline` EdgeContext (Drive-only) | N | ADAPT concept (clean-room) | Caps and sample tiers re-fit |
| Efficiency HOT/COLD regression signals | NFL `usage.py` | W (HOT 48% n=101 — hot streaks fade) | ADAPT as *evidence against* trusting short-run shooting | Strong shrinkage of shooting %, not a signal |
| L5/L10 hit-rate tiers | NFL `calibration.py:716-727`; WNBA alt props `alt_player_props.py:141-163` | W (raw empirical Brier 0.238) | EXCLUDE as model input | Context display only |
| Low-line AST/REB exclusion (<3.5) | WNBA `alt_player_props.py:42-45`; NBA-pp `generate_prop_edges.py:116` | N | RETAIN as gate (validate) | — |
| `|proj−line|/line` edge | NBA-pp `generate_prop_edges.py:598-601` | **Disproven** (blows up on 0.5 lines; 100% "elite" bucket is an artifact) | EXCLUDE | Probability edge only |
| Three hand-weighted confidence/grade models | NBA-pp `core/confidence.py`, `generate_playbook_grades.py`, SGP ScoreModel | N (in-sample, constant features) | EXCLUDE | — |
| Hand-set gates from single-slate post-mortems (WNBA team-total 68/72, PPG 95/82, elite-rebound team list) | WNBA `slate_quality.py`, `slate_strategy.py` | N | EXCLUDE as constants; RETAIN idea list as *ablation candidates* | WNBA-scale constants would never fire at NBA scoring |
| Matchup Advantage Rankings | NFL `matchup_rankings.py` | N (no grader; no line comparison; unclamped boosts) | EXCLUDE | Matchup layer rebuilt with shrinkage + caps + ablation |

### 2.5 Pricing, sizing, risk

| Component | Source | Ev. | Decision | NBA adaptation |
|---|---|---|---|---|
| Push-aware EV `p·(d−1) − (1−p−push)` | NFL `best_bets.py:731-734` | N (15 pricing tests) | RETAIN | — |
| Quarter Kelly, edge band 0.02–0.10, max 3u, 0.5 grid | Shared `sizing.py:57-133` | W (10%+ edge bucket −19.5% ROI n=60) | RETAIN + extend | Kelly on conservative probability (§5 M12) |
| Pillar caps (0.03 per pillar, 0.08 total) | NFL `best_bets.py:67-68` | N | ADAPT | Caps on total adjustment vs market |
| Portfolio caps (wager 3, daily 20, event 4, player 3, team 5, market type 6, cluster 5, book 8) | Shared `portfolio.py`, `config/portfolio_risk.json` | N | RETAIN + extend | **Add statistical correlation** (shared core's is structural only; `soft_correlation_multiplier` always 1.0) |
| Same-player correlation guard | NFL `high_prob_rank.py:77` | N | COMBINE into portfolio | — |
| T-30 reprice kill/keep (never increases stake) | Shared `t30_reprice.py:183-552` | N | ADAPT | Kill on late scratch / starting-lineup change / line move |
| Parlay optimizer: marginal-EV "parasitic leg" refusal, fragility, modes | NCAA `governance/parlay.py:91,241`, `models.py:386-526` | N | ADAPT concept | **Fix fail-open defaults** (missing prob→0.50, missing edge→positive) to fail-closed; joint prob from sim, not ad-hoc penalties |
| Copula parlay correlation | NCAA plan §6 (not implemented) | — | REPLACE | Joint sim (§5 M8) |
| Teammate assist-barrier (no AST+AST same team) | WNBA `alt_player_props.py:382-398` | N | REPLACE | Correlation from sim decides |
| Two-key promotion gate (n floor AND OOS skill vs vig-free market; calibration gap; CLV) | NCAA `config/promotion.json` (no evaluator in code) | design only | ADAPT + **implement evaluator** | Per market family |
| Blend promotion (`final = w·market + (1−w)·model`, holdout) | Shared `probability_blend.py:167-271` | **Fails** (`improved:false` everywhere) | ADAPT | **Fix:** if holdout does not improve, model weight = 0 (shared core floors market at 0.75, still giving 25% to a losing model) |
| Learned multipliers / stake calibration / drawdown | Shared `learned_multipliers.py`, `stake_calibration.py`, `drawdown.py` | N (neutral in prod) | RETAIN (import, neutral) | Wilson-of-segment haircut flagged as unusual — re-examine before enabling |

### 2.6 Validation, feedback, tracking

| Component | Source | Ev. | Decision | NBA adaptation |
|---|---|---|---|---|
| Walk-forward refit by period, kickoff < period start | NCAA `backtest/harness.py:142,182-191` | mechanics V | ADAPT | Daily refit; tip < cutoff |
| Metrics: Brier, log loss, Wilson CI, reliability, ECE | NCAA `backtest/metrics.py:21-129`; `settle.py:169` | — | ADAPT | **Fix bucket-gap bug** (half-open buckets drop [0.849,0.85) etc.) |
| Feedback ledger (`market_snapshots`, `decisions`, `settlements`, memberships) | Shared `feedback_db.py:560-662` | — | RETAIN (import) | **Fix pseudo-replication:** one training row per outcome (first decision-time capture) |
| Results grading (box score → market components, DD/TD) | Shared `results.py:410-458` | — | ADAPT | Add `NBA` to `SPORT_PATHS` and the basketball branch |
| Settlement Brier/log-loss/CLV by tier/bucket | NFL `settle.py:355,527-536,648-663` | — | COMBINE | — |
| Signal scorecard + JSONL ledger + same-week direction baselines | NFL `scorecard.py:207-314` | W | ADAPT | Per-layer scorecards (minutes, usage, pace…) |
| Write-once archive | NFL `archive.py` | — | ADAPT | Raw inputs + run bundles |
| Desk A–E reasoning layer | Shared `run_desk.py`, verdict gate | — | EXCLUDE from v1 | House rule: never run reasoning unless asked. NBA pack format kept desk-compatible for later |

---

## 3. Phase 2 deliverable — NBA-specific adaptation (summary)

Each requested topic maps to an engine in §5. All are PROPOSED (unvalidated for NBA).

| Topic | Engine | Mechanism |
|---|---|---|
| Offensive/defensive efficiency | M4 | Ridge per-100 ratings, as-of, shrunk to preseason prior |
| Pace & possessions | M5 | Log-additive pace model; possession distribution; OT probability |
| Usage, minutes, rotations | M6, M7 | Minutes mixture model; usage shares with with/without redistribution |
| Injuries, lineups, availability | M3 | Status → play probability (fitted); timestamped; fail-closed |
| Home/away | M4 (team), M7 (player, shrunk to team effect) | Team HCA fitted per season; player splits only if they persist |
| Rest, travel, back-to-back | M3, M5, M6 | Mostly via availability and minutes; small efficiency terms only if ablation-positive |
| Matchups | M7 | Opponent-by-role, shot-profile vs defense, scheme pooling; shrunk + capped |
| Recent form, SOS, opponent adjustment | M4, M7 | Changepoint for roles; EWMA per stat; SOS adjusts history, matchup adjusts tonight, same ratings |
| Player & team projections | M8 | Joint simulation |
| Regular season vs playoffs | M2 phase flag, M6/M7/M5 | Playoff deltas from past postseasons, shrunk; separate calibration and gate |
| Uncertainty & confidence | M8, M10, M12 | Full distributions; interval coverage/PIT; Kelly on conservative p |

**NCAA ≠ NBA:** no NCAA statistical constant is used. Basketball constants come from NBA data only;
WNBA constants are not transplanted either (40-min games, ~80-85 PPG, 15 teams).

---

## 4. Phase 3 deliverable — Master NBA Pipeline Blueprint

### 4.1 Re-ordered architecture (changes from the proposed 13-step order, with reasons)

| # | Engine | Change vs proposed order | Justification from existing pipelines |
|---|---|---|---|
| M0 | Run Context & As-Of Boundary | **New, first** | NFL `run_context` + NCAA `asof`: every later stage reads data through one cutoff; the main source of past bugs (NFL F01–F30 forensic epic) was leakage and publication gating |
| M1 | Data Collection & Integrity Verification | = 1 | Stage receipts per source (NFL); feed health (shared) |
| M2 | Cleaning, Identity & Standardization | = 2 | Name-key normalization failures caused two NFL injury leaks (findings A and the #268 fix) |
| M3 | **Availability, Rest & Lineup Engine** | **Moved up from 5/7** | Everything downstream (team ratings tonight, pace, minutes, usage, prices) depends on who plays. NFL's fail-closed injury admission is its most robust gate |
| M4 | Team Strength, Efficiency, SOS & Opponent Ratings | = 3+4 merged | Advanced metrics and team models are one fitting step (ridge per-100 *is* the advanced metric) |
| M5 | Pace & Possession Engine | split out of 3 | Pace must exist in exactly one place to prevent double counting |
| M6 | Minutes & Rotation Engine | split of 5 | Minutes are the largest error source for props |
| M7 | Usage, Rates & Matchup Engine | 5+6 merged | Matchups adjust *rates*, never final outputs (lesson of NFL rankings) |
| M8 | Joint Game Simulation (Predictive Engine) | = 8 | One simulation prices every market consistently |
| M9 | Market Consensus Engine | **New explicit engine (runs in parallel from M2)** | Market is the baseline that all evidence says is hardest to beat |
| M10 | Calibration, Consensus & Market Blend | = 9+10 merged | "Cross-model consensus" = ridge/Elo/logit ensemble + dual player projections + market blend, all OOS-weighted |
| M11 | Pricing & Opportunity Engine | new | Main lines, alt ladders, props, combos, parlays, SGP, mispricing checks |
| M12 | Risk, Gates, Sizing & Portfolio | = 11 | Shared sizing + portfolio + statistical correlation |
| M13 | Reporting & Publication | = 12 | Run bundle, receipts-gated |
| M14 | Settlement, Tracking, Drift & Promotion | = 13 | Ledger + promotion evaluator (the NCAA gate made executable) |
| M15 | Live Engine (separate service) | new, Phase 7 | Different latency/data regime |

### 4.2 Dependency DAG

```
M0 RunContext(as_of_utc, mode)
 └─ M1 Ingest ──► receipts{schedule, odds, props, injuries, stats, lineups, refs}
     └─ M2 Identity/standardize
         ├─ M9 Market consensus (odds → vig-free probs, consensus lines, ladders, movement)
         └─ M3 Availability/Rest/Lineup ─┐
             M4 Team ratings (as-of) ────┤
             M5 Pace ◄── M3, M4 ─────────┤
             M6 Minutes ◄── M3, M4(margin prior), M5 ─┤
             M7 Usage/Rates/Matchups ◄── M3, M4, M6 ──┤
                                                     ▼
                         M8 Joint simulation (N sims per game)
                                                     ▼
            M10 Calibration + ensemble + market blend ◄── M9
                                                     ▼
            M11 Pricing (EV, fair odds, ladders, combos, parlays, SGP) ◄── M9 prices
                                                     ▼
            M12 Gates → sizing → portfolio (correlation from M8)
                                                     ▼
            M13 Publish (only if all REQUIRED receipts OK and promotion status allows)
                                                     ▼
            M14 Settle → CLV → scorecards → drift → promotion evaluator ──► config/nba_promotion.json
```

**Required stages** (failure ⇒ withhold publication, exit 3, bundle-only): M0, M1(schedule, odds,
injuries), M2, M3, M8, M9, M12. **Optional stages** (degrade with flag): referees, tracking-data
enrichments, coaching, matchup history.

---

## 5. Phase 4 deliverable — Complete NBA Module Specifications

Notation: `t` = game, `i` = player, `T` = team, `O` = opponent. "Fit" = estimated by walk-forward on
data strictly before the prediction cutoff. Every numeric value labelled **starting value** is a
placeholder to be replaced by a fitted value; none is claimed to be validated.

### M0 — Run Context & As-Of Boundary (`outlier_nba/run_context.py`)
- **Inputs:** mode ∈ {live, replay, retrospective, fixture}, slate date, `as_of_utc`.
- **Rules:** live/fixture may publish; replay/retrospective are bundle-only. Every reader takes the
  context; rows stamped ≥ `as_of_utc` (or ≥ tip for game-scoped data) are dropped; unstamped rows
  raise `LeakageError` in non-live modes.
- **Outputs:** `RunContext` (frozen dataclass), `run_id`.
- **Fallback:** none — no context, no run.

### M1 — Data Collection & Integrity (`outlier_nba/ingest/`)
- **Sources (see §9 for verification status):** Outlier (schedule, game markets, player props incl.
  alt ladders, injuries); NBA stats (box scores, play-by-play, lineup stints, shot charts, tracking);
  ESPN (game logs/scoreboard fallback — adapters exist for WNBA in `projections.py:382-385`,
  `form_source.py:13-17`); official injury report; starting lineups; referee assignments.
- **Integrity checks per source:** pagination completeness (NFL `IncompletePaginationError`), schema
  validation, freshness ≤ source max age, coverage ≥ threshold (shared `feed_health`), duplicate
  event detection, timestamp presence.
- **Outputs:** raw archive (write-once, sha256), `StageReceipt{status: OK|INCOMPLETE|FAILED|INVALID}`.
- **Fallback:** ESPN for box scores if NBA stats unavailable; required-source failure ⇒ withhold.

### M2 — Identity & Standardization (`outlier_nba/identity.py`)
- 30-team alias table (incl. LA/Los Angeles variants); canonical player id (NBA person id) with an
  alias map from Outlier/ESPN names.
- **Name key:** lowercase, strip punctuation, strip suffixes (Jr, Sr, II, III, IV), collapse spaces —
  one function used everywhere (NFL finding A: `alt_floors._name_key` skipped suffixes).
- Unresolved identity ⇒ row flagged `IDENTITY_UNRESOLVED` (disqualifying), never silently dropped.

### M3 — Availability, Rest & Lineup Engine (`outlier_nba/availability.py`, `rest.py`, `lineups.py`)
- **Inputs:** injury report rows (status, report timestamp), lineup confirmations, schedule, roster.
- **Play probability:** `P(play | status, context) = logistic(β_status + β_rest·b2b + β_age·age +
  β_return·games_missed + …)` fit on historical report-status → actual-played pairs. **Starting
  value** only if fit impossible: Available 1.0 / Probable ~0.95 / Questionable ~0.5 / Doubtful ~0.1
  / Out 0 (to be replaced).
- **Rest features:** days rest {0,1,2,3+}, B2B leg & road-second-leg, 3-in-4, 4-in-6, travel km,
  time zones crossed & direction, altitude venue (DEN, UTA), opponent rest diff.
- **Rest-DNP probability:** separate logistic on healthy-scratch history (age, minutes load, B2B,
  late season, seeding-locked).
- **Lineups:** projected starters → confirmed starters (timestamped); starter change triggers re-run
  of M5–M12 for that game.
- **Fail-closed:** injury report missing/stale ⇒ all props for that game `INJURY_UNVERIFIED`
  (withheld); team markets carry flag and wider uncertainty. Never collapse "unknown" to "no
  injuries" (NFL rankings bug at `matchup_rankings.py:172`).
- **Outputs:** per player `p_play`, `minutes_cap`, `return_from_injury_games`, `rest_features`;
  per game `availability_receipt`.

### M4 — Team Strength, Efficiency, SOS & Opponent Ratings (`outlier_nba/ratings/`)
- **Possessions:** `poss = FGA + 0.44·FTA − OREB + TOV` (standard estimate; both teams averaged).
- **Ridge per-100:** `pts_per100(T vs O, t) = μ + h·home_t + O_T − D_O + ε`, weights
  `w = exp(−Δdays/τ)`, penalty λ; as-of fit daily. Neutral site ⇒ h = 0. Starting τ, λ from NCAA
  structure, then **fit** (NCAA retuned λ 25→0.5 — expect similar re-tuning).
- **Preseason prior:** `O_prior = a·O_prev_final + b·roster_continuity_adj`; blend
  `(n·O_fit + k·O_prior)/(n + k)`, k fit.
- **Elo:** `E = 1/(1+10^(−(R_A − R_B + HFA)/400))`; MOV multiplier per published 538 NBA form as
  structure; K, HFA, season carryover fit.
- **Availability-adjusted ratings:** `O_T,tonight = O_T − Σ_i∈out impact_i · minutes_share_i`,
  where `impact_i` is a ridge-regularized on/off (RAPM-lite) estimate from lineup stints, shrunk by
  minutes. Requires stint data (§9).
- **Per-stat opponent ratings (for M7):** opponent-adjusted allowed rates by role (ball-handler /
  wing / big) for PTS, REB (O/D), AST, 3PA, 3P%, FTA, TOV, STL, BLK; pace-removed; ridge-shrunk.
- **SOS use:** past games re-expressed vs average opponent (history correction) — M7 reads the same
  ratings for tonight's opponent. One rating source ⇒ no double counting.
- **Outputs:** `team_ratings_asof`, `elo_asof`, `opp_stat_ratings_asof`, `player_impact_asof`.
- **Validation:** walk-forward Brier/log loss of margin and win prob vs CFBD-style baselines (Elo
  alone, ridge alone) and vs market.

### M5 — Pace & Possession Engine (`outlier_nba/pace.py`)
- `log(poss_t) = μ_p + p_home + p_away + β_rest·rest_diff + β_po·playoff + β_lineup·lineup_pace + ε`
  (ridge, as-of).
- Possession distribution: `poss ~ N(mean, σ_p)` with σ_p fit from residuals (no floors).
- OT: handled in M8 (regulation tie ⇒ 5-min OT, `poss_OT ≈ poss·5/48`).
- **Invariant:** no other engine includes a pace term; test asserts M7 features are pace-neutral.
- **Validation:** possessions MAE vs baseline `(pace_home + pace_away)/2`; OT frequency calibration.

### M6 — Minutes & Rotation Engine (`outlier_nba/minutes.py`)
- **Baseline:** EWMA of role-consistent minutes (half-life fit; changepoint detection on starter
  status, trade, rotation change — role changes override smoothing).
- **Redistribution:** if teammate j out, minutes_j reallocated by historical with/without minutes
  shifts (shrunk to depth-chart/position proportional allocation).
- **Coach-level tendencies:** rotation depth, blowout pull curve `Δmin = −β_blow·max(0, |margin|−θ)`
  for starters (β, θ fit per coach, shrunk to league), rest policy (feeds M3), ramp-up caps.
- **Game script:** minutes conditioned on simulated margin inside M8.
- **Distribution:** given plays: mixture `(1−q)·N(μ_m, σ_m) + q·early_exit`, q = f(foul-trouble
  propensity, injury-return, ref crew foul rate if enabled).
- **Constraint:** Σ team minutes = 240 (+25 per OT) — renormalize.
- **Playoffs:** rotation-shortening deltas from past postseasons (shrunk).
- **Validation:** minutes MAE and CRPS, reported separately; DNP-rate calibration by rest bucket.

### M7 — Usage, Rates & Matchup Engine (`outlier_nba/rates/`)
- **Usage share** `u_i = (FGA + 0.44·FTA + TOV)_i / team_possessions_used_while_on` ; with/without
  redistribution when key teammates out; Σ on-court usage normalized to 1.
- **Shot profile:** location mix (rim, short paint, mid, corner 3, above-break 3), shot type
  (C&S/pull-up, assisted/unassisted), FT rate. Expected pts/shot from location×type league values;
  player make rates shrunk toward expected: `r̂ = (n·r_obs + k·r_exp)/(n + k)`, k per shot class
  (beta-binomial EB).
- **3PM:** `3PA = usage-driven attempts × 3PA share`, `3P% = shrunk C&S/pull-up, corner/ATB mix`.
- **Rebounds:** chances = opponent misses (O-reb: own misses) from sim; long-rebound split by miss
  type; player share of available O/D rebounds while on floor, competitor-adjusted by lineup.
- **Assists:** potential-assist chances × teammate conversion (teammates' xFG on assisted shots);
  falls back to assist rate if tracking data unavailable.
- **FTA:** foul-drawing rate × opponent foul rate × (ref crew factor, optional) + late-game
  intentional-foul volume from sim.
- **TOV, STL, BLK:** TOV share of usage vs opponent pressure; STL = opponent live-ball TOV chances ×
  player steal share; BLK = opponent rim attempts × player block share.
- **Recent form:** EWMA half-life per stat (fast for minutes/usage, slow for shooting %), fit.
- **Matchups (opponent-by-role, shot-profile vs defense, scheme pooling, primary defender if
  tracking available):** multiplier `m = exp(β·z_opp)`, β fit, z from M4 per-stat ratings, `|log m|`
  capped (starting cap 0.03 in probability-equivalent terms per layer, 0.08 total — NFL caps as
  structure, to be fit).
- **Home/away player term:** shrunk to team HCA; kept only if season-to-season persistence test
  passes.
- **Matchup history (player vs team):** residual after all other effects, heavily shrunk; **off by
  default**.
- **Outputs:** per player per game: rate vectors with standard errors.

### M8 — Joint Game Simulation (`outlier_nba/sim/`)
- **Per simulation s (N starting 10,000/game; fit N for tail stability):**
  1. Draw availability for each uncertain player (from M3).
  2. Draw possessions (M5).
  3. Draw team efficiencies per100 with correlated noise (ρ between teams fit) from M4.
  4. Regulation score; tie ⇒ OT loop.
  5. Draw minutes for each player conditioned on |margin_s| (M6), renormalize to team minutes.
  6. Allocate team possessions-used across on-court players by usage (Dirichlet-multinomial);
     resolve each player-possession into 2PA/3PA/FT trip/TOV; makes via shrunk rates (M7);
     rebounds and assists allocated from resulting misses/makes.
  7. **Reconcile** Σ player points to team points: scale rates by
     `r = team_pts_target / Σ E[player_pts]` (bounded); log r as a diagnostic (large r ⇒ model
     inconsistency flag).
- **Outputs:** per game arrays: margin, total, team totals, OT flag; per player arrays: MIN, PTS,
  REB (O/D), AST, 3PM, FTM/FTA, TOV, STL, BLK. All markets read from these arrays.
- **Fast path:** NB2 per stat with fitted dispersion for non-SGP pricing; a test asserts NB2 and sim
  agree within tolerance on main lines.
- **Dependency decision:** add `numpy` (not currently a dependency — `pyproject.toml` has none).
  Pure-Python simulation at this scale (≈12 games × 10k sims × ~26 players) is impractical.

### M9 — Market Consensus Engine (`outlier_nba/market/`)
- Per book two-sided devig (methods: multiplicative, Shin w/ arb fallback, power); default method
  chosen by NBA OOS log loss vs outcomes (NCAA publishes Shin→multiplicative while its config says
  multiplicative — resolve explicitly here).
- Consensus = median per side across books, renormalized; sharp anchor if ≥2 sharp books;
  `thin_market` if <3 books; placeholder drop.
- Props: balanced main line from ladder (NFL consensus rule); alt ladders kept per rung; integer-line
  push via adjacent half-lines.
- Line movement and snapshots (append-only, as-of filtered).
- **Outputs:** `p_market` per outcome, consensus line, best price per book, movement features.

### M10 — Calibration, Ensemble & Market Blend (`outlier_nba/calibrate/`)
- **Team ensemble:** logit pooling of ridge-sim, Elo, features-logit; weights fit by simplex grid on
  *prior seasons only* (nested walk-forward; fixes NCAA in-sample weighting).
- **Player dual-projection consensus:** minutes×rate vs usage-share projections; |difference| > fitted
  threshold ⇒ `PROJECTION_DISAGREEMENT` flag (no blind averaging).
- **Calibration:** per market family, isotonic (or Platt when n small) fitted on OOS predictions.
- **Market blend:** `logit p_final = w·logit p_model + (1−w)·logit p_market`, w fit per segment with
  shrinkage; **w = 0 unless the holdout improves** and the promotion gate (M14) has passed for that
  family.
- **Outputs:** `p_model`, `p_calibrated`, `p_final`, `w_used`, `segment`.

### M11 — Pricing & Opportunity Engine (`outlier_nba/pricing/`)
- **EV (push-aware):** `EV = p_win·(d−1) − p_loss`; fair odds `1/p_win` (adjusted for push);
  edge = EV.
- **Markets:** ML, spread, game total, team totals, all player props, combos (PRA, PR, PA, RA,
  S+B), DD/TD, alt spreads/totals/team totals, every prop-ladder rung.
- **Cross-game parlays:** joint = product of leg `p_final` only for legs in different games;
  NCAA-style leg admission (marginal EV > 0; win-probability cost bound); **missing probability ⇒
  reject leg** (fail-closed).
- **SGP:** joint probability from M8 co-occurrence; compared only against an actual quoted SGP
  price. **No quoted price ⇒ fair-price display only, never a recommendation.**
- **Mispricing checks:** book combo line vs its own single-stat lines; DD price vs REB/AST lines;
  ladder monotonicity violations; flagged for review, not auto-bet.

### M12 — Risk, Gates, Sizing & Portfolio (`outlier_nba/risk/`)
- **Hard gates (fail-closed, disqualifying):** identity unresolved; injury unverified; stale market;
  books < 3 (props); price window (starting: −250 ≤ price < +150, as shared core); integer line
  without push model; post-tip; season phase not publishable; promotion status for the market family
  ≠ promoted ⇒ shadow-only (units 0).
- **Sizing:** quarter Kelly (shared `sizing.py`) applied to a **conservative probability**
  `p_cons = p_final − z·se(p_final)` (se from sim + parameter uncertainty; z fit to maximize
  OOS log growth); edge band and max units from shared core; 0.5 grid.
- **Portfolio:** shared caps (`config/portfolio_risk.json`) plus **statistical correlation**: pairwise
  outcome correlation of all candidate bets from M8; bets with ρ > ρ* (fit; starting 0.3) form a
  cluster; cluster stake cap; portfolio variance check. Combo + its components on one player = one
  exposure.
- **Insufficient correlation data ⇒ treat as fully correlated** (never as independent).

### M13 — Reporting & Publication (`outlier_nba/publish.py`)
- Run bundle `runs/<run_id>/` (manifest sha256, pointer last); publish only if all required receipts
  OK.
- Outputs: `nba_candidates.csv` (columns compatible with shared pack CSV conventions:
  `board`, `actionable`, `units`, `edge_pct`, flags), `nba_game_lines.csv`, `nba_props.csv`,
  `nba_alt_ladders.csv`, `nba_parlays.csv`, `nba_sgp_shadow.csv`, `briefing.md`, per-game dossier
  with uncertainty decomposition (which source dominates).
- Every artifact stamped `SHADOW` / `UNPROMOTED` per family until promoted.
- **Banner text is generated from receipts**, never hard-coded (NFL prints "Zero Data Leakage"
  unconditionally, `matchup_rankings.py:744`).

### M14 — Settlement, Tracking, Drift & Promotion (`outlier_nba/track/`)
- **Ledger:** shared `feedback_db` with `sport='NBA'`; training/evaluation views take **one row per
  outcome** (first decision-time capture).
- **Settlement:** NBA box scores → results via shared `results.py` basketball branch.
- **CLV:** real close = last Outlier snapshot within 60 min of tip with a verified timestamp; else
  `close_unverified` (excluded from CLV stats).
- **Scorecards:** per layer (minutes MAE, usage MAE, possessions MAE, efficiency error), per market
  (Brier, log loss, reliability, ECE, interval coverage, PIT), per rung, per phase.
- **Drift:** rolling 14/28-day Brier gap vs market, CLV trend, feature PSI, minutes MAE trend; alert
  thresholds fit from first-season variance.
- **Promotion evaluator** (`outlier_nba/track/promotion.py`) — executable version of the NCAA gate,
  per market family:
  ```json
  {"family":"player_props.PTS","status":"shadow",
   "min_settled_outcomes":1500,"min_distinct_dates":45,
   "max_abs_calibration_gap":0.03,"min_bucket_n_for_gap_test":100,
   "require_oos_logloss_beat_market":true,
   "require_median_clv_non_negative":true,
   "require_ablation_pass_for_each_enabled_layer":true}
  ```
  Status is written only by the evaluator; hand-editing is rejected by a config-hash check.

### M15 — Live Engine (Phase 7, separate service `outlier_nba/live/`)
- State: score, clock, possession, fouls/bonus, timeouts, on-court players.
- Win prob/margin: pregame M8 prior updated by game state (structure from NCAA `models/live.py`
  idea — Φ((lead + E[remaining margin])/σ·√t_remaining) — refit for NBA); totals = current +
  remaining possessions × current-pace-adjusted efficiency; player props = current + remaining
  minutes × rates.
- Shadow only: logs would-bet vs live price at that moment; latency recorded per decision.
- **Blocked on data:** live PBP feed and live odds feed (§9).

---

## 6. Phase 5 deliverable — Validation & Monitoring Framework

1. **Historical backtest (team markets):** daily walk-forward over NBA seasons (target 2016-17 →
   2025-26). 2019-20 bubble and 2020-21 limited-attendance seasons scored as a **stress slice** and
   excluded from HCA fitting (NCAA treated 2020 the same way).
2. **Time-based OOS:** every parameter (τ, λ, k, half-lives, β's, dispersions, ensemble weights,
   calibration maps, blend w) fit only on data before the evaluation date; hyperparameters tuned on
   seasons *outside* the scoring window (NCAA tuned inside it — not repeated).
3. **Leakage prevention:** M0 as-of reader on every source; point-in-time ratings; timestamped
   injury/lineup/ref data; tests that inject a post-cutoff row and assert it is rejected (pattern:
   `test_nfl_asof_leakage`, NCAA `test_asof`).
4. **Calibration:** reliability by decile with **closed buckets** (fix NCAA gap bug), ECE, interval
   coverage (50/80/95%), PIT histogram, CRPS for distributions, per-rung calibration for ladders,
   joint-pair calibration for SGP/correlations.
5. **Model comparison & ablation:** each layer in §3 has an on/off test on held-out seasons; a layer
   ships enabled only if it improves log loss/CRPS on the relevant targets with a CI excluding zero.
   Baselines: market (vig-free), season average, L10 hit rate, ridge-only, Elo-only.
6. **Market comparison:** primary metric is OOS log loss vs vig-free closing consensus; secondary is
   CLV (median and mean, verified closes only).
7. **Drift monitoring:** M14 drift metrics; automatic demotion to shadow if rolling 28-day Brier gap
   vs market exceeds the fitted alert threshold.
8. **Performance tracking:** ROI reported but never used as a promotion key alone (small-n ROI is
   noise: shared core +2.9% on 159 plays with price CLV −0.07 on SO).
9. **Historical-odds caveat:** team-market backtests vs market need historical NBA closing lines
   (source not yet identified — §9). Historical **prop** lines are effectively unavailable, so prop
   families can be backtested vs non-market baselines historically but can only pass the
   "beat market" key prospectively from Phase 1 shadow capture.

---

## 7. Phase 6 deliverable — Execution Workflow (target operating procedure)

```
# daily, after Outlier auth (shared login flow)
python -m outlier_nba.cli ingest   --date 2026-10-21            # M1-M2, receipts
python -m outlier_nba.cli ratings  --date 2026-10-21            # M4 as-of fit
python -m outlier_nba.cli run      --date 2026-10-21 --mode live # M3-M13, bundle + publish gate
python -m outlier_nba.cli reprice  --date 2026-10-21 --t-minus 30 # late scratches / lineups
python -m outlier_nba.cli settle   --date 2026-10-20            # M14 results + CLV
python -m outlier_nba.cli scorecard --through 2026-10-20
python -m outlier_nba.cli promote  --evaluate                    # writes promotion status only via evaluator
python -m outlier_nba.cli backtest --seasons 2018-19:2025-26 --families team
```
Exit codes: 0 OK, 2 lock busy, 3 required stage failed (publication withheld). Replay:
`run --mode replay --as-of <ts>` is bundle-only. No reasoning-model calls anywhere in this CLI.

---

## 8. Phase 6 deliverable — Implementation Package (step-by-step)

Layout:
```
outlier_nba/
  __init__.py  cli.py  config.py  run_context.py  receipts.py  run_writer.py  identity.py
  ingest/      outlier.py  nbastats.py  espn.py  injuries.py  lineups.py  refs.py  archive.py
  availability.py  rest.py  lineups.py
  ratings/     possessions.py  ridge.py  elo.py  logistic.py  ensemble.py  player_impact.py  opp_stats.py
  pace.py  minutes.py
  rates/       usage.py  shots.py  rebounds.py  assists.py  freethrows.py  defense_events.py  matchup.py  form.py
  sim/         engine.py  allocate.py  reconcile.py  fastpath.py
  market/      devig.py  consensus.py  ladders.py  movement.py
  calibrate/   isotonic.py  blend.py
  pricing/     ev.py  markets.py  ladders.py  parlays.py  sgp.py  mispricing.py
  risk/        gates.py  sizing.py  portfolio.py  correlation.py
  publish.py
  track/       settle.py  clv.py  scorecard.py  drift.py  promotion.py  backtest.py
  live/        (Phase 7)
config/nba_settings.json  config/nba_promotion.json
tests/test_nba_*.py   tests/fixtures/nba/
```
`pyproject.toml`: add `outlier_nba*` to packages, pyright `include`; add `numpy` dependency
(decision D2).

### Phase 0 — Foundations & data-source verification (≈2-3 days)
1. `registry.py`: NBA team aliases (30) + display names; `opp_rank_applicable=False`; keep
   `enabled_by_default=False` until Phase 1 passes; update `tests/test_registry_paths.py`.
   *Deliverable:* `normalize_team` resolves all 30 teams; tests green.
2. `results.py`: add `NBA` to `SPORT_PATHS` and the basketball grading branch (`:34-37, 412, 789`).
   *Deliverable:* NBA box score fixture grades PTS/REB/AST/3PTS/combos/DD/TD.
3. **Data-source spike (read-only, no paid sources):** confirm Outlier NBA schedule/markets/props/
   injuries and whether props include alt ladders; confirm NBA stats endpoints for box scores,
   PBP, lineup stints, shot charts, tracking (potential assists, rebound chances, defender
   matchups) — rate limits and ToS; injury-report timestamps; referee assignment page; historical
   closing-line source for team markets. *Deliverable:* `docs/nba/data-sources.md` with a
   verified/unverified table.
4. `outlier_nba` skeleton: `run_context`, `receipts`, `run_writer`, `identity` with tests
   (as-of rejection, publication gate re-gating after any FAILED stage, name key with suffixes).

### Phase 1 — Shadow capture (ship ASAP; ≈1 week)
5. `ingest/outlier.py` + `market/devig.py` + `market/consensus.py` + `market/ladders.py`: capture
   every NBA market and ladder into the shared ledger with `sport='NBA'`, stamped SHADOW.
6. `track/settle.py` + `track/clv.py`: nightly settlement and verified-close CLV.
7. Enable NBA in the refresh path behind `--leagues ...,NBA` (default unchanged).
   *Done when:* a full slate is captured, settled next day, and one-row-per-outcome view is correct.

### Phase 2 — Team model → game lines, spreads, totals, team totals (≈2-3 weeks)
8. `ingest/nbastats.py` historical backfill (seasons per D4) into a local store (SQLite, written in
   local temp then copied in — see cloud-sync note §11).
9. `ratings/*`, `pace.py`, sim team layer (`sim/engine.py` steps 1-4).
10. `track/backtest.py` walk-forward + metrics (closed buckets) + ablations + nested ensemble.
11. `calibrate/*`, `pricing/markets.py` for ML/spread/total/team total + alt lines, `risk/*`,
    `publish.py`. *Done when:* backtest report vs Elo-only, ridge-only, and (if available) market
    baselines; shadow publication of team markets daily.

### Phase 3 — Player model → props and combos (≈3-4 weeks)
12. `availability.py`, `rest.py`, `lineups.py` (fail-closed, timestamped).
13. `minutes.py`, `rates/*` (usage, shots, rebounds, assists, FT, TOV, STL/BLK, form, matchup).
14. `sim/allocate.py`, `sim/reconcile.py`, `sim/fastpath.py`.
15. Pricing for all props, combos, DD/TD, prop ladders; per-rung calibration.
    *Done when:* per-layer scorecards and ablations exist; props published in shadow.

### Phase 4 — Correlations, cross-game parlays, portfolio (≈1-2 weeks)
16. `risk/correlation.py` (pairwise from sim), `risk/portfolio.py` clusters, `pricing/parlays.py`
    (fail-closed). Joint-pair calibration report.

### Phase 5 — Same-game parlays (shadow) (≈1 week, blocked on SGP quote source)
17. `pricing/sgp.py` fair prices from sim; recommendation path only if a quoted SGP price source
    exists and the SGP family passes promotion.

### Phase 6 — Optional layers via ablation (rolling)
18. Referees, coaching tendencies, matchup history, primary defender, player-level home/away,
    lineup synergy. Each: feature + ablation test + scorecard; enabled only on pass.

### Phase 7 — Live (separate service, shadow) — blocked on live PBP + live odds feeds.

**Test plan (every phase):** `pytest` (offline; fixtures only; no provider SDK calls),
`python -m ruff check`, `python -m mypy outlier_nba`, `pyright outlier_nba`; ≥80% coverage on
changed `outlier_nba` modules (house rule); TDD per `tdd-workflow`.

### Pseudo-code — core simulation and pricing

```python
def simulate_game(ctx: RunContext, game: Game, n: int, rng: np.random.Generator) -> SimResult:
    avail = availability.draw(game, n, rng)                  # (n, players) bool
    poss = pace.draw(game, n, rng)                           # (n,)
    eff_h, eff_a = ratings.draw_efficiency(game, avail, n, rng)  # correlated, per100
    pts_h, pts_a = poss * eff_h / 100, poss * eff_a / 100
    pts_h, pts_a, ot = overtime.resolve(pts_h, pts_a, poss, eff_h, eff_a, rng)
    margin = pts_h - pts_a
    mins = minutes.draw(game, avail, margin, ot, rng)        # (n, players), team sums = 240+25*ot
    lines = allocate.player_lines(game, mins, poss, rates.for_game(game), rng)
    lines, r = reconcile.to_team_points(lines, pts_h, pts_a) # r logged; |r-1|>tol => flag
    return SimResult(margin, pts_h, pts_a, ot, lines, diagnostics={"reconcile_r": r})

def price_outcome(outcome: Outcome, sim: SimResult, market: MarketView) -> Priced:
    win, push = outcome.evaluate(sim)                        # vectorized over sims
    p_model = calibrate.apply(outcome.family, win.mean())
    p_final = blend.apply(outcome.segment, p_model, market.p_vigfree(outcome))
    if p_final is None:
        return Priced.rejected("MISSING_PROBABILITY")        # fail closed
    ev = p_final * (outcome.decimal - 1) - (1 - p_final - push.mean())
    return Priced(p_final=p_final, push=push.mean(), ev=ev, se=win.std() / sqrt(len(win)))
```

### Key files

| File | Operation | Description |
|---|---|---|
| `outlier_scrapers/registry.py:153-169, 339-346` | Modify | NBA aliases/display, `opp_rank_applicable=False` |
| `tests/test_registry_paths.py:18-23` | Modify | NBA no longer asserted disabled once enabled |
| `outlier_scrapers/results.py:34-37, 410-458, 789` | Modify | NBA grading branch |
| `outlier_scrapers/calibration.py:42`, `config/learned_multiplier_promotion.json` | Modify | `required_sport_samples` NBA key |
| `pyproject.toml` | Modify | package + pyright include + numpy |
| `outlier_nba/**` | Create | New package (above) |
| `config/nba_settings.json`, `config/nba_promotion.json` | Create | Settings; evaluator-owned promotion state |
| `tests/test_nba_*.py`, `tests/fixtures/nba/` | Create | Offline tests |
| `docs/nba/data-sources.md`, `docs/plans/2026-10-20-nba-master-pipeline.md` | Create | Design of record |

---

## 9. Missing information & unverified assumptions (explicit)

| # | Item | Status | Blocks |
|---|---|---|---|
| U1 | Outlier serves NBA schedule/markets/props/injuries today; props include alt ladders | Prior use by `nba-props-pipeline` (`/sportsdata/leagues/NBA/playerProps`); **re-verify** | Phase 1 |
| U2 | NBA stats endpoints (box, PBP, stints, shot charts, tracking) availability, rate limits, ToS | Not verified | Phases 2-3; M4 player impact, M7 shot/tracking |
| U3 | Historical NBA closing lines for team markets | **No source identified** | "Beat market" backtest key for team families |
| U4 | Historical prop lines | Believed unavailable | Prop families can pass "beat market" only prospectively |
| U5 | Official injury report with publication timestamps | Not verified | M3 point-in-time backtests |
| U6 | Timestamped starting-lineup feed | Not verified | M3 lineup confirmation |
| U7 | Referee assignments (daily) + historical crew data | Not in any current source | Optional layer |
| U8 | Coach-by-date table | Not in any current source | Coaching layer |
| U9 | Quoted SGP prices | **No source found** | SGP recommendations (fair-price display still possible) |
| U10 | Live PBP + live odds feed | No source | Phase 7 |
| U11 | Verified-timestamp closing odds (Odds API key was INVALID for NFL as of 2026-09-26) | Outlier snapshots near tip may suffice — verify | CLV key |
| U12 | Exact 2026-27 opening date | Assumed "now" | Phase 1 urgency |
| U13 | Sharp-book set available via Outlier for NBA | Not verified | M9 sharp anchor |

Assumptions labelled as such: possession formula coefficient 0.44 (standard), 538 Elo structure,
starting status→play probabilities, starting caps — all to be replaced by NBA-fitted values.

---

## 10. Risks and mitigation

| Risk | Mitigation |
|---|---|
| Model never beats the market (the historical norm in this repo) | Market-anchored design; w=0 default; system still useful for consistency/mispricing checks and shadow evidence; PASS is a valid output |
| Layer bloat (≈25 requested layers) overfits | Every layer behind ablation; core-first build order; promotion requires ablation pass |
| Leakage via late injury/lineup/ref info | M0 as-of on all sources; timestamp-required; replay tests |
| Pseudo-replication inflating n | One-row-per-outcome evaluation views |
| Cloud-sync filter corrupting large SQLite writes in canonical checkout (memory: `calibration/` corruption history) | Build/backfill DBs in local temp, copy in, verify counts from a fresh process |
| Paid/cookie scraper dependence (RG, CTG) | Not used; free sources only in v1 |
| Pure-Python performance | numpy dependency (D2); fast-path NB2 for non-SGP |
| NFL-style publication-after-failure | Re-gate after every receipt; test the pipeline path, not just receipt lists |
| Shared-core coupling (registry test, results) | Minimal, tested edits; NBA disabled by default until Phase 1 passes |
| Season already starting | Phase 0-1 first; models later |

---

## 11. Decisions

**Resolved 2026-10-20 (user: "proceed" on the recommended options):** D1 slim NBA harness modeled
on `outlier_nfl` (no shared extraction); D2 add `numpy`; D3 shared `feedback.sqlite3` with
`sport='NBA'`; D4 backfill 2016-17 → 2025-26 and include a historical-closing-line source search in
the Phase 0 spike; D5 desk compatibility deferred (desk never run without an explicit request).
Still open: the shared-core blend-floor ruling in PR #282 (not an NBA decision).

Original options:

- **D1** — Harness reuse: re-implement a slim NBA run harness modeled on `outlier_nfl` (recommended;
  avoids coupling NBA to NFL release cadence) vs extracting a shared `outlier_core` harness used by
  both (cleaner long-term, touches NFL).
- **D2** — Add `numpy` as a dependency (recommended; required for the simulation at scale).
- **D3** — Ledger: write NBA into the shared `feedback.sqlite3` with `sport='NBA'` (recommended; reuses
  settlement/reporting) vs a separate NBA ledger (isolates the cloud-sync corruption risk).
- **D4** — Historical backfill depth (recommended 2016-17 → 2025-26) and whether to spend effort
  sourcing historical closing lines (U3).
- **D5** — Whether NBA packs should be desk-compatible now (A–E) or later. Either way the desk is
  never run without an explicit request.

---

## 12. Side findings from the audits (not part of NBA scope)

- **NFL A:** `alt_floors._name_key` (`alt_floors.py:160-161`) and `matchup._eligible`
  (`matchup.py:599,626`) do not strip Jr/III suffixes → inactive players can pass.
- **NFL B:** matchup-rankings FAILED receipt appended after the publish decision
  (`pipeline.py:819` vs `:429-443`); dated/latest files still committed.
- **Shared core:** `negative_learned_edge` check reads a field written later (`pack_selection.py:924-932`
  vs `pack_sizing.py:149,166`) → cannot fire; `so_eval` filter erodes after 90-day retention clears
  `projection_feature_hash`; ledger pseudo-replication; blend floor 0.75 keeps 25% weight on a
  losing model; `compute-drawdown` never invoked nightly; `slate_strategy` not in the daily DAG.
- **cfb-analytics:** fix commit `fb6ffb3` unpushed; σ inconsistent across modules; parlay/gate
  fail-open defaults; promotion evaluator missing.

---

## SESSION_ID (for /ccg:execute use)
- CODEX_SESSION: none — `codeagent-wrapper` not installed; no Codex session was created.
- GEMINI_SESSION: none — same reason.
