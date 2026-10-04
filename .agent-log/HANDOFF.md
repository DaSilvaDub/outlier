# HANDOFF — 2026-10-04 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-uwwa7c` · **Last commit**: `e310d90` · **PR**: https://github.com/DaSilvaDub/outlier/pull/209

## Fixed (reproduced before the fix)
- `outlier_nfl/alt_floors.py` — `export_alt_floors` gated `nfl_alt_floors_latest.json` and
  `Alt_Floors_latest.md` on `write_latest` but wrote the undated `nfl_alt_floors.csv`
  unconditionally, though that file is the same kind of current-slate artifact.
  `NflPipeline.run` sets `write_latest = window is None`, so any `--window early/late` run
  rewrote the slate-wide CSV from a partial subset: a 3-prop full run then a 1-prop windowed
  run left the CSV at 1 row while `nfl_alt_floors_latest.json` still held 3. Same class as the
  `_trace_best_bets` stale-latest bug closed in #206. The undated CSV is now written only when
  `write_latest` is set, and `outputs["csv"]` reports the dated CSV on a windowed run.
  Regression test: `tests/test_nfl_alt_floors.py::test_windowed_run_does_not_clobber_the_undated_csv`.

- `outlier_nfl/weekly.py` + 7 NFL test files — `NflPipeline.run`'s report writers fall back to
  the CWD-relative `Path("reports/NFL")`, i.e. the *tracked* reports directory, when no
  `reports_dir` is passed. Harmless while the only writer was the game-script block (behind
  `generate_game_script=False`), but #207's alt-floors block runs unconditionally. Result:
  (a) every `pytest` run overwrote the real `reports/NFL/Alt_Floors_latest.md` with an empty
  fixture-dated report — 9 `pipeline.run()` call sites across 7 test files omitted
  `reports_dir`; (b) `run_week` accepted `reports_dir`, used it for its own weekly card, but
  never forwarded it to the per-slate `pipeline.run()`, so weekly runs scattered per-slate
  alt-floor reports into `reports/NFL` regardless of the requested directory. Both fixed.
  Guard: `tests/test_nfl_ops_hygiene.py::test_pipeline_run_writes_no_reports_into_the_repository`
  snapshots the repo's `reports/NFL` around a run and fails on any new file (verified
  non-vacuous). The full offline suite now leaves the worktree clean.

## Reviewed, not changed (needs a ruling / no evidence of a live defect)
- **Contradictory parlay guidance in the generated Alt Floors report** —
  `render_alt_floors_markdown` guideline 1 recommends a *same-game* parlay of alt floor legs
  ("QB Passing Floor + Workhorse RB Rushing Floor"), while guideline 3 says "Per pipeline
  invariants, alternate player props must be parlayed across different games". The cross-game
  restriction in AGENTS.md / CLAUDE.md HOUSE RULE 3 is MLB-only, so guideline 3 also
  misattributes it to NFL. Whether NFL alt floors may be combined same-game is a desk policy
  call — not something an agent should silently pick. Needs your ruling, then one of the two
  guidelines goes.
- **Hit-rate scale in `alt_floors`** — `discover_alt_floor_candidates` reads `l5/l10/season_hit_rate`
  raw, while `calibration._normalize_hit_rate` (used by `best_bets`) defensively accepts both
  0–1 and 0–100 and divides by 100. If the feed ever emits percentages, every alt-floor
  confidence score would clamp to 1.0 and all ranking discrimination would be lost. Fixtures and
  all tests use 0–1 and there is no data on disk showing otherwise, so this is an unproven
  robustness gap, not a confirmed bug. Routing the three reads through `_normalize_hit_rate`
  would close it cheaply.
- `tapes=` is accepted by `discover_alt_floor_candidates` / `generate_alt_floors_pipeline` and
  never read. Dead parameter, harmless.
- 25 pre-existing ruff F401/E402 warnings, all in test files (12 in
  `tests/test_challenger_adversarial.py`, 5 in `tests/test_nfl_alt_floors.py`). No ruff gate in
  CI, so cosmetic. Left alone — unrelated cleanup.

