# Handoff

- **Last Commit SHA**: `35c466c3d2d11570aff6099712a2eb113b14d129` on `feat/richer-injury-flags`
- **PR**: https://github.com/DaSilvaDub/outlier/pull/81
- **Files Touched**:
  - `outlier_scrapers/pack.py` — richer `_format_injury` (body, return date, analysis @ 160 chars)
  - `tests/test_pack.py` — updated schema test + new richer-flags test
  - `tests/test_games.py` — e2e asserts body part
  - `.claude/plan/richer-injury-flags.md`
- **Next Steps**:
  - Merge PR #81 after CI green
  - Optional: hasNews-first / IL filter for huge MLB lists
  - Next pack rebuild surfaces richer `injury_flags` automatically
