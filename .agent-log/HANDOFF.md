# Handoff — 2026-09-27 (Antigravity / Gemini)

**Branch**: `fix/nfl-prop-scope-gating`
**Last Commit SHA**: `cef0b73` — docs(skills): codify full-game scope invariant into nfl-game-script skill
**PR**: [#196](https://github.com/DaSilvaDub/outlier/pull/196) — `fix/nfl-prop-scope-gating` → master

## Problem & Root Cause
- **User Correction**: Chris Olave receiving yards line was erroneously reported as 14.5 @ -104, an impossible full-game total.
- **Root Cause**: FanDuel and HardRock offered a 4th-quarter receiving yards prop (`scope: "fourth_quarter"`) for Chris Olave at 14.5 @ -104. Because Olave hit >=15 yards in the 4th quarter in 5 of 5 games (L5=100%, L10=100%), `apply_game_script_calibration` in `outlier_nfl/calibration.py` evaluated hit rates without checking `scope == "full_game"`, incorrectly stamping it with `TIER_1_ANCHOR` and `HIGH_HIT_RATE_ANCHOR`.
- **Downstream Leak**: `pipeline.py` exported all `TIER_1_ANCHOR` records into `nfl_high_prob_props_*.json`, and `scripts/export_nfl_extra_pack.py` exported them into `nfl_only.csv` without filtering for full-game scope or formatting the period scope in the selection string. Over 109 period props (e.g. Mahomes 55.5 Pass Yds 1Q, Burrow 0.5 Pass TD 1H, Olave 14.5 Rec Yds 4Q) were masquerading as full-game lines across the board.
- **Chris Olave Ground Truth**: Full-game consensus line is 79.5 Receiving Yards (Over +101 / Under -117 with 14 books quoting) and 6.5 Receptions (Over +140 / Under -140 with 17 books quoting).

## Files Touched
- `outlier_nfl/calibration.py` — Enforced `is_full_game = prop.scope in (None, "", "full_game")` gate on deficit volume adjustments, defensive shell coverage adjustments, and `TIER_1_ANCHOR`/`TIER_2_STRONG` confidence tier assignments.
- `outlier_nfl/pipeline.py` — Added defense-in-depth full-game scope filtering when populating `anchors` for `nfl_high_prob_props_*.json`.
- `scripts/export_nfl_extra_pack.py` — Filtered `export_nfl_only` to full-game scope records, and updated `_selection()` to explicitly append `({scope})` if a non-full-game scope is ever present.
- `tests/test_nfl_calibration.py` — Added `test_period_props_never_qualify_for_tier1_or_tier2_anchors()` unit test.
- `.agents/AGENTS.md` — Codified `Codebase Quirk: NFL Prop Scope Gating (Full-Game vs. Micro-Periods)`.
- `.agents/skills/nfl-game-script/SKILL.md` — Codified Section 4 Full-Game Scope Invariant.

## Validation & Verification
- `pytest tests/test_nfl_calibration.py tests/test_nfl_matchup.py tests/test_nfl_roster.py` — 42/42 passed.
- Re-ran recalibration on 2026-09-27 slate: Period props in `nfl_high_prob_props_2026-09-27.json` dropped from 109 to 0. Olave 14.5 dropped to 0.
- `export_nfl_extra_pack.py` successfully updated `nfl_only.csv` (857 rows, all full_game).

## Next Steps
- Push branch `fix/nfl-prop-scope-gating` to remote and open PR to master.
