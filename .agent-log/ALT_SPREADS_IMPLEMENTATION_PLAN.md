# Alt Spreads Handoff Report and Implementation Plan

Date: 2026-08-06 (America/New_York)

Repository: `C:\Users\dasil\Dev\GitHub\outlier`

## Executive decision

Implement Alt Spreads as a dedicated MLB/WNBA product lane, but do not build another scraper or duplicate the existing bankroll-selection engine.

Spread records already travel through the full pipeline:

`*_games_latest.json` -> `build_alt_bankroll_board()` -> league bankroll CSVs -> Desk1 prompts

The missing capability is product separation. Today, spread rows are mixed with moneylines, game totals, and team props in `mlb_alt_bankroll_props.csv` / `wnba_alt_bankroll_props.csv`, then published under misleading `3_Master_Alt_Total_*` filenames.

The lowest-risk design is:

1. Keep the existing mixed bankroll CSVs unchanged as compatibility artifacts.
2. Add dedicated `mlb_alt_spreads.csv` and `wnba_alt_spreads.csv` outputs derived from the same selected rows.
3. Add dedicated league-specific `5_Master_Alt_Spread_*` prompts.
4. Exclude spread rows from the existing `3_Master_Alt_Total_*` prompts so the same line is not analyzed twice.

No paid reasoning or external model calls are required for implementation or verification.

## Handoff status

- Canonical sync completed successfully at `bf46b38`.
- `HEAD` matched `origin/master`; working tree was clean.
- Sync verdict: `REPORT STATUS: OK`.
- RUN-NONCE: `a22a0ddf073648d4` (`2026-08-07T01:11:56Z`).
- The product code from the handoff ends at `fa2e8db`; `bf46b38` is the subsequent handoff-document commit.
- Relevant baseline tests pass: `19 passed` from `tests/test_alt_bankroll_props.py` and `tests/test_generate_prompts.py`.

## What exists now

### Data and selection

- `outlier_scrapers/normalizer.py` represents spreads as `GAMELINE` / `SPREAD` records with `HOME` or `AWAY` position, a signed numeric `line`, a selected `team`, books, and side-specific stats.
- `outlier_scrapers/alt_bankroll_props.py` already admits full-game `MONEYLINE`, `SPREAD`, and `TOTAL` gamelines plus eligible team props.
- Its board enforces active and pregame status, target slate date, full-game scope, allowed books, the `-1000..-110` odds window, and `L5>=75%` plus `L10>=75%`.
- `outlier_scrapers/pack.py` writes the selected rows to `mlb_alt_bankroll_props.csv` and `wnba_alt_bankroll_props.csv`.
- `generate_prompts.py` embeds those mixed CSVs into `3_Master_Alt_Total_MLB_*` and `3_Master_Alt_Total_WNBA_*` prompts.

### Saved-feed findings

- MLB saved feed: 1,707 normalized spread rows; six had no usable summary-stat blob and correctly fail closed.
- WNBA saved feed: 514 normalized spread rows; all carried a usable side-specific summary-stat blob.
- Spread records use one selected side: `HOME` maps to `homeSummaryStat`, and `AWAY` maps to `awaySummaryStat`.
- Therefore, spread hit rates must remain team-side specific. The weaker-of-two-team rule is correct for game totals only and must not be applied to spreads.
- A replay of the August 6 normalized feeds at slate start produced 74 qualifying MLB spread rows and 54 qualifying WNBA spread rows. These counts are a smoke-test snapshot, not a stable test fixture.

### Important correctness risk

The handoff records a real report transcription bug where positive spread lines were rewritten as negative. A dedicated Alt Spreads contract must make the signed selection unambiguous. The output should retain numeric `line` and add:

- `signed_line`: explicit string such as `+3.5` or `-1.5`.
- `selection`: explicit string such as `PIT +3.5`.

The prompt must instruct the analyst never to invert or normalize the supplied sign.

## Proposed product contract

An Alt Spread row qualifies only when all conditions are true:

1. League is MLB or WNBA.
2. `market_type == GAMELINE` and `proposition == SPREAD`.
3. Scope is full game and overtime inclusion remains whatever the source row declares.
4. Event ID, market ID, outcome ID, team, position, start time, and numeric line are present.
5. Position is exactly `HOME` or `AWAY`, and the selected stats blob matches that side.
6. Record is active, belongs to the target local slate date, and has not started.
7. L5 and L10 are both at least 75% and no greater than 100%.
8. At least one qualifying offer exists from Hard Rock, Fanatics, Midnite, DraftKings, or Novig.
9. Best price is selected only from qualifying offers in the inclusive `-1000..-110` window.
10. `Hardrock R` remains excluded as a distinct book.
11. The signed line is preserved exactly; a positive source line must render with an explicit plus sign.

## Implementation plan

### Phase 1: Create the dedicated board contract

Files:

- New: `outlier_scrapers/alt_spreads.py`
- New: `tests/test_alt_spreads.py`

Work:

