---
name: nfl-master-betting-pipeline
description: Run the live Outlier NFL pipeline for a slate, kickoff window, or matchup and deliver the standard projections, value boards, parlay sections, and verified Desktop bundle.
---

# Run NFL pipeline

Use this workflow for "Run NFL pipeline," including single-matchup, kickoff-window and pre-lock refresh requests. Interpret slate dates and kickoff windows in America/New_York. If the user specifies a matchup, resolve the actual event and limit the report to that matchup. With no scope specified, use the current Eastern slate. Treat a follow-up to resume as continuation of the named run, preserving its scope.

## Execute and verify

1. Complete the repository sync check and use an isolated checkout and output root outside repository data and calibration. Use the canonical saved Outlier session through the supported NFL client. Keep paid reasoning off unless explicitly requested. Run the authenticated deterministic pipeline with refreshed team tape and game scripts via `outlier_nfl.pipeline.NflPipeline.run` or its CLI. Set an explicit date, output root, and supported window where appropriate. With the Python entry point pass `reports_dir` outside the checkout and `write_latest=False`. Supply the supported local saved session to `OutlierNflApiClient(session_path=...)` when the isolated checkout cannot discover it. Keep authentication material out of the bundle and logs.
2. Verify the manifest, run ID, stage receipts, market timestamps, publication status, and exact event IDs before analysis. A window may contain games outside the requested scope. Filter any wider ingestion by event ID before modeling, ranking or exporting the requested board, and disclose its scope. For a pre-lock refresh, record actual capture time, compare against the earlier dated bundle, and recheck lines and inactives. After kickoff, preserve replay or retrospective status and use pregame evidence only when its provenance supports that label. Keep existing publication and staking gates.
3. Confirm current starters, same-team game counts, official availability, roof and kickoff weather, plus same-team pregame game logs. Use the matching run artifacts and the `nfl-game-script` roster and matchup workflow. Apply the automated roster text validator to the report and README. Hold rows whose player, team, line, odds or timing cannot be verified.
4. Run the 50,000-trial drive simulation for the selected games when a runnable V2 engine and verified inputs are available. Record implementation, seed, trial count and input provenance. If unavailable, mark the simulation **NOT EXECUTED**, report verified context and supported deterministic output, and identify the missing component. A market-derived score or a game-specific script for another matchup cannot substitute for the simulation.

## Standard user-facing report

Open with slate or matchup, Eastern capture time, run ID, games covered, simulation status and publication verdict. Use these six sections in order. Keep unavailable sections visible with the reason.

1. **Game projections and pre-lock updates.** One row per game with starting QBs and same-team games, venue and current wind/gusts/precipitation, projected score, fair and market spread, fair and market total, home/away win probability, and material injury, weather and line changes. Include tie probability when modeled and state the spread sign convention.
2. **Best value props and lines by game.** Rank verified straight-market rows by game. Include confidence or verdict, exact selection and scope, best eligible book and odds, retail fallback with its own exact line and odds, model win/push probability and estimated ROI for each verified quote, plus game-log values, sample count, hit counts, mean and last-three average where available. Show stale or moved lines as deltas. Reprice changed lines before ranking them. Do not infer value or confidence from hit rate alone.
3. **Regular same-game parlays.** Show one qualifying construction per game when available. List exact legs and the shared game script.
4. **Alternate-floor same-game parlays.** Show qualifying alternate lines and distinct players. Report exact-line historical hit counts, sample windows and role changes. Follow the active leg limits and eligibility rules.
5. **Slate-wide regular parlays.** Show the core cross-game option, and a longer option only when active rules and evidence allow it. For a single game, mark slate-wide sections inapplicable.
6. **Slate-wide alternate-floor parlays.** Use eligible alternate lines and show leg and game overlap. Keep the section visible when evidence or rules prevent a ticket.

For every parlay, clearly label the price as a verified executable quote from a single eligible book or an illustrative reference. Require a verified exact ticket price and a supported joint distribution, including same-game dependence and push treatment, before quoting actionable EV, a fair ticket price, or stake. Avoid assumed copula boosts, mixed-book ticket prices treated as executable, and "safe" or "true probability" language. Research candidates remain unstaked. If there are no validated plays, say so plainly.

## Save and verify the run bundle

Save a dated, non-overwriting Desktop copy under the actual configured Windows Desktop folder, using a run-specific suffix if needed. Include README with slate, scope, kickoff window, Eastern timestamp, run ID, publication verdict, source and output index, and missing stages. Include the scoped report, execution report, best-bets and alternate-floor reports when produced, and organize copied artifacts into `Game_Scripts/`, `Matchup_Rankings/`, and `Raw_Outputs/`. Copy the manifest, summary, relevant weather evidence and sanitized execution or simulation receipts. Link large intermediates to their original run directory. If an earlier run is included for comparison, label it separately with its own date and run ID.

Compare copied files with their source by size or hash, verify readable nonempty files and record the actual count and roster-validation result in the README or manifest. Report the Desktop path and link the main report and index. If a stage or the Desktop copy fails, report what actually completed and where it was saved. File counts, fixture numbers, prices, probabilities and stakes from examples are never defaults.
