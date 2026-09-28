# Handoff

**Last Commit SHA**: see branch `claude/nifty-einstein-gbtl85`
**Pull Request**: https://github.com/DaSilvaDub/outlier/pull/198

**Files Touched**:
- `outlier_nfl/tape_nflverse.py` (new): rebuilds `data/NFL/tape/prior_week.json` from nflverse stats_team_week + schedules; before-date cutoff, optional last-N, role fields preserved, atomic write + `.prev` backup.
- `outlier_nfl/pipeline.py`: `--refresh-tape`, `--tape-last-n` (fetch failure keeps old tape).
- `scripts/pull_nflverse_tape.py` (new CLI), `tests/test_nfl_tape_nflverse.py` (6 tests).
- `.agents/skills/nfl-game-script/SKILL.md` runbook; `docs/nfl-data-sources.md` data-source survey.

**Validation**: `pytest tests/test_nfl_*.py` 298 passed / 2 skipped; ruff + mypy clean on touched files; live pull wrote 32 teams.

**Data state (Drive tape folder)**: `prior_week.json` = nflverse Weeks 1-3 box-score tape (uploaded manually 2026-09-28); `prior_week_w1_backup.json` = original Week 1; `prior_week_w1w2_search_blend.json` = superseded search-based blend.

**Next Steps** (see docs/nfl-data-sources.md "Suggested integration order"):
1. Auto roles/inactives from nflverse depth_charts + injuries (LAR TE1 is Colby Parkinson, tape still says Higbee).
2. Pass-rush grade from pfr_advstats pressures; qb_grade from ESPN QBR.
3. Fill outlier_nfl/external/ ngs/pbp/schedule stubs.
