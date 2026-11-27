---
name: nfl-master-betting-pipeline
description: Run the live Outlier NFL pipeline for the current NFL betting week or requested slate and deliver the V2 Section 14 report and verified Desktop bundle.
---

# Run NFL pipeline

“Run NFL pipeline” invokes the NFL Master Betting Pipeline V2.0 for the current Eastern betting week (Tuesday through Monday). Run every remaining date with a scheduled game, including special or rescheduled slates. Use the Tuesday baseline when captured. If Tuesday has passed, record an available timestamped baseline or mark it unavailable. An explicit date, “today’s games,” kickoff window, matchup or pre-lock refresh controls scope. Resume the named run with its original scope.

This runbook carries the shared execution and reporting contract from the user's `Desktop/prompts/run nfl pipeline 2.txt`. Windows agents may resolve that file under the configured Desktop for additional methodology. The shared contract works across checkouts and agents.

## Execute and verify

1. Complete the repository sync check and use an isolated checkout and output root outside repository data and calibration. Use the canonical saved Outlier session through the supported NFL client. Keep paid reasoning off unless explicitly requested. Run the authenticated deterministic pipeline with refreshed team tape and game scripts via `outlier_nfl.pipeline.NflPipeline.run` or its CLI. Set an explicit date, output root, and supported window where appropriate. With the Python entry point pass `reports_dir` outside the checkout and `write_latest=False`. Supply the supported local saved session to `OutlierNflApiClient(session_path=...)` when the isolated checkout cannot discover it. Keep authentication material out of the bundle and logs.
2. Verify the manifest, run ID, stage receipts, market timestamps, publication status, and exact event IDs before analysis. A window may contain games outside the requested scope. Filter any wider ingestion by event ID before modeling, ranking or exporting the requested board, and disclose its scope. For a pre-lock refresh, record actual capture time, compare against the earlier dated bundle, and recheck lines and inactives. After kickoff, preserve replay or retrospective status and use pregame evidence only when its provenance supports that label. Keep existing publication and staking gates.
3. Confirm current starters, same-team game counts, official availability, roof and kickoff weather, plus same-team pregame game logs. Use the matching run artifacts and the `nfl-game-script` roster and matchup workflow. Apply the automated roster text validator to the report and README. Hold rows whose player, team, line, odds or timing cannot be verified.
4. Read `nfl_drive_simulations.json` in the run bundle. The pipeline builds research-only regulation simulations from nflverse PBP for games admitted before the run cutoff and matchup kickoff. It requires four completed games per team and averages the offense and opposing defense drive distributions. Shared pace uses historical average drive counts rounded and clipped to 8–14. Inspect each event status and history. For isolated simulation with separately verified inputs, run `python -m outlier_nfl.drive_sim <inputs.json> --seed <integer>` for each resolved event when verified drive inputs are available. Save the JSON receipt. Input keys are `event_id`, `as_of`, `source`, `home`, `away`, and `drives`. Home and away distributions use this order: TD with zero, one, or two conversion points, made field goal, safety conceded, and scoreless drive. The pace probabilities correspond to 8 through 14 drives per team. Derive and document probabilities from pre-kickoff evidence, including opponent adjustments and sample size. Reject missing or stale evidence. The generalized engine uses 50,000 regulation trials, a shared pace draw and conditional independent drive outcomes. Label its score, spread, total and lead probabilities as regulation estimates. Full-game markets that include overtime require an overtime model. The engine supplies joint score counts for regulation score markets, with no player-stat distribution. If inputs are unavailable, mark the simulation **NOT EXECUTED** and identify what is missing.

## Weekly and slate execution

- After STEP 0 returns `REPORT STATUS: OK`, refresh prior-game tape with `python scripts/pull_nflverse_tape.py --before <Eastern YYYY-MM-DD> --data-dir <external-run-root>`. Record the cutoff and tape receipt. The weekly CLI also supports its built-in tape and injury refresh.
- Use `python -m outlier_nfl.weekly --today <Eastern YYYY-MM-DD> --data-dir <external-run-root> --reports-dir <external-reports-dir>` for the remaining week, or the equivalent `outlier_nfl.weekly.run_week` API with a client configured for the canonical saved session. Use the normal weekly refresh when the injury report also needs refreshing. The `--no-refresh-tape` flag skips both refreshes. Generate a game script for every included slate via `NflPipeline.run` with `generate_game_script=True` and an external `reports_dir`. The daily CLI also supports `--date <slate> --generate-game-script`. Reuse a verified artifact if it already covers the same capture and scope. If stages use separate captures, record each run ID and source time and verify the joined event IDs and prices. A scoped request can run the daily pipeline directly.
- Verify moneylines, spreads, totals, team totals, supported periods, alternate lines and props. Record exact sportsbook, selection, line, odds, settlement scope and capture time. Future slates may have missing news or quotes. Preserve pending status and require a pre-lock refresh before promotion when material inputs remain outstanding.
- The V2 modeling target is 50,000 drive-based trials per game using calibrated offensive and defensive EPA and drive rates, kickoff weather and confirmed starters. Record which inputs actually entered the model. The current historical-drive receipt is research-only regulation. Record calibrated EPA, weather and QB conditioning as unavailable for that simulation when absent. Require a calibrated model for the actual market and settlement scope before assigning official value or stakes.