## Sandbox limitation worth knowing
This cloud session cannot install Python packages: PyPI returns **403** behind the network
policy (`pip` and `uv` both). So `structlog`, `anthropic`, `google-genai` and `sqlalchemy` are
missing and 48 test modules fail to collect / 51 tests fail on import alone — identical before
and after any change. 1037 tests do run and pass. If you want a cloud agent to see a genuinely
green suite, the environment needs those wheels pre-installed (a SessionStart hook or a
vendored wheelhouse).

## Files Touched
- `outlier_nfl/alt_floors.py`
- `outlier_nfl/weekly.py`
- `tests/test_nfl_alt_floors.py`, `tests/test_nfl_ops_hygiene.py`, `tests/test_nfl_pipeline.py`,
  `tests/test_nfl_best_bets.py`, `tests/test_nfl_calibration.py`, `tests/test_nfl_external.py`,
  `tests/test_nfl_weather.py`
- `.agent-log/HANDOFF.md`

## Next Steps
- Rule on the same-game vs cross-game question above so the Alt Floors report stops
  contradicting itself.
- Still open from 2026-10-04 (Antigravity): verify weekly replay / shadow settlement against
  actual box scores once Week 4 concludes.

---

# HANDOFF — 2026-10-04 (Antigravity, Sportsbook Alternate Floor Props)

**Branch**: `feat/nfl-alt-floors` · **Last commit**: `2dc5495`

## Implemented & Baked into Pipeline
- **Dynamic Sportsbook Alternate Floor Props (`outlier_nfl/alt_floors.py`)**:
  - Replaces rigid, static prop filters (fixed 50 rush / 200 pass) with dynamic, player-specific floor detection.
  - Tailored to sportsbook alternate ladders (Hard Rock Bet default via `HARDROCK`/`HARDROCK_R`, with retail consensus fallback).
  - Handles ladder shifts cleanly: detects elite QB floors (e.g. Joe Burrow OVER 224.5 vs other QBs at 199.5/174.5/149.5), RB floors (Derrick Henry OVER 64.5 vs Walker 49.5 and Swift/Brown 39.5), and WR/TE floors (Nacua 39.5/49.5, JSN 64.5).
  - Composite confidence score: blends historical hit rate stability (`L10` 35%, `L5` 30%, `Season` 15%), safety cushion below consensus (`cushion_ratio` 10%), book implied probability / juice (10%), and situational weather/game-script adjustments.
  - Automatically extracts Top 3 in Passing, Top 3 in Rushing, Top 3 in Receiving, and ranks an overall Master Confidence list (1–9).
  - Exports artifacts on every pipeline run:
    - `data/NFL/exports/nfl_alt_floors_{date}.json` (and `nfl_alt_floors_latest.json`)
    - `data/NFL/exports/nfl_alt_floors.csv` (and `nfl_alt_floors_{date}.csv`)
    - `reports/NFL/{date}_Alt_Floors.md` (and `reports/NFL/Alt_Floors_latest.md`)
- **Pipeline Integration (`outlier_nfl/pipeline.py`)**:
  - Hooked directly into `NflPipeline.run()` after best-bets trace and attached to execution summary dictionary.
  - Added `--target-alt-book` CLI option (defaults to `HARDROCK`).
  - Added dedicated summary console printout showing top category plays and overall #1 confidence play.
- **Unit Tests (`tests/test_nfl_alt_floors.py`)**:
  - 6 unit tests covering odds conversion, book extraction, dynamic floor discovery, category and master ranking, file exports, and weather calibration. All 6 passed in 0.61s.
- **Documentation**:
  - Documented heuristic in `.agents/AGENTS.md`.

## Files Touched
- `outlier_nfl/alt_floors.py` (new)
- `outlier_nfl/pipeline.py`
- `outlier_nfl/__init__.py`
- `tests/test_nfl_alt_floors.py` (new)
- `.agents/AGENTS.md`
- `.agent-log/HANDOFF.md`

## Next Steps
- Verify weekly replay / shadow settlement against actual box scores when Week 4 games conclude.

---

# HANDOFF — 2026-10-02 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-lw6unk` · **Last commit**: `fb8c706` · **PR**: https://github.com/DaSilvaDub/outlier/pull/206

