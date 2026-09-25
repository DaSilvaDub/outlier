# HANDOFF — 2026-09-25 (Antigravity)

## Last Commit SHA
`93622b5` — fix(nfl-external): add missing pbp and schedule stub adapters

## PR
[#188](https://github.com/DaSilvaDub/outlier/pull/188) — `fix/nfl-external-missing-stubs` → master

## What Was Done
- **Bug fixed:** `outlier_nfl/external/__init__.py` referenced `from .pbp import fetch` and `from .schedule import fetch` but neither module existed. Added stub adapters matching `ngs.py` pattern (return empty records).
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
