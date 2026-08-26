1. **Last Commit SHA**: 890a0dc (branch `claude/pipeline-pack-accuracy-check-tj3czl`)
2. **Files Touched**: outlier_scrapers/pack_accuracy.py (new), tests/test_pack_accuracy.py (new), outlier_scrapers/feedback.py, outlier_scrapers/results.py, outlier_scrapers/alt_team_totals.py, tests/test_feedback.py, tests/test_results.py, docs/feedback-loop.md
3. **What landed**: By-pack-type accuracy grading. `pack_snapshot_memberships.source` (the capture lane) now reaches reporting via a pack-vintage join in `_joined_rows`; `pack_type.csv` added to the nightly feedback report; new `python -m outlier_scrapers.pack_accuracy --date <slate>` audits one pack lane by lane and reports generated-but-never-captured lanes as NO_COVERAGE.
4. **Next Steps**:
   - Open the PR (GitHub MCP was unauthenticated in the remote session): https://github.com/DaSilvaDub/outlier/pull/new/claude/pipeline-pack-accuracy-check-tj3czl
   - Run the audit locally against the real ledger for 2026-08-25; the packs and calibration DB are gitignored so no remote agent can grade them.
   - Alt boards are now captured and graded (alt_player_props, alt_team_totals, {mlb,wnba}_alt_spreads, {mlb,wnba}_alt_bankroll_props), each with its own capture source. `results._grade_row` no longer requires a line, so moneylines settle for the first time.
   - Open work: `*_parlays` lanes remain uncaptured by design (a parlay needs every leg settled; cross-game legs sit on different events). Grading them would need a multi-event settlement path in `outlier_scrapers.results` plus a parlay-shaped decision row.
   - Backfill note: the alt lanes only start accruing history from the next capture. Re-running `capture-pack` over retained pack dirs would backfill them where the pack CSVs still exist on disk.
