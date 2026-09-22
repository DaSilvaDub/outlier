# HANDOFF — 2026-09-25 (Antigravity)

## Last Commit SHA
`7a55a11` — feat(nfl-external): complete external metrics adapter package and rule

## PR
[#188](https://github.com/DaSilvaDub/outlier/pull/188) — `fix/nfl-external-missing-stubs` → master

## What Was Done
- **Bug fixed & completed:** `outlier_nfl/external/` stub adapter pattern implemented (`pbp.py`, `schedule.py`, `ngs.py`, `common.py`, `__init__.py`) and wired into `pipeline.py`.
- **Learned rule recorded:** Added Codebase Quirk in `.agents/AGENTS.md` documenting the required adapter stub pattern.
- **NFL pipeline ran successfully** for Sunday 2026-09-27:
  - 14 games found
  - 35,527 player props extracted (10,466 consensus, 316 Tier-1 anchors)
  - 716 matchup-tagged props
  - 14 matchup game scripts generated under `reports/NFL/`

## Generated Game Script Files
| Matchup | File |
|---------|------|
| TEN @ NYG | `reports/NFL/2026-09-27_TEN_NYG_Game_Script.md` |
| NYJ @ DET | `reports/NFL/2026-09-27_NYJ_DET_Game_Script.md` (largest, 15KB) |
| MIN @ TB | `reports/NFL/2026-09-27_MIN_TB_Game_Script.md` |
| HOU @ IND | `reports/NFL/2026-09-27_HOU_IND_Game_Script.md` |
| CIN @ PIT | `reports/NFL/2026-09-27_CIN_PIT_Game_Script.md` |
| NE @ JAX | `reports/NFL/2026-09-27_NE_JAX_Game_Script.md` |
| LAC @ BUF | `reports/NFL/2026-09-27_LAC_BUF_Game_Script.md` |
| BAL @ DAL | `reports/NFL/2026-09-27_BAL_DAL_Game_Script.md` |
| ARI @ SF | `reports/NFL/2026-09-27_ARI_SF_Game_Script.md` |
| SEA @ WAS | `reports/NFL/2026-09-27_SEA_WAS_Game_Script.md` |
| CAR @ CLE | `reports/NFL/2026-09-27_CAR_CLE_Game_Script.md` |
| LAR @ DEN | `reports/NFL/2026-09-27_LAR_DEN_Game_Script.md` |
| KC @ MIA | `reports/NFL/2026-09-27_KC_MIA_Game_Script.md` |
| LV @ NO | `reports/NFL/2026-09-27_LV_NO_Game_Script.md` |

## Normalized Outputs Written
All under `data/NFL/normalized/`:
- `nfl_games_2026-09-27.json`, `nfl_props_2026-09-27.json`
- `nfl_calibrated_props_2026-09-27.json`, `nfl_high_prob_props_2026-09-27.json`
- `nfl_rosters_2026-09-27.json`, `nfl_matchup_scripts_2026-09-27.json`
- `nfl_matchup_props_2026-09-27.json`, `nfl_external_metrics_2026-09-27.json`
- `summary_2026-09-27.json`

## Next Steps
- Merge PR #188 to master
- Review the high-probability Tier-1 anchor props (316 total) for desk analysis
- `reports/NFL/` game scripts are ready for manual review / Desk2 prompt routing
- External metrics (`pbp`, `schedule`) are currently stubs — wire up real providers when available

## Daily Debug Review: NFL Team Totals Misclassified as Game Totals (Claude) - 2026-09-23
1. **Last Commit SHA**: rebased onto master after #182; branch `claude/inspiring-fermat-bzoq3i` (PR #185: https://github.com/DaSilvaDub/outlier/pull/185).
2. **Files Touched (still distinct after #182)**:
   - `outlier_nfl/games.py`: `extract_game_lines()` tests the game-total branch before the team-total branch, and `"TOTAL"`/`"TOTALPOINTS"` belong to both `GAME_TOTAL_PROPOSITIONS` and `TEAM_TOTAL_PROPOSITIONS`. A market the feed explicitly typed `TEAM_PROP` whose proposition read "Total" / "Total Points" / "TOTALPOINTS" satisfied `is_game_total()` and was emitted as a GAMELINE TOTAL with `team=None` -- one team's total published beside the real game total. The branch now declines markets typed `TEAM_PROP` so they fall through to the team-total branch.
   - `outlier_nfl/games.py` (follow-up): guard requires BOTH `TEAM_PROP` type AND a resolved team via `resolve_outcome_team()`, so a game total a provider mislabels `TEAM_PROP` (no team attribution) stays a game total instead of vanishing. Branch 3's resolution is extracted and reused so the guard and the branch cannot disagree.
   - `tests/test_nfl_normalizer.py`: regression coverage for colliding feed spellings plus `test_game_total_mislabelled_team_prop_is_still_a_game_total`.
3. **Superseded by #182 (dropped on rebase)**:
   - `outlier_nfl/matchup.py` / `tests/test_nfl_matchup.py` changes that filtered team totals via `PROP_TEAM_TOTAL_POINTS` on canonical `market`. Master already reads team totals through `is_team_total(proposition)` (#182), which is the wider canonical predicate; keeping both would duplicate and risk divergence.
   - #182's version is not merely equivalent, it is **strictly better**, and dropping mine avoided a regression: `games.py` branch 3 stamps `market=PROP_TEAM_TOTAL_POINTS` (`"POINTS"`) on *every* `TEAM_PROP` line regardless of what the market is, a team **touchdown** total included. A `market`-based filter would therefore read a 3.5 touchdown line as a projected score; `is_team_total(proposition)` rejects it. Confirmed on the rebased branch (touchdown total -> projection stays at the 24.0 default).
   - #182 also caught a **third** instance of the same raw-`proposition` defect, in `outlier_nfl/calibration.py::extract_game_script_context()`, which this review missed. There `home_tt` fell back to 27.0, below the 28.0 deficit-risk threshold, so `DEFICIT_VOLUME_RISK` and its road-underdog RB haircut were silently dead on any affected feed.
4. **Verification** (post-rebase, on head `88179d3`): `pytest tests/test_nfl_*.py` 287 passed / 2 skipped; `ruff check` clean; `mypy outlier_nfl` no issues in 18 source files; all 4 hosted CI checks green (core, provider, typecheck, Codacy) with `mergeable_state: clean`. Composition verified end-to-end through `normalize_game_markets` -> `build_matchup_script`: this branch's classification feeding master's `is_team_total` reader yields the real market numbers on all six feed spellings, and a team touchdown total is still correctly refused.
5. **Next Steps**:
   - Review and merge PR #185 once rebase checks are green.
   - Open, not fixed: repo-wide `ruff check` F401 unused imports; `--window` runs clobber `*_latest.json`; `matches_kickoff_window()` returns True for unrecognized tokens.
   - **New, flagged not fixed (schema decision, not a bug fix):** `games.py` branch 3 stamps `market=PROP_TEAM_TOTAL_POINTS` on any `TEAM_PROP` market, so a team touchdown/other non-points team prop is published carrying `market="POINTS"`. Nothing is broken today because the readers filter on `proposition` after #182, but it is a live trap for any future code that filters team props on `market`. Closing it properly needs real canonical codes for non-points team props.
## Daily Automated Debug & Code-Health Review (Claude) - 2026-09-22
1. **Last Commit SHA**: rebased onto master after #185; branch `claude/inspiring-fermat-yd5et4` (PR #184: https://github.com/DaSilvaDub/outlier/pull/184).
2. **Files Touched**:
   - `outlier_nfl/utils.py`: `to_eastern_datetime()` now stamps a naive datetime as UTC before `astimezone()`. Previously a naive value was read as the *host's* local clock, so the Eastern slate date depended on the machine: a Sunday 8:15pm ET kickoff (00:15 UTC Monday) resolved to `2026-09-13` on a UTC runner and `2026-09-14` on a US Pacific workstation. `parse_iso_datetime()` already normalised the ISO-string path; this closes the same hole on the datetime-object path. Every current caller passes a string or an aware datetime, so no existing behaviour changes.
   - `tests/test_nfl_stress.py`: two tests. `test_to_eastern_datetime_naive_is_utc_under_a_non_utc_host_tz` is the real regression guard — it runs the assertion in a subprocess with `TZ` forced, because on a UTC host the pre-fix and post-fix behaviour are indistinguishable. `test_to_eastern_datetime_reads_a_naive_datetime_as_utc` covers the host-independent assertions.
3. **Verification** (pre-rebase): regression fails against pre-fix utils on TZ=UTC; `pytest tests/test_nfl_*.py` 278 passed / 2 skipped; prior CI green on `00d8d0e`.
4. **Gotcha for the next agent**: `git stash push -- <path>` silently no-ops once a change is committed, so a "stash, run, pop" red-before/green-after check quietly tests the *fixed* code and reports a false pass. Use `git show <base-sha>:<path>` to materialise the pre-fix file instead.
5. **Next Steps**:
   - Review and merge PR #184 once rebase checks are green.
   - Reported, not fixed: `matches_kickoff_window()` falls through to `return True` for an unrecognised window token.
   - Also open: `--window` CLI flag advertised but no argparse wiring exists.

## Daily Debug Review: Matchup Team-Total Reader (Claude) - 2026-09-21
1. **Last Commit SHA**: `a38d4b3` on branch `claude/inspiring-fermat-xdn5p3` (PR #182: https://github.com/DaSilvaDub/outlier/pull/182).
2. **Files Touched**:
   - `outlier_nfl/matchup.py`: `_market_context()` selected a game's team totals by testing `NflGameLine.proposition` against four literal spellings, but `proposition` carries the raw feed string (`games.py` sets `proposition=str(raw_prop)` while pinning the canonical code on `market`). The sibling SPREAD and TOTAL filters in the same function already match on canonical `market`; team totals were the one exception. A feed spelling the market `TEAM_TOTAL_POINTS` / `Team Total Points` / `team_total` -- all of which normalize into real TEAM_PROP lines carrying real numbers -- was dropped, and the script published the hardcoded 24.0/21.0 placeholder as the projected score into `nfl_matchup_scripts_*.json` and `reports/NFL/*_Game_Script.md`. Now matched through `outlier_nfl.config.is_team_total()`, the predicate the normalizer itself uses: it accepts every points-total spelling and still rejects non-points TEAM_PROPs (TEAM_TOTAL_TOUCHDOWNS). Strict widening -- the four old spellings all still match.
   - `outlier_nfl/calibration.py`: same defect in `extract_game_script_context()`, found by Copilot's review on #182 and verified -- and the worse of the two. That reader feeds `apply_game_script_calibration()` over every slate game, and its fallback pins `home_tt` at 27.0, *below* the 28.0 deficit-risk threshold, so `away_deficit_risk` can never fire: DEFICIT_VOLUME_RISK and its road-underdog RB rushing haircut are silently dead on any affected feed while the real quoted totals sit unread in the same list. Same `is_team_total()` fix.
   - `tests/test_nfl_calibration.py`: 2 regression tests on the calibration path (widening across seven proposition spellings incl. `away_deficit_risk`; non-points TEAM_PROPs ignored).
   - `tests/test_nfl_matchup.py`: 2 regression tests (score follows the team total across five proposition spellings; a team TD total never becomes the projected score).
3. **Verification**:
   - 280 passed / 2 skipped across `tests/test_nfl_*.py` (was 276/2). Both widening tests fail on their pre-fix predicate and pass after.
   - Full offline suite 1,062 passed (was 1,058), with the same dependency-driven collection errors as before the change. PyPI is unreachable in this cloud sandbox (`pytest`, `ruff` and `mypy` cannot be installed), so the suite was run under a local minimal pytest-compatible runner plus a `structlog` stand-in, both kept outside the repo; `sqlalchemy`, `openai` and `google-genai` modules stay uncollectable. **Hosted CI is authoritative.**
   - End-to-end offline pipeline run against `tests/fixtures/nfl`: status OK, 3 matchup scripts, 3 game-script reports, 0 errors.
   - `python -m compileall` clean across `outlier_nfl`, `outlier_scrapers`, `scripts`, `tests`.
   - Roster registry cross-checked programmatically: 32 teams, no player on two depth charts, no `OFFSEASON_MOVES_2026` entry contradicting a chart.
   - No paid reasoning models were invoked (house rule respected).
4. **Next Steps**:
   - PR #182 open against master: core/provider/typecheck green on `a38d4b3`, Copilot's one finding fixed and its thread resolved. Awaiting human review.
   - **Reported, not fixed** (report-semantics call for a human): when a game genuinely has no team totals or total in the feed, `_market_context()` still returns hardcoded 24.0/21.0/45.5 and `render_matchup_markdown()` prints them as real lines ("**Total lean:** UNDER 45.5") with nothing marking them as defaults. Both committed live reports (`2026-09-20_IND_KC`, `2026-09-21_NYG_LAR`) show projected scores that came from the spread/total fallback rather than team totals.
   - Minor, no change made: `build_matchup_script()` derives projected scores with `round()`, whose banker's rounding turns a 25.5/22.5 team-total pair into 26-22 (margin 4 against a 3.5 spread). Cosmetic; intended tie-breaking is not clear from the code.

## NFL Roster Accuracy: Dolphins Starting QB Malik Willis & Tua Tagovailoa Relocation (Gemini) - 2026-09-21
1. **Last Commit SHA**: `827ce4e` on branch `fix/roster-tua-dolphins-update` (PR #183: https://github.com/DaSilvaDub/outlier/pull/183)
2. **Files Touched**:
   - `outlier_nfl/roster.py`: Mapped Malik Willis as starting QB for MIA in `NFL_2026_FULL_DEPTH_CHARTS`. Registered Tua Tagovailoa on ATL with former team MIA and registered Malik Willis on MIA with former teams GB/TEN in `OFFSEASON_MOVES_2026`.
   - `outlier_nfl/tape/prior_week_tape.json` & `tests/fixtures/nfl/prior_week_tape.json`: Updated MIA unit tape qb to Malik Willis and te to Julian Hill.
   - `tests/test_nfl_roster.py`: Added assertions verifying Malik Willis on MIA, Tua Tagovailoa on ATL, and text validation catching Tua on Dolphins hallucinations.
   - `.agents/AGENTS.md` & `.agents/skills/nfl-game-script/SKILL.md`: Added MIA Core Anchor to documentation and skill invariants.
   - `reports/NFL/2026-09-21_NYG_LAR_Game_Script.md`: Re-rendered with verified active starters.
3. **Verification**:
   - 294/294 tests passed (`pytest -k nfl`).
   - `validate_analysis_text_for_roster_errors` confirmed 0 errors on generated game scripts.
   - PR #183 opened targeting `master`.
4. **Next Steps**:
   - Review and merge PR #183.
   - House rules respected: paid reasoning models kept strictly OFF.
