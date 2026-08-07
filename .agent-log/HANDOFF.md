# Handoff

## Master Cards split, alt-bankroll data fixes, multi-book widening (2026-08-06)

Claude is running low on usage this session — handing off to Codex to pick up the next task (Alt Spreads, see "Next Steps" below).

- **Last Commit SHA**: `fa2e8dbc2ff6271fc8d034131afa3dbab0edb72a` on `master` (all 4 commits below pushed directly to master, matching this repo's recent convention — no feature branch/PR for this stretch of work)
- **Commits this session** (oldest → newest):
  1. `6e0b7eb` feat(prompts): split Master Cards into per-league MLB/WNBA/Both variants
  2. `9eb4150` fix(alt-bankroll): stop pooling home/away hit rates for game totals
  3. `3601dca` fix(registry): add missing MLB_TEAM_ALIASES entry for Cincinnati Reds
  4. `fa2e8db` feat(alt-bankroll): widen books beyond Hard Rock-only, loosen odds ceiling to -110

### 1. Master Cards split (`6e0b7eb`)

- **Files**: `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`, `.agents/skills/export-manual-outlier-packs/SKILL.md`, `prompts/A.md`, `tests/test_generate_prompts.py`
- The single `1_Master_Cards_pack_*.txt` became three sport-scoped files: `1_Master_Cards_MLB_pack_*.txt`, `1_Master_Cards_WNBA_pack_*.txt`, `1_Master_Cards_Both_pack_*.txt`, each filtered via a new `filter_master_card_candidates()` in `generate_prompts.py` to a sport-specific market whitelist (MLB: Moneyline/Spread/Strikeouts/Total Bases; WNBA: Moneyline/Spread/Points/Assists/Rebounds + combo props) and odds `-250` to `+150`. Files are omitted when a variant has zero qualifying rows.
- Also removed a stale "never recommend total bases" line from `prompts/A.md` §5.2 that directly contradicted the new MLB whitelist requiring Total Bases.
- Real-data gotcha worth knowing: the pack sometimes tags a market with the generic `PLAYER_PROP` type instead of a specific code (`SO`/`TB`/etc.), and spells gameline labels inconsistently (`Run Line` vs `Spread` for MLB, `Money Line` vs `Moneyline` for WNBA) — the whitelist filter normalizes on this.

### 2. Alt-bankroll home/away pooling bug (`9eb4150`)

- **File**: `outlier_scrapers/alt_bankroll_props.py`, `tests/test_alt_bankroll_props.py`
- For game totals, L5/L10 hit rate was pooling both teams' results into one blended average (e.g. a 90%/70% split pooled to a passing 80%), which could hide a weak team's rate behind a strong partner's. Now uses the **weaker (minimum)** side for the pass/fail gate. Added `home_l5_pct`/`away_l5_pct`/`home_l10_pct`/`away_l10_pct` columns to the CSV output for transparency.
- User caught this by comparing our exported hit rates against the live Outlier site UI, which shows each team's rate separately.

### 3. Missing Cincinnati Reds team alias (`3601dca`)

- **File**: `outlier_scrapers/registry.py`, `tests/test_registry_paths.py`
- `MLB_TEAM_ALIASES` had no `"CINCINNATIREDS": "CIN"` entry — the only 1 of 30 MLB teams missing one. `probable_pitchers.py` calls `normalize_team()` on the MLB Stats API's raw team names; for the Reds this silently returned `None`, dropping Cincinnati entirely from the probable-pitchers lookup and leaving the Athletics' `opponent` field blank for that game. Downstream effect: a Totals report had to fall back to a Tier-3 web source for Cincinnati's starter and got the wrong name.
- Added a regression test that normalizes every MLB team's full display name and asserts it round-trips to its own code — catches this class of gap for any team going forward.

### 4. Alt-bankroll book scope widened, odds ceiling loosened (`fa2e8db`)

- **Files**: `outlier_scrapers/alt_bankroll_props.py`, `outlier_scrapers/alt_player_props.py`, `prompts/Alt_Bankroll_Props_Analysis.md`, `prompts/Alt_Player_Props_Analysis.md`, `.agents/skills/export-manual-outlier-packs/SKILL.md`, both test files
- Both alt-bankroll boards (game/team totals AND player props) were Hard Rock-exclusive. User spotted real qualifying lines missing (e.g. `LAS Team Total 83.5`, `MIN Team Total 104.5`, an alt game total) — traced directly against raw scraped `books` data and confirmed Hard Rock genuinely doesn't carry most of these; Fanatics/Midnite/DraftKings/Novig do. Not a scraper bug, a scope limitation.
- Widened to accept Hard Rock + Fanatics + Midnite + DraftKings + Novig, picking the single best qualifying price across all of them per row. **Odds ceiling loosened from -200 to -110** (user's explicit instruction — the floor stays -1000).
- The odds-window check is now folded into offer selection itself (`_best_allowed_offer` in both files) rather than checked after picking a price — picking the numerically-best price across all allowed books first and checking the window after could silently pick a non-qualifying book's price over a different book's qualifying one for the same row.
- "Hardrock R" is deliberately kept excluded as a distinct book, not treated as a Hard Rock alias — there's an existing test (`test_bankroll_board_still_excludes_hardrock_r_as_a_distinct_book` / equivalent in player props) guarding this; don't "fix" it into an alias without checking why it was excluded originally.
- Verified against live data, not just unit tests: WNBA qualifying lines went 7 → 77, MLB → 75. `LAS @ MIN` specifically went from zero rows to many once its Fanatics-only team-total lines became visible.

### Betting reports produced this session (outside the repo, not committed)

Several `BETTING REPORTS/GENERIC/2026-08-05` and `2026-08-06` reports were hand-analyzed (Master Totals, Alt Total MLB/WNBA, Alt Player Prop) and iteratively corrected after review — including a real self-authored spread-sign transcription bug (wrote `-1.5`/`-3.5` when the pack's actual line values were positive `1.5`/`3.5`) that was fixed across both the MLB and WNBA Alt Total reports. These live in `C:\Users\dasil\OneDrive\Desktop\BETTING REPORTS\GENERIC\` and mirrored copies in `Desktop\today\reports\GENERIC\` (OneDrive) and `G:\My Drive\today\reports\GENERIC\` (Google Drive) — not part of this repo, just noted here for continuity.

### Verification

- `pytest -q`: 680 passed (as of `fa2e8db`)
- `ruff check` on all touched files: clean
- Live pack rebuild + `generate_prompts.py` re-run confirmed the alt-bankroll widening in real output, not just tests

### Next Steps — hand this to Codex

**User's explicit ask: implement Alt Spreads for WNBA and MLB.** This wasn't scoped or started this session — no design decisions have been made yet. Open questions for whoever picks this up:

- Is this a *new* alt-bankroll-style board (own script, own filters, own Master prompt file — following the `alt_bankroll_props.py` / `alt_player_props.py` pattern), or an extension of the existing `GAMELINE`/`SPREAD` handling already inside `alt_bankroll_props.py` (which currently treats spreads as one of three allowed `ALLOWED_GAMELINES` alongside Moneyline/Total, not as its own dedicated product)?
- Same L5≥75%/L10≥75%, odds -1000 to -110, multi-book (Hard Rock/Fanatics/Midnite/DraftKings/Novig) contract as the other two boards, or different thresholds specific to spreads?
- Given the just-fixed home/away-pooling bug in `extract_l5_l10()` (item #2 above) was specifically about `GAMELINE`/`TOTAL` records using both teams' stats — check whether `GAMELINE`/`SPREAD` records have the same home/away stats-pooling shape and need the same weaker-side-wins treatment, or if spread hit-rate is inherently single-team already (worth checking `_summary_stat_for_team` vs the `homeSummaryStat`/`awaySummaryStat` split before assuming either way).
- Should this land in a new Master prompt file (`5_Master_Alt_Spread_*_pack_*.txt`?) via `generate_prompts.py`, alongside the existing `3_Master_Alt_Total_*` and `4_Master_Alt_Player_Prop`? If so it needs the same MLB/WNBA split treatment, a new prompt template under `prompts/`, and `SKILL.md` documentation updated to match, following the pattern already established for Alt Total and Alt Player Prop in this session's commits.
- Re-run **STEP 0** (`report-sync.ps1`) before starting, per this repo's mandatory multi-agent sync protocol in `AGENTS.md`/`CLAUDE.md` — don't skip it just because this is a same-repo handoff, not a different ent.

---

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