- Add `ALT_SPREADS_HEADER` using the current bankroll fields plus `signed_line` and `selection`.
- Add `build_alt_spreads_board()` as a specialization of `build_alt_bankroll_board()` that retains only exact `SPREAD` rows and applies fail-closed spread identity/sign validation.
- Add `export_alt_spreads_for_league()` and the same offline CLI pattern used by the current bankroll module.
- Reuse the existing bankroll board so book, odds, hit-rate, date, active, and pregame policies cannot drift.
- Do not change `extract_l5_l10()` weaker-team handling for totals; spreads already use `_summary_stat_for_team()` correctly.

### Phase 2: Wire dedicated pack artifacts

Files:

- Modify: `outlier_scrapers/pack.py`
- Modify: `tests/test_alt_bankroll_props.py` or add pack assertions to `tests/test_alt_spreads.py`

Work:

- Add `mlb_alt_spreads.csv` and `wnba_alt_spreads.csv` to `DERIVED_PACK_OUTPUTS`.
- In `write_pack()`, call `build_alt_spreads_board()` for each normalized games payload.
- Always write headers; qualifying rows remain league-scoped.
- Preserve `mlb_alt_bankroll_props.csv` and `wnba_alt_bankroll_props.csv` unchanged for compatibility.
- Do not modify `daily_job.py`: pack generation already receives normalized games data, so no new acquisition step exists.

### Phase 3: Add dedicated Desk1 prompts and remove prompt duplication

Files:

- New: `prompts/Alt_Spreads_Analysis.md`
- Modify: `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`
- Modify: `tests/test_generate_prompts.py`

Work:

- Load the new league spread CSVs from the latest canonical pack.
- Write only non-empty prompts:
  - `5_Master_Alt_Spread_MLB_pack_YYYY-MM-DD.txt`
  - `5_Master_Alt_Spread_WNBA_pack_YYYY-MM-DD.txt`
- Give the prompt a spread-specific table: sport, matchup, selection, team, position, signed line, L5, L10, book, and odds.
- State explicitly that `team + signed_line` is authoritative and the sign may not be inverted.
- Filter `SPREAD` rows out of the data embedded in `3_Master_Alt_Total_*` prompts. Keep the legacy mixed bankroll CSVs themselves unchanged.
- Omit each league prompt when its spread CSV is empty or header-only.
- Keep existing Master Cards and Alt Player Prop filenames unchanged.

### Phase 4: Update documentation and handoff contract

Files:

- Modify: `.agents/skills/export-manual-outlier-packs/SKILL.md`
- Modify: `.agent-log/HANDOFF.md`

Work:

- Document the two new CSV artifacts and two new Desk1 prompt filenames.
- Record the exact gates, side-specific stats rule, signed-line invariant, and compatibility behavior.
- Record test/smoke results and final commit SHA in the handoff.

### Phase 5: Verification and release gates

Focused tests must cover:

- Exact `GAMELINE/SPREAD` inclusion and rejection of moneyline, total, team prop, and partial-period rows.
- HOME selecting `homeSummaryStat`; AWAY selecting `awaySummaryStat`.
- A strong opponent-side blob never replacing or weakening the selected spread-side blob.
- L5/L10 boundary: 75% included; below 75% excluded.
- Odds boundary: `-1000` and `-110` included; `-1001` and `-109` excluded.
- All five allowed books, best qualifying price selection, and `Hardrock R` exclusion.
- Missing IDs, team, position, line, stats, or qualifying offer failing closed.
- Positive line rendered as `+N`; negative line retained as `-N`.
- Pack writes both league CSVs with correct headers and no cross-league rows.
- Prompt generation writes each non-empty league prompt, omits header-only prompts, preserves the sign, and does not leave spreads in Alt Total prompt data.
- Existing mixed bankroll CSV behavior remains compatible.

Required commands before publication:

1. `python -m pytest -q tests/test_alt_spreads.py tests/test_alt_bankroll_props.py tests/test_generate_prompts.py`
2. Run relevant `tests/test_pack.py` coverage, then full `python -m pytest -q`.
3. Run Ruff on every touched Python file and test.
4. Run MyPy using the repository's established command/configuration.
5. Rebuild a pack from saved normalized MLB/WNBA feeds and confirm non-empty spread CSVs plus correctly signed prompt rows.
6. Confirm no Alt Spread row appears in the generated Alt Total prompt payload.
7. Update `.agent-log/HANDOFF.md`.
8. Publish through the repository's normal branch/PR workflow unless the user explicitly directs a direct-to-master release.
9. After publication, rerun the full canonical `report-sync.ps1` and require `REPORT STATUS: OK` plus a complete RUN-NONCE trailer.

## Definition of done

Alt Spreads is complete when MLB and WNBA each have a dedicated, strict, spread-only CSV and conditional Desk1 prompt; every selection carries an unambiguous signed line; existing bankroll artifacts remain available; spreads are not duplicated inside Alt Total prompts; focused/full tests, Ruff, MyPy, saved-feed smoke verification, handoff update, publication checks, and final canonical sync all pass.

## Explicit non-goals

- No new scraping endpoint.
- No paid reasoning run.
- No change to L5/L10, odds, or sportsbook policy without a separate user instruction.
- No application of the game-total weaker-team rule to spreads.
- No broad refactor of player props, game cards, totals modeling, or daily refresh orchestration.