## Fixed (both reproduced on `tests/fixtures/nfl` before the fix)
- `outlier_nfl/best_bets.py` — `american_to_decimal(0)` raised ZeroDivisionError. `0` is not a
  real American price but nothing upstream rejects it (`extract_book_prices` keeps `int(0)`,
  `coerce_odds(0)` → `0`, `is_candidate` only checks `best_odds is not None`), so one junk quote
  on one prop propagated out of `build_best_bets` and cost the whole slate its card (fixture
  slate: 8 candidates → no card); through `weekly.run_week` it aborts the entire weekly run.
  Now reads `0` as even money (2.0) like the other three converters in the repo
  (`fetch_odds_close`, `outlier_scrapers.pack_market`, `outlier_scrapers.utils`) and like
  `games.american_to_implied_probability` (50.0).
- `outlier_nfl/pipeline.py` — `_trace_best_bets`' failure path removed the dated card but not
  `nfl_best_bets_latest.json`, so on a `write_latest` run the previous run's picks survived
  under the name readers treat as current — the exact stale card its own comment says it
  prevents. The existing test missed it because it runs `write_latest=False`.
  `_latest` is only unlinked when `write_latest` is set.

## Scorecard `hit_vs_line` item — closed by someone else mid-run
- I investigated the 2026-09-28 open item (102 signals graded vs player average, only 2 vs a
  consensus line) and independently reached the same two causes in `scorecard.py`: the
  `p.get("scope", "full_game")` gate (which only defaults when the key is *absent*) and the
  exact-team key in the line lookup, where `matchup._names_match` deliberately skips the team
  check when either side is blank. **PR #205 landed that fix on `master` (854edc1) while this
  run was in progress**, with the stronger `lookup_consensus_line()` + event scoping. Nothing
  left to do here; this branch merges 854edc1 rather than touching `scorecard.py`.
- Worth knowing for next time: running the pipeline offline against `tests/fixtures/nfl`
  produces props with `team` ('KC'/'BAL'), `scope='full_game'` and `is_consensus_line=True`,
  so the fixture path never exposes either gate. That is why this looked data-dependent.

## Checked clean this run
- All 5 upgrade markers present; branch was level with `origin/master` at `b408fae`.
- `ruff check` across the repo: 25 findings, all F401 unused imports in `tests/`,
  `scratch.py`, `script.py`, `append_feedback.py`. Cosmetic, pre-existing, left alone.
- `mypy outlier_scrapers` (4) + `mypy outlier_nfl` (8) + `pyright`: every finding is a
  narrowing false positive (the call is inside `try/except (TypeError, ValueError)` or behind
  an `is not None` guard) or a missing third-party stub. `pipeline.py:639 window_slug possibly
  unbound` is guarded by the same `if window:` as its assignment.
- Audited every signal-market literal in `matchup.py` / `usage.py` / `weather.py` against
  `NFL_MARKET_ALIASES` for more LONG_PASS-class join bugs: none. `usage.RECEIVING_TARGETS`
  is not an alias target but survives `normalize_market`'s raw-string fallback, and
  `TARGET_MARKETS` also emits `REC`/`REC_YDS`, so the vacated-volume effect still lands.

## Environment note (cloud sandbox)
- pypi is blocked by the network policy (403 from the egress proxy on `pypi.org`), so
  **pytest could not be installed at all this run** — unlike 2026-09-28, when a runnable
  subset existed. Test bodies were executed with a stdlib runner plus a minimal `pytest`
  stand-in kept in the scratchpad (not committed); it cannot resolve `conftest` fixtures, so
  modules relying on them report failures under it. The comparison that matters: pre-change
  and post-change results are identical across all 17 `test_nfl_*` modules, and
  `test_nfl_best_bets` went 25 → 26 passed. `ruff`, `mypy` and `pyright` are installed in the
  image and did run. The `Offline Pytest` CI job is the authoritative suite.
# HANDOFF — 2026-10-01 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-j81j64` · **Last commit**: `176186a` · **PR**: https://github.com/DaSilvaDub/outlier/pull/205

## Fixed — closes the open `hit_vs_line` item from the 2026-09-28 handoff
Both candidate causes listed there were real; both were in `outlier_nfl/scorecard.py`.

- **scope gate** (`consensus_lines`): `p.get("scope", "full_game") != "full_game"` only
  defaults when the key is *absent*. A serialized `NflPlayerProp` always carries `scope`
  and it can be `None`/`""`, which this dropped as a period market. Now uses the repo's
  standard `str(p.get("scope") or "full_game")` — the form `calibration.py:391`,
  `pipeline.py:426`, `snapshots.py:62,90` and `best_bets.py:662` all already use.
