# HANDOFF — 2026-09-27 (Claude, daily automated debug review)

## Last Commit SHA
`e7d9075` — fix(nfl): read a January playoff slate as the previous NFL season

## PR
[#197](https://github.com/DaSilvaDub/outlier/pull/197) — `claude/inspiring-fermat-x2sxto` → master

## Files Touched
- `outlier_nfl/utils.py` — new `nfl_season_for_date()` beside the Eastern-date helpers.
  Sep–Feb resolves to the season in progress, Mar–Aug to the season about to start
  (nflverse convention). Accepts a `datetime`, an ISO timestamp, or a bare `YYYY-MM-DD`;
  returns `None` on unreadable input.
- `outlier_nfl/pipeline.py` — replaced `season_year = int(target_date.split('-')[0])`.
  A `2027-01-10` wild-card slate asked `load_external_metrics()` for the **2027** season,
  a year that has not been played. Caller now skips the fetch and logs when the season
  can't be derived, instead of passing `None` into a parameter typed `int`.
  This was the reported-not-fixed item carried by the 2026-09-25 handoff.
- `outlier_scrapers/pack_selection.py` — dropped the dead `market_type_upper` local in
  `_apply_quality_and_signal_flags()`. #190 moved the projection_side_conflict check
  upstream into `build_row()` and deleted the only consumer (ruff F841).
- `tests/test_nfl_stress.py` — 2 regression tests (playoff/offseason boundaries; datetime,
  ISO-timestamp and bare-date inputs plus the None cases).

## Verification
- Offline suite **906 passed / 43 skipped** (was 904; +2 new tests). NFL suite
  **294 passed / 2 skipped** (was 292/2). `mypy outlier_nfl` clean; ruff clean on all four
  touched files; `compileall` clean; repo-wide ruff 20 → 19.
- The 51 failures / 48 collection errors are **unchanged by this diff** and are all
  `ModuleNotFoundError` (structlog, sqlalchemy, provider SDKs).
- **Sandbox limitation (recurring, 3rd review running):** pypi.org and
  files.pythonhosted.org return **403** from the egress proxy, so declared deps cannot be
  installed. `pytest`/`ruff`/`mypy` were available at `/root/.local/bin` this run. A
  `structlog` stand-in under the scratchpad clears 41 collection errors but `sqlalchemy`
  still gates `pack*`/`verdict*`/`games`/`cards`. **Hosted CI is authoritative.**
- `pack_selection.py` tests are sqlalchemy-gated, so the dead-store removal was verified by
  a normalised bytecode diff of the function (jump targets/line numbers/addresses ignored):
  **10 opcodes removed, 0 added**, all ten that one statement.
- No reasoning models or paid desk calls were invoked (house rule respected).

## Reviewed and found clean this run (no change needed)
- `c6f739b` full-game scope gating: `prop.scope` is a real model field defaulting to
  `"full_game"` and `detect_scope()` only returns the lowercase canonical tokens, so the new
  `in (None, "", "full_game")` guards cannot silently zero out every TIER_1_ANCHOR.
- `scripts/export_nfl_extra_pack.py` (new, 192 lines): exercised end-to-end against a payload
  built from the real `NflPlayerProp` model. Field names align with `to_dict()`, `"records"`
  matches what the pipeline writes, best-book resolution correct, period props filtered,
  header written on zero rows.
- The 4 `mypy outlier_scrapers` findings are **not** bugs: every flagged `float()`/`int()` is
  already inside `try/except (ValueError, TypeError)` (`schema.py:256`,
  `probable_pitchers.py:95`, `game_totals.py:1045`); the 4th is a missing
  `types-python-dateutil` stub.

## Next Steps / Open Items
- Review and merge PR #197.
- **Still open, needs a human call:** `matches_kickoff_window()` falls through to
  `return True` for an unrecognised window token.
- **Still open:** `--window` advertised but no argparse wiring; window runs clobber
  `*_latest.json`.
- **Still open (from 2026-09-24):** the roster gate's bare-proximity pattern treats an
  *opponent* mention as a violation ("leaky WAS secondary vs Jahan Dotson").
- **Repo hygiene, needs a human call:** `.pytest_pr1_tmp/` (334 files) and
  `.worktrees/test-pr97/` (364 files, ~3 MB) are tracked in git and pollute every repo-wide
  grep with stale duplicates — the exact class of confusion the d05eb21 protocol exists to
  prevent. Recommend `git rm --cached -r` plus `.gitignore` entries; not done here because it
  rewrites ~698 tracked paths.
- **Open:** repo-wide ruff reports 19 remaining F401 unused imports (`scratch.py`,
  `script.py`, `append_feedback.py`, `tests/test_challenger_adversarial.py`,
  `tests/test_nfl_roster.py`).