## Filters A–I and bankroll limits

Record a pass, hold or fail and the evidence for each filter on a candidate considered for the official card.

| Filter | Required evidence |
| --- | --- |
| A. Minimum EV | At least +3% projected ROI for main lines, +4% for derivatives and +5% for player props. Apply any stricter active market floor. |
| B. Probability advantage | Target at least 2 percentage points above offered-price break-even. For standard push-capable straight bets, compare win probability conditional on a decision to break-even and compute ROI as win probability times net decimal payout minus loss probability. |
| C. Calibration | Chronological out-of-sample validation for the market and settlement scope. |
| D. Injuries | Confirm material availability, role and starter assumptions. |
| E. Weather | Stress-test material changes to wind, precipitation and venue conditions. |
| F. Line sensitivity | Reprice reasonable line and odds moves and record the acceptable price. |
| G. Freshness | Verify the exact still-available sportsbook line and capture time. |
| H. Market uncertainty | Require market-specific evidence for props, thin markets and derivatives. |
| I. Robustness | Require positive edge under reasonable parameter and scenario changes. |

Use uncertainty-adjusted quarter-Kelly as a maximum, with caps of **1% per wager, 1.5% per game and 3% per slate**, and enforce tighter active limits. Use a 100-unit reference unless the user supplies a bankroll. Aggregate game and slate exposure across the proposed card and account for correlated positions and parlays. Stakes require evidence for the settlement distribution and every filter. Report zero stakes for held, rejected and research-only rows. Existing engine stake suggestions must pass these V2 limits before entering the official card.

## Section 14 — Master Pipeline V2.0 report

Use all nine sections in this order for every scope. Keep sections visible when evidence or execution is incomplete.

- **A. NFL Pipeline Execution Summary** — Eastern scope and capture times, run IDs, scheduled and modeled games, sources, markets, candidate and verdict counts, stage receipts and missing evidence.
- **B. NFL Game Projection Board** — kickoff, confirmed or pending starters, weather and venue, projected score, fair spread and total, captured market lines, home and away win probabilities or regulation lead and tie probabilities where applicable. State the spread sign convention and compare pre-lock changes to the timestamped baseline when available.
- **C. Master NFL Value Board** — rank verified rows by estimated ROI and show risk-adjusted ordering where supported. Include exact selection, book, line, odds, capture time, model win/push probabilities, edge, ROI and confidence. Show separately priced retail fallbacks and supporting game-log hit counts where verified.
- **D. Top NFL Bet Analysis** — for approved wagers show break-even and fair price, maximum acceptable price, ROI math, key matchup and injury factors, weather, scenario sensitivity, confidence and stake.
- **E. Best Opportunity by Market** — cover moneyline, spread, game total, team total, first half, first quarter, passing, rushing, receiving, touchdown and best overall risk-adjusted value. Show **NO BET** for empty categories.
- **F. Rejected Opportunity Board** — summarize held and rejected rows by filter and link the detailed board. Include provisional rows with their evidence gaps and zero stakes.
- **G. Final Bet Card** — show only fully qualified wagers, exact sportsbook quotes, ROI and capped stake. Include regular and alternate-floor same-game and cross-game parlay results beneath this section. Mark unavailable or out-of-scope constructions explicitly.
- **H. Portfolio Analysis** — report wager count, per-game and per-slate exposure, supported portfolio EV, overlap, correlation and downside scenarios. Mark unavailable estimates.
- **I. Final Verdict** — report strongest overall, moneyline, spread, total, player prop and model-reliability opportunities where qualified, plus approved count, exposure and data/model confidence. If the card is empty, report **NO BET — INSUFFICIENT VERIFIED EDGE**.

For actionable parlays, require one eligible book's complete executable ticket with exact legs, price, quote time and settlement rules. Evaluate compatible regulation-score legs on shared draws and require a validated joint player-stat model for player-prop tickets. Price pushes and voids under the ticket rules. Report unsupported constructions as unstaked research without actionable ROI. For alternate floors, show exact-line hit counts, sample windows, role changes and overlap where verified.

## Save and verify the run bundle

Save a dated, non-overwriting Desktop copy under the actual configured Windows Desktop folder, using a run-specific suffix if needed. Include README with slate, scope, kickoff window, Eastern timestamp, run ID, publication verdict, source and output index, and missing stages. Include the scoped report, execution report, best-bets and alternate-floor reports when produced, and organize copied artifacts into `Game_Scripts/`, `Matchup_Rankings/`, and `Raw_Outputs/`. Copy the manifest, summary, relevant weather evidence and sanitized execution or simulation receipts. Link large intermediates to their original run directory. If an earlier run is included for comparison, label it separately with its own date and run ID.

Compare copied files with their source by size or hash, verify readable nonempty files and record the actual count and roster-validation result in the README or manifest. Report the Desktop path and link the main report and index. If a stage or the Desktop copy fails, report what actually completed and where it was saved. File counts, fixture numbers, prices, probabilities and stakes from examples are never defaults.