- **team gate** (line lookup): required exact normalized team equality, but
  `NflPlayerProp.team` is `str | None` and `props.extract_player_props` (props.py:76-84)
  leaves it empty when the feed's team id resolves through neither the alias table nor the
  event's home/away map. New `lookup_consensus_line()` keeps an exact-team match
  authoritative and falls back to player+market only when one side's team is blank —
  matching `matchup._names_match` / `best_bets._matching_signals`. A same-name collision
  across teams stays unmatched rather than grading the wrong player's line.

3 regression tests in `tests/test_nfl_scorecard.py`, each confirmed to fail against the
unfixed code.

## Then fixed again — Copilot review finding on #205 (`176186a`), correct and confirmed
The blank-team fallback in `9191d87` searched the **whole slate**, so an unresolved-team prop
for one game's "Mike Williams" could hand its line to a different game's same-named player.
The joins that commit cited as precedent are the proof it was wrong: `best_bets.py:316`
selects signals by `prop.event_id` and `matchup.py:712-715` works inside one game script, so
both reach `_names_match`'s blank-team check *already scoped to one game*. The original
collision test missed it because both fixture entries carried a team.

- `consensus_lines` now keys on `(event_id, team, player, market)` — no callers outside the
  module, so the wider key is contained.
- `lookup_consensus_line` takes the signal's `event_id` and falls back only within it. Two
  teams carrying the name in that game stay unmatched unless the signal's team pins it
  exactly. A blank prop `event_id` counts as unknown, not a different game, so a
  still-matching team keeps working (degenerate records only — the real pipeline always sets
  it; it cannot reintroduce the cross-player hazard, since two teams always give two entries
  and that returns None).

5 regression tests total; the 2 new ones fail when the event filter is removed. Full runnable
suite 1250 passed / 43 skipped (baseline 1245; delta = the 5 new tests), failures and
collection errors unchanged at 67/29. ruff clean; mypy unchanged at 8 pre-existing narrowing
false-positives. Review thread replied to and resolved.

## Open — needs the Windows box (no `data/` in the cloud sandbox)
- The 2 rows that *did* get a line in `reports/NFL/2026-09-27_Signal_Scorecard.md` look like
  **alt ladder lines**, which `consensus_lines`' docstring says must never be used:
  Darnell Mooney REC_YDS prior avg 37.5 / line 17.5, Juwan Johnson 60.0 / 35.5 — both
  graded `miss` against a line ~half the player's average. Suspected path: the fallback
  branch of `consensus.identify_consensus_lines_for_group` (most-quoted *priced* line, no
  balance requirement) can flag an alt rung as consensus when the main line is one-sided or
  off the board. Not fixed — consensus selection feeds the whole pipeline, so it needs real
  data and a deliberate call. Check against
  `data/NFL/normalized/nfl_calibrated_props_2026-09-27.json`.
- Re-run `scripts/nfl_signal_scorecard.py --date 2026-09-27` on the box to see how many of
  the 102 signals this PR actually recovers. Unverifiable here.
- Still open from the 2026-10-01 best-bets handoff: install the weekly snapshot tasks, check
  whether Outlier `books[]` ever carries a sharp book, calibrate the pillar deltas.

## Reviewed clean (no findings)
`outlier_nfl/best_bets.py`, `snapshots.py`, `pipeline.py`, `weekly.py`, `tape_nflverse.py` —
the whole PR #204 surface. The newest code consistently uses the tolerant scope and team
forms; `scorecard.py` was the lone holdout.

## Environment note (cloud sandbox)
pypi is blocked at the proxy gateway (403 on CONNECT; pypi.org is also in `noProxy`, so pip
goes direct and is refused). `structlog`, `sqlalchemy`, `pandas`, `openai`, `anthropic`,
`google-*`, `python-dateutil` cannot be installed. Workaround used this run: pytest 9.1.1,
ruff, mypy and librt were linked out of `/root/.cache/uv/archive-v0` into a scratch venv, and
a minimal `structlog` shim (its only use is `outlier_scrapers/api.py` logging) was written
into that venv — **scratch only, nothing added to the repo**. That lifted the runnable set
from 1024 to 1248 tests. The residual 67 failures / 29 collection errors are all
sqlalchemy/google/anthropic/openai/dateutil imports.

