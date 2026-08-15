# PR Review: #19 — feat: split team totals from game totals

**Reviewed**: 2026-07-12
**Author**: DaSilvaDub
**Branch**: feat/split-team-totals → master
**Decision**: APPROVE with comments

## Summary
Clean split of `game_totals.csv` into separate `game_totals`/`team_totals` streams via a shared `build_totals(kind=)` engine, with every consumer (pack, daily_job, runners A–E, prompts) updated consistently. Logic is correct and fully covered by passing tests (294/294 on the PR head, including the 4 new/updated test files). Two non-blocking issues: a leftover one-shot patch script left in the repo, and a missing test for the new team-totals branch in `daily_job.py`.

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM
1. **Leftover one-shot applicator script committed to repo root** — `_apply_split_team_totals.py` (421 lines) is a throwaway script used to mechanically patch `game_totals.py` during development. It has already been applied (the patched code is committed directly in `game_totals.py`), so the script now serves no purpose. Its final step also does `exec(open(ROOT / "_apply_split_team_totals_runner_common.py")...) if exists else None` — that sibling file doesn't exist in this tree, so running the script today would silently skip patching `runner_common.py` while printing "OK core paths+game_totals", which reads as success. Recommend deleting `_apply_split_team_totals.py` before merge.

2. **No test coverage for the new team-totals branch in `daily_job._count_pack_rows`** — `daily_job.py:162-168` now sums `count_actionable_team_totals` when `team_totals.csv` exists, but `tests/test_daily_job.py::test_daily_job_runs_desk_for_actionable_totals_only` only ever writes `game_totals.csv`. There's no test asserting the desk runs (or the pack-row count is correct) when only `team_totals.csv` has actionable rows. Recommend adding a `team_totals.csv`-only variant of that test.

### LOW
1. Section-heading relabeling for `team_totals.md` in `pack.write_pack` uses a partial string `.replace("# Game totals", "# Team totals")`, while the equivalent replacement in `build_briefing` uses the full heading string `.replace("# Game totals projection board", "### Team totals")`. Both happen to work correctly given `_format_game_totals_md`'s fixed output, but the inconsistency is fragile — if that heading text ever changes, the section-file `.replace` would silently no-op instead of erroring. Not blocking; consider passing an explicit title into `_format_game_totals_md` instead.
2. `runner_common._parse_totals_csv` always raises `"{label} header does not match GAME_TOTALS_HEADER"` verbatim, even when `label="team_totals.csv"` — harmless since `TEAM_TOTALS_HEADER is GAME_TOTALS_HEADER`, but the wording is momentarily confusing in a team-totals stack trace.

## Validation Results

| Check | Result |
|---|---|
| Compile (`python -m compileall -q outlier_scrapers`) | Pass (on PR head, via isolated worktree) |
| Targeted tests (test_game_totals, test_pack, test_paths, test_runner_common, test_daily_job) | Pass — 107/107 |
| Full test suite | Pass — 294/294 |
| Lint | Skipped — no flake8/ruff/pre-commit config found in repo |
| Type check | Skipped — no mypy config found in repo |

Note: local canonical checkout was already hard-reset to `origin/master` by this repo's mandatory `report-sync.ps1`, so validation was run against `origin/feat/split-team-totals` (73eacbb) in a disposable `git worktree`, not the working tree.

## Files Reviewed
- Modified: `.agent-log/HANDOFF.md`, `outlier_scrapers/{c_research,claude_reasoning,claude_synthesis,daily_job,game_totals,games,gemini_research,line_movement,pack,paths,reasoning,run_desk,runner_common}.py`, `prompts/{A,B,C,D,E}.md`, `tests/{test_game_totals,test_pack,test_runner_common}.py`
- Added: `_apply_split_team_totals.py` (flagged above), `tests/test_paths.py`
