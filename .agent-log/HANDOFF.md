# HANDOFF — 2026-09-30 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-40u1rb` · base `e09f399`

## Fixed — the scorecard consensus-line join (closes the open item from 2026-09-28)
`outlier_nfl/scorecard.py`. The prior handoff flagged that
`reports/NFL/2026-09-27_Signal_Scorecard.md` grades 102 signals vs the player average but
only **2** vs a consensus line, and listed two unproven candidates. Both were tested:

- **Team-code mismatch: ruled out.** `_team()` round-trips every nflverse abbreviation to the
  same canonical code the props carry, legacy spellings included (`LA`→`LAR`, `WSH`→`WAS`,
  `JAC`→`JAX`, `OAK`→`LV`, `SD`→`LAC`, `STL`→`LAR`). `normalize_team` returned `None` for none
  of them, so nothing falls through to a raw code.
- **Name-keying: ruled out.** `_name_key` is strictly *more* tolerant than
  `matchup._names_match`, the join that does work — it collapses `D.K. Metcalf`/`DK Metcalf`,
  which `_names_match` misses. Markets are aligned too (`PROP_REC_YARDS == "REC_YDS"`), and 39
  `EFFICIENCY_COLD` `REC_YDS` signals got no line while 2 `REC_YDS` signals did, so the
  failure was in the player/team key, not the market string.
- **Blank prop team: confirmed and reproduced.** `team` is **not** in
  `validate_player_prop_record`'s `required_fields`, so `extract_player_props` leaving `team`
  unset (its `teamId` is neither a known code nor either of the event's two team ids) writes
  `"team": null` straight into `nfl_calibrated_props_<date>.json`. `_team(None)` is `""`, so
  the prop keys as `("", name, market)` and can never meet a signal keyed
  `(team, name, market)`. Reproduced with real code: the signal keeps `hit_vs_avg` and
  silently loses `line`/`hit_vs_line` — exactly the 102-vs-2 shape.
  `matchup._names_match` already skips the team comparison when either side is blank, with a
  comment giving this same reason; `unambiguous_lines()` now applies that rule to the
  scorecard join. It is strictly additive — it can only turn a `None` line into a line, never
  change one that already resolved — and it refuses to guess when two teams carry the same
  player name and market at different lines.

Also in the same predicate: `scope` was read as `p.get("scope", "full_game") != "full_game"`,
which drops a row whose `scope` key is present but `None`/`""`. `pipeline.py:413` and
`scripts/export_nfl_extra_pack.py:156` both treat those as full game. Latent today
(`detect_scope` always returns a non-empty string), so this is hardening, not the cause.

Regression tests in `tests/test_nfl_scorecard.py` (4 added); 3 of them fail on the pre-fix
tree, the 4th pins exact-team precedence over the fallback.

## Still needs the Windows box
The **production** cause of the 2026-09-27 miss is still unconfirmed — this fixes a proven,
reachable mechanism that reproduces the symptom, but the slate file itself is not in the
sandbox (`data/` does not exist here). To confirm and to see the fix's effect:

```powershell
python -c "import json;r=json.load(open(r'data\NFL\normalized\nfl_calibrated_props_2026-09-27.json'))['records'];c=[p for p in r if p.get('is_consensus_line')];print('records',len(r),'consensus',len(c),'blank team',sum(1 for p in c if not p.get('team')))"
python scripts\nfl_signal_scorecard.py --date 2026-09-27
```

A high "blank team" count confirms it. If instead `consensus` is near zero, the cause is
upstream in `select_consensus_player_props` and needs a separate look.
Note the run rewrites this date's rows in `data/NFL/scorecard/ledger.jsonl` (idempotent per
date), so re-running is safe and will backfill the line grades.

## Environment note (cloud sandbox) — unchanged from 2026-09-28
pypi is blocked by the network policy (403 both direct and via the proxy), so `structlog`,
`sqlalchemy`, `psycopg2`, `openai`, `anthropic`, `google-genai` cannot be installed. The
toolchain came from the local `uv` cache instead (`pytest` 9.0.2, `ruff` 0.15.8, `mypy`
1.19.1). Runnable subset: **1003 passed, 43 skipped, 51 failed — all 51 are
`ModuleNotFoundError`** for those packages (47 structlog, 3 google, 1 sqlalchemy), zero logic
failures. All 391 NFL tests pass. Full-suite verification still has to happen in CI or on the
Windows box. `mypy>=2.3.1` (the requirements floor) is not in the cache either; 1.19.1 is
what ran, and its `float()`-on-Optional reports are the documented false positives.

## Pre-existing, not touched
- `ruff check .` — 25 `F401` unused imports in `tests/` plus `append_feedback.py`,
  `scratch.py`, `script.py`. `make lint-check` only lints changed files, so these stay out of
  the gate; it passes clean on this branch's two files.
- `mypy` — 4 errors in `outlier_scrapers`, 8 in `outlier_nfl`, all unchanged baseline. Each
  `float()`/`int()`-on-Optional site was read and is already guarded by an `is not None` check
  or `except (TypeError, ValueError)`; the rest are `MutableMapping` vs `dict` invariance in
  `enrich_close.py` and missing `types-python-dateutil` stubs.

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