---

# HANDOFF — 2026-10-01 (Claude, NFL traced best bets)

**Branch**: `claude/festive-sagan-n0hlpu` · **Last code commit**: `4757b72` · **PR**: https://github.com/DaSilvaDub/outlier/pull/204

## Built
- `outlier_nfl/best_bets.py` — six-pillar trace (historical, opportunity, matchup,
  injury_weather, market, price) with a per-pick contribution ledger. NGS volume/separation,
  PBP defensive EPA z-scores, usage profiles, weather, injury report, matchup/usage/weather
  signals and price history all feed the final probability or gate a pillar. VALIDATED only
  when all six pillars are VERIFIED (game-day refresh, two-sided de-vig, >=2 snapshots,
  injury report loaded) and edge > 0; any CONTRADICTS or inactive player = REJECTED.
  Deltas capped ±3 pts/pillar, ±8 total (uncalibrated — tune via scorecard).
- Orphan audit: signals that reached no prop, split into `market_not_joined` (LONG_PASS bug
  class) vs `no_prop_offered`; plus external-source loaded/consumed counts.
- `outlier_nfl/snapshots.py` — append-only weekly JSONL (`data/NFL/snapshots/`, week starts Tue).
- `outlier_nfl/pipeline.py` — every run snapshots + writes `nfl_best_bets_{date}.json/.md`;
  new `write_latest` override; `load_injury_report` (None = no report, {} = nobody out).
- `outlier_nfl/weekly.py` + `scripts/run_nfl_weekly_snapshots.ps1 -Install` — Task Scheduler
  Tue 10:00/18:00, Thu 15:00, Sun 10:30, Mon 15:00 (machine-local clock; set for ET).

## Next
- Install the tasks on the Windows box and check the first Tuesday card's audit section
  (orphans, `candidates_without_ngs`, `opponents_without_pbp_defense`).
- Check whether Outlier `books[]` ever carries a sharp book (Pinnacle/Circa); else
  sharp money stays NOT_VERIFIED.
- Calibrate pillar deltas against settled results (`nfl_signal_scorecard.py`).

---

# HANDOFF — 2026-09-28 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-xi4ctd` · **Last commit**: `83b1ac4`

## Fixed
- `outlier_nfl/weather.py` + `outlier_nfl/fetch_odds_close.py` — the QB longest-completion
  market was written as `"LONGEST_PASSING_COMPLETION"` on both the signal side and the
  Odds-API mapping side, but that string is not in `NFL_MARKET_ALIASES`; the normalizer
  produces `PROP_LONG_PASS` ("LONG_PASS"). Since `apply_matchup_signals` joins on exact
  `signal.market == prop.market` and `enrich_close._row_match_key_short` joins on the
  uppercased `market` string, the weather haircut never reached the prop and the book close
  never stamped. Both now emit `LONG_PASS`. Regression tests in
  `tests/test_nfl_weather.py` and `tests/test_nfl_fetch_odds_close.py`.

## Open — needs a look with real slate data (cloud sandbox has no `data/`)
- `reports/NFL/2026-09-27_Signal_Scorecard.md` grades 102 signals vs the player average but
  only **2** vs a consensus line, and `scorecard.py` calls the line grade "the
  betting-relevant grade". Worth running `scripts/nfl_signal_scorecard.py --date 2026-09-27`
  on the box with `data/NFL/normalized/nfl_calibrated_props_2026-09-27.json` present and
  checking why `scorecard.consensus_lines` misses. Two candidates, both unproven here:
  `consensus_lines` keys on `(team, name, market)` and requires exact team equality, while
  the codebase's other signal↔prop join (`matchup._names_match`) deliberately skips the team
  check when either side is blank (`props.extract_player_props` can leave `team=None`); and
  it reads `p.get("scope", "full_game") != "full_game"`, which rejects a row whose `scope`
  key is present but `None`/`""` — everywhere else (`calibration.py`, `pipeline.py`,
  `export_nfl_extra_pack.py`) treats those as full game.

