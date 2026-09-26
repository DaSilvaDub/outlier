# Handoff

**Last Commit SHA**: 13d7d46
**Files Touched**:
- outlier_scrapers/pack_selection.py (migrated _projection_side_conflicts logic upstream inside uild_row to drop contradicting rows entirely rather than flagging them, correctly scoped to player props)
- 	ests/test_pack.py (updated 	est_player_projection_mean_against_selected_side_discarded_upstream to verify upstream discard with proper distribution schema)

**Next Steps**:
- The user can review the amended changes and push the branch eat/upstream-projection-filter to remote, then open a PR.
- Continue addressing the other suggested tasks, such as updating the schema warnings in CANDIDATES_HEADER.
