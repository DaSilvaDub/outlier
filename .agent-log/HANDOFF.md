1. **Last Commit SHA**: 237a6ee (branch `claude/pipeline-pack-accuracy-check-tj3czl`)
2. **Files Touched**: outlier_scrapers/pack_accuracy.py (new), tests/test_pack_accuracy.py (new), outlier_scrapers/feedback.py, docs/feedback-loop.md
3. **What landed**: By-pack-type accuracy grading. `pack_snapshot_memberships.source` (the capture lane) now reaches reporting via a pack-vintage join in `_joined_rows`; `pack_type.csv` added to the nightly feedback report; new `python -m outlier_scrapers.pack_accuracy --date <slate>` audits one pack lane by lane and reports generated-but-never-captured lanes as NO_COVERAGE.
4. **Next Steps**:
   - Open the PR (GitHub MCP was unauthenticated in the remote session): https://github.com/DaSilvaDub/outlier/pull/new/claude/pipeline-pack-accuracy-check-tj3czl
   - Run the audit locally against the real ledger for 2026-08-25; the packs and calibration DB are gitignored so no remote agent can grade them.
   - Open work: `feedback._load_pack_rows` still never reads alt_player_props.csv, alt_team_totals.csv, the *_alt_spreads / *_alt_bankroll_props lanes, or any *_parlays file. Those CSVs also lack the `selection` column `_snapshot_from_pack_row` requires and `results._grade_row` parses, so capture needs a canonical selection string synthesized at write time.