## Environment note (cloud sandbox)
- pypi is blocked by the network policy, so `structlog`, `sqlalchemy`, `pandas`, `openai`,
  `anthropic`, `google-*`, `python-dateutil` cannot be installed: 48 test modules fail to
  import. The runnable subset is green — 999 passed, 43 skipped, and all 51 reported
  failures are `ModuleNotFoundError` from those same missing packages, not logic failures.
  Full-suite verification still has to happen in CI or on the Windows box.

## Pre-existing, not touched
- `ruff check .` — 25 `F401` unused imports, all in `tests/` plus `append_feedback.py`,
  `scratch.py`, `script.py`. `make lint-check` only lints changed files, so these stay out
  of the gate.
- `mypy outlier_scrapers` — 4 errors (unchanged baseline): three `float()`/`int()`-on-Optional
  reports at `schema.py:256`, `probable_pitchers.py:95`, `game_totals.py:1045` that are each
  already wrapped in `except (ValueError, TypeError)`, plus missing `types-python-dateutil`
  stubs for `c_research.py`.

---

# Handoff — 2026-09-28 (claude)

**Branch**: `claude/nifty-einstein-gbtl85` · **PR**: https://github.com/DaSilvaDub/outlier/pull/198 (merged master incl. #192, #196)

**Scope**: nflverse tape pull (`outlier_nfl/tape_nflverse.py`, `scripts/pull_nflverse_tape.py`) with auto roles/inactives, pass-rush + QBR grades; real external adapters (`outlier_nfl/external/`); weather (`outlier_nfl/weather.py`); player usage vacancy/efficiency signals (`outlier_nfl/usage.py`); pass-rush retargeted to TIMES_SACKED; regression wins conflicts; signal scorecard (`outlier_nfl/scorecard.py`, `scripts/nfl_signal_scorecard.py`). Findings in `docs/nfl-data-sources.md`.

**Open**: Codacy flagged a high issue on the scorecard commit; mitigated by moving URL building into `usage.fetch_player_weeks` — confirm Codacy clean before merging #198. PRs #193 (Codacy action_required), #194/#195/#197 (conflicts with master) were not merged.

**Next**: run `python scripts/nfl_signal_scorecard.py --date 2026-09-27` after nflverse posts Week 3 box scores.

---

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

---

# HANDOFF — 2026-09-26 (Claude, daily automated debug review)

## Last Commit SHA
`394138e` — fix(pack): restore projection_side_conflict flag on audit-only rows

