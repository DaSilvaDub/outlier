# Handoff

- **Last Commit SHA**: `d0df4655915cd37df9ccdb0fa05b8d3e4bad4e4c` on `feat/probable-pitchers-scraper`
- **PR**: https://github.com/DaSilvaDub/outlier/pull/83
- **Files Touched**:
  - `outlier_scrapers/probable_pitchers.py` (new) — MLB Stats API scraper (`statsapi.mlb.com`, no auth) for probable starting pitchers + confirmation status, normalized into a per-team lookup
  - `outlier_scrapers/paths.py` — added `probable_pitchers_latest()`
  - `outlier_scrapers/refresh.py` — new `--probable-pitchers` flag
  - `outlier_scrapers/daily_job.py` — wired probable-pitchers step into the explicit refresh sequence (MLB-only; no-ops for other leagues)
  - `outlier_scrapers/game_totals.py` — new informational `starter_flags` column (e.g. `STARTER_UNCONFIRMED:BAL`), does not affect `actionable` gating
  - `outlier_scrapers/pack.py` — loads the probable-pitchers lookup and threads it into `build_game_totals`/`build_team_totals`
  - `tests/test_probable_pitchers.py` (new), `tests/test_game_totals.py` — 11 new tests
- **Verification**:
  - `pytest -q`: 664 passed
  - `mypy` + `ruff check`: clean on all touched/new files
  - Live smoke test against the 2026-08-04 MLB slate: 15 games exported; confirmed the data is genuinely time-sensitive — Baltimore's starter went from `TBD` (at earlier report time) to confirmed `Cade Povich` on a later rescrape
- **Next Steps**:
  - PR #83 ready for review/merge
  - Open design question, deliberately left undecided in this PR: should `STARTER_UNCONFIRMED` actually gate `actionable` in `game_totals.py`, or stay informational-only (current behavior)? That's a betting-policy call, not a code call.
  - Natural follow-up: extend the probable-pitcher lookup into player-prop cards (`cards.py`), not just game/team totals.

---

## Injury flags: date validation & type safety (2026-08-01)

- **Last Commit SHA**: `22ce08453cfd107386ee35a1a1eb2b8ebcdfc27d` on `feat/richer-injury-flags`
- **PR**: https://github.com/DaSilvaDub/outlier/pull/81
- **Files Touched**:
  - `outlier_scrapers/pack.py` — date validation in `_injury_return_date` & type safety guards for scalar fields in `_format_injury`
  - `tests/test_pack.py` — strengthened analysis truncation & multi-player separator assertions; added test for unnormalized dates & non-scalar types
- **Verification**:
  - `pytest -q tests/test_pack.py tests/test_games.py`: 117 passed
  - `ruff check outlier_scrapers/pack.py tests/test_pack.py tests/test_games.py`: All checks passed
- **Next Steps**:
  - PR #81 ready for merge after CI checks pass

---

## Master Cards 2-unit floor (2026-08-01)

- **Last Commit SHA**: `a4fd697e137bf3f75d13b038f789dbbd8e72e687`
- **Files Touched**:
  - `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`
  - `.agents/skills/export-manual-outlier-packs/SKILL.md`
  - `tests/test_generate_prompts.py`
- **Verification**:
  - `python -m pytest tests/test_generate_prompts.py -q`: 6 passed
  - `python -m ruff check .agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py tests/test_generate_prompts.py`: passed
- **Next Steps**:
  - None. The `1_Master_Cards_pack_YYYY-MM-DD.txt` generator now includes candidates at 2.0 units or higher and labels the section `2+ Unit Candidates Data`.
