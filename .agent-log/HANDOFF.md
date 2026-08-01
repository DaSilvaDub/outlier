# Handoff

- **Last Commit SHA**: `c3b9203c966ac8ff394186a3d21925cde26c783b` (merged PR #80).
- **Pull Request**: https://github.com/DaSilvaDub/outlier/pull/80 (merged).
- **Files Touched**:
  - `outlier_scrapers/cards.py` — surfaces ambiguous TEAM_PROP stats-side selection and keeps hit-rate mapping centralized.
  - `tests/test_cards.py` — covers ambiguity propagation and preserves `h2h_pct` through game-card assembly.
- **Verification**:
  - `python -m pytest tests/test_cards.py tests/test_games.py -q`: 64 passed.
  - `ruff check outlier_scrapers/cards.py tests/test_cards.py`: passed.
  - Fresh GitHub Offline Pytest and Static Type Checking runs: passed.
  - All PR review threads resolved before merge.
- **Next Steps**: None; confirm canonical sync and no remaining open PRs.