## PR
[#194](https://github.com/DaSilvaDub/outlier/pull/194) — `claude/inspiring-fermat-xuehxu` → master

## Files Touched
- `outlier_scrapers/pack_selection.py` — #190 (`26f257e`) moved the
  `projection_side_conflict` check out of `_apply_quality_and_signal_flags` into a
  `build_row` discard; `c25a0a0` then exempted audit-only fallback projections from that
  discard. The combination left audit-only rows with *no* signal: the row is kept, but the
  flag was no longer appended anywhere in the codebase. Restored the original block. It is
  only reachable by rows the upstream discard deliberately kept, so real projections still
  drop at `build_row` and only the audit-only case falls through to the flag. The flag stays
  informational (not in `DISQUALIFYING_DQ_FLAGS`), matching its pre-#190 role. Also re-uses
  the `market_type_upper` local that #190 orphaned (ruff `F841`).
- `tests/test_pack.py` — `test_audit_only_projection_side_conflict_is_kept_but_flagged`,
  placed alongside #190's two `_discarded_upstream_` tests so the trio pins all three
  outcomes.

## Evidence
Same card as `test_league_average_so_projection_is_audit_only` (OVER 5.5, league-average SO
mean 4.95 — below the line, so it opposes the OVER):

| revision | `data_quality_flags` |
|---|---|
| `82569cd` (pre-#190) | `projection_side_conflict` |
| `c25a0a0` (master) | *(empty)* |
| this branch | `projection_side_conflict` |

## Verification
- `pytest` — 1124 passed / 43 skipped, identical to the pre-change baseline. All 67 failures
  and 29 collection errors are `ModuleNotFoundError` (`sqlalchemy` ×most, plus `google`,
  `anthropic`, `openai`, `dateutil`).
- **Sandbox limitation (recurring):** pypi.org and files.pythonhosted.org return **403** from
  the egress proxy (also via `--proxy $HTTPS_PROXY`), so project deps cannot be installed.
  `pytest`/`ruff`/`mypy` are present as standalone uv tools. A stdlib-only `structlog` shim
  under the scratchpad unblocked 41 of the 48 collection errors. **`tests/test_pack.py` is
  sqlalchemy-blocked, so the new test could not run locally — CI is the authority.** Its
  assertions were validated by driving `build_row` directly through a `pack_selection`-only
  import path, and the pre-#190 comparison was run in a detached worktree at `82569cd`.
- `ruff check` — 20 → 19 errors (the `F841` is resolved). Remaining 19 are pre-existing
  unused imports in `scratch.py`/`script.py`/`append_feedback.py` and two test files; ruff is
  not in CI.
- `mypy outlier_scrapers` — unchanged: 3 pre-existing `arg-type` false positives
  (`schema.py:256`, `probable_pitchers.py:95`, `game_totals.py:1045` — all wrapped in
  `try/except (ValueError, TypeError)`) plus one missing-stub note.

## Next Steps / Open Items
- Review and merge PR #194.
- **Still open (from 2026-09-25):** `outlier_nfl/pipeline.py:239` derives the NFL season as
  `int(target_date.split('-')[0])`, so a January/February playoff slate resolves to the
  *next* season. Confirmed still present. Impact is currently **nil** — every
  `outlier_nfl/external/*` adapter (`ngs`, `pbp`, `schedule`) is a stub returning
  `{"records": []}`, and the call is wrapped in `try/except` with an empty-list fallback. It
  becomes real the moment those adapters are implemented. Fix is a month<=2 → year-1 guard.
- **Still open (from 2026-09-24):** the roster gate's bare-proximity pattern treats an
  *opponent* mention as a violation ("leaky WAS secondary vs Jahan Dotson"). Needs a human
  call on whether opponent context should be exempted.

---

# HANDOFF — 2026-09-27 (Antigravity)

## Last Commit SHA
`8ffee08` — feat(pack): add team_total and player_position to CANDIDATES_HEADER

## PR
[#195](https://github.com/DaSilvaDub/outlier/pull/195) — `feat/candidates-header-parity` → `master`

## Files Touched
- `outlier_scrapers/pack_selection.py` — added `player_position` (after `player_id`) and `team_total` (after `priced_line`) to `CANDIDATES_HEADER`.
- `tests/test_pack.py` — updated `test_header_canonical_with_flags` to assert presence of `player_position` and `team_total`.

## Verification
- `pytest tests/test_pack.py -k test_header_canonical_with_flags` passed (1/1).
- `pytest tests/test_schema.py` passed (11/11).
- `pytest tests/test_pack_index.py tests/test_runner_common.py` passed (72/72).
- Scratch verification confirmed zero schema errors/warnings on candidate rows.

## Next Steps
- Merge PR #195 into master.
- When running tomorrow's daily job, confirm that the 27 `Candidate row schema warning` messages are gone.

---

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

## Hosted CI (authoritative)
Green on PR head `17145d2`: **Offline Pytest success**, **Static Type Checking success**
(also both green on the code commit `e7d9075`). The Offline Pytest job installs the declared
dependencies, so it is the authority for the `pack_selection.py` change and every other file
this sandbox cannot import.

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
- **Corrected inherited claim:** earlier handoffs listed "`--window` advertised but no
  argparse wiring exists". That is now **stale** -- `--window` is fully wired
  (`pipeline.py:524-529`) and threaded through `run(window=...)`. Do not re-chase it.
- **Still open, needs a human call:** a `--window` run *does* clobber `*_latest.json`.
  Every `*_latest.json` write (`pipeline.py:262,323,324,329,346,360,370,389,495`) is
  unconditional and sits *outside* the `if window:` block, so `--window snf` overwrites
  `nfl_props_latest.json` / `nfl_high_prob_props_latest.json` with only that window's subset
  while also writing the `_{window_slug}` copies. Newly found downstream consequence:
  `scripts/export_nfl_extra_pack.py::resolve_source()` falls back to
  `nfl_high_prob_props_latest.json`, so after a window run the exported `nfl_only.csv` is a
  partial slate with nothing marking it partial. Not fixed because the intended semantics are
  genuinely ambiguous (should "latest" mean the most recent run, or the full slate?) -- a
  one-line `if not window:` guard would settle it either way once the owner decides.
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
