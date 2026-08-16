# Handoff

- **Last Commit SHA**: pending reviewer follow-up on `fix/pipeline-debug-20260816`
- **PR**: https://github.com/DaSilvaDub/outlier/pull/103
- **Files Touched**:
  - `outlier_scrapers/games.py`
  - `outlier_scrapers/t30_reprice.py`
  - `outlier_scrapers/pack.py`
  - `tests/test_games.py`
  - `tests/test_t30_reprice.py`
  - `tests/test_slate_strategy.py`

**What broke**
- Evening games scrape used local "today" only. After tonight's games started, it wrote an empty `ok` payload and overwrote `*_games_latest.json`.
- That emptied `game_totals.csv` / `team_totals.csv` on the 2026-08-16 pack.
- T-30 `first_lock_at` used `min(all event_starts)`, including leftover 2025-08-26 dates in `original_t30_context.json`, so the window was a year off.

**What was fixed**
- Games scrape auto-advances to tomorrow when today's pregame window is empty.
- Empty scrapes no longer clobber a healthy latest artifact.
- T-30 first lock is scoped to the pack date (or the modal slate date).
- New T-30 freezes drop off-date event starts.
- Live restore: MLB 6651 game records, WNBA 1372; pack rebuilt with 10 game totals + 22 team totals. Computed first lock is now `2026-08-16T16:15:00+00:00`.

**Next Steps**
- Commit/push `fix/pipeline-debug-20260816` and open a PR if not already done.
- Existing `original_t30_context.json` still contains 2025 dates (freeze is immutable). Runtime first-lock filtering handles that.
- `manifest.json` is derived and was cleared by the standalone pack rebuild; daily_job writes it.
- OneDrive left `packs/.2026-08-16.feedback-backup-*` (WinError 5). Safe to delete later.
