## NFL Active Roster & Offseason Movement Authority (Gemini) - 2026-09-20
1. **Last Commit SHA**: `c72fb08` on branch `fix/nfl-active-roster-grounding` (PR #175: https://github.com/DaSilvaDub/outlier/pull/175)
2. **Files Touched**:
   - `outlier_nfl/roster.py`: Implemented full 32-team 2026 depth chart registry (`NFL_2026_FULL_DEPTH_CHARTS`), offseason player movement registry (`OFFSEASON_MOVES_2026` tracking Kenneth Walker III signed by KC as lead RB, Daniel Jones starting on IND, Aaron Rodgers on PIT, Geno Smith on NYJ, DK Metcalf on PIT, David Montgomery on HOU, Travis Etienne Jr. on NO, DJ Moore on BUF, etc.), and pre-flight text validation gate (`validate_analysis_text_for_roster_errors`) to mechanically block any references to former teams.
   - `tests/test_nfl_roster.py`: Added 4 comprehensive unit tests verifying 32-team depth charts, Kenneth Walker on KC, Daniel Jones on IND, and proving the text validation gate catches stale team hallucinations.
   - `.agents/AGENTS.md`: Updated Invariant 5 (Active Roster & Offseason Movement Authority Invariant) codifying all major 2026 offseason acquisitions and mandating grounding to `get_team_depth_chart` and `get_starting_qb`.
   - `data/NFL/normalized/nfl_rosters_latest.json`: Refreshed with complete 32-team verified rosters.
3. **Verification**:
   - 43/43 NFL tests passing (`pytest tests/test_nfl_roster.py tests/test_nfl_calibration.py tests/test_nfl_normalizer.py tests/test_nfl_pipeline.py`).
   - PR #175 updated targeting `master`.
4. **Next Steps**:
   - Merge PR #175.
   - No paid reasoning models were invoked.

## Pitcher Handedness Splits (Gemini) - 2026-09-20
1. **Last Commit SHA**: 81b16c4 on branch feat/pitcher-handedness-splits (PR #174: https://github.com/DaSilvaDub/outlier/pull/174)
2. **Files Touched**:
   - outlier_scrapers/projections.py: Added etch_pitcher_handedness, modified etch_team_batter_k_rate to fetch s LHP / s RHP splits and fallback to global season stats if PA < 50, and updated nrich_probable_with_so_features to cache the splits correctly.
3. **Verification**:
   - 34/34 	est_projections.py tests passed.
   - Offline test suite (1,892 tests) passed (excluding one known pre-existing intermittent multithread contention failure).
   - PR #174 opened targeting master.
4. **Next Steps**:
   - Review and merge PR #174.
   - Run the full pipeline when new probable pitchers are listed to observe the split projections.
   - No paid reasoning models were invoked.
## NFL Slate Extraction, Multi-Window Prop Re-Basing & Situational Calibration (Gemini) - 2026-09-20
1. **Last Commit SHA**: `34a225b` on branch `feat/nfl-prop-rebasing-protocol` (PR #173: https://github.com/DaSilvaDub/outlier/pull/173)
2. **Files Touched**:
   - `.agents/AGENTS.md`: Updated NFL game script calibration heuristics to mandate multi-window hit rate convergence (L5 >= 80% and L10 >= 70-80%), balanced line movement / multi-book consensus (-145 to +115), and compiled situational analysis (injuries, matchups, weather).
   - `.agents/skills/nfl-game-script/SKILL.md`: Expanded runbook with 5-pillar evaluation framework (multi-window hit rate re-basing, line movement & steam auditing, compiled Outlier stats, injury/personnel vacancy redistribution, and weather/venue calibration).
3. **Verification**:
   - Extracted 2026-09-20 NFL slate (14 games, 19,335 game lines, 48,425 props, 12,996 consensus lines, 567 Tier-1 anchors).
   - 39/39 NFL tests passed in 1.95s (`pytest tests/test_nfl_calibration.py tests/test_nfl_normalizer.py tests/test_nfl_pipeline.py`).
   - `ruff check .agents/` clean (all checks passed).
   - PR #173 opened targeting `master`.
4. **Next Steps**:
   - Review and merge PR #173 when hosted CI passes.
   - Paid reasoning models were not invoked.

## Nightly Calibration, Results Audit & Learning Invariants (Gemini) - 2026-09-20
1. **Last Commit SHA**: `ff641e8` on branch `feat/nightly-recalibration-and-learn-audit` (PR #172: https://github.com/DaSilvaDub/outlier/pull/172)
2. **Files Touched**:
   - `.agents/AGENTS.md`: Documented codebase quirks and heuristics (`calibration/feedback.sqlite3` DB path invariant, `_test_fixture_*` directory naming invariant, MLB opponent handedness K% split heuristics, and exchange discount vs thin liquidity calibration rules).
   - `.agents/skills/nightly-calibration-audit/SKILL.md`: Added step 1 for automated results grading (`python -m outlier_scrapers.results`) and updated feedback database references.
   - `calibration/alerts/nightly_audit_status.json`: Nightly audit status tracking.
   - `calibration/blend_weights.json`: Re-fitted segment blend weights across updated settled history.
   - `calibration/stake_calibration.json`: Re-fitted empirical reliability factors across 19,560 eligible samples trained through 2026-09-19 slate.
3. **Verification**:
   - 2026-09-19 results graded (434 decisions settled): Published Board A plays went 3W - 1L (+1.227u, +35.05% ROI). Rejected Board A props (37 total) contained 19 losses, 9 pushes, 9 hits (blocked 2.1 losses per missed win).
   - Replay portfolio confirmed positive expectation (+4.54u net profit on 144 plays; 28,779 stand-downs filtered out -2,254.93u of losses).
   - 67/67 feedback tests passed in 22.10s (`pytest tests/test_feedback.py`).
4. **Next Steps**:
   - Merge PR #172 when hosted CI checks pass.
   - Paid reasoning models were not invoked.

## Settlement Team Aliases, Totals Parsing & Heavy-Dog Spread Cap (Gemini) - 2026-09-19
1. **Last Commit SHA**: `a326f62` on branch `fix/settlement-team-aliases-and-totals` (PR #171: https://github.com/DaSilvaDub/outlier/pull/171)
2. **Files Touched**:
   - `outlier_scrapers/results.py`: Added `"NYL": "NY"` and `"CONN": "CON"` to `TEAM_ALIASES`; updated `_team_total_event_match` and `_grade_row` to support matchup-prefixed team totals (`IND @ TOR Indiana Fever - Points UNDER 100.5`) and excluded matchup strings from player prop matching.
   - `outlier_scrapers/slate_quality.py`: Added `apply_wnba_heavy_dog_spread_cap` capping WNBA double-digit dogs (+12.0+) at 0.5u (or 0.25u when multiple starters are out) with flag `wnba_heavy_dog_deficit_cap`.
   - `outlier_scrapers/pack_selection.py`: Wired `apply_wnba_heavy_dog_spread_cap(row, injury_view)` right after `apply_local_devig_unit_cap(row)`.
   - `tests/test_results.py`: Added regression tests for `NYL` alias and matchup-prefixed team totals (13/13 passed).
   - `tests/test_slate_quality.py`: Added unit tests for `apply_wnba_heavy_dog_spread_cap` (17/17 passed).
3. **Verification**:
   - Targeted unit tests: 13 results tests, 17 slate quality tests, 161 pack integration tests passed.
   - Ruff lint clean on all modified files.
4. **Next Steps**:
   - Paid reasoning models were not invoked.

## Daily Debug Review — master CI red + NFL calibration inputs (Claude) - 2026-09-19
1. **Last Commit SHA**: `0063058` on branch `claude/inspiring-fermat-v8n34k` — **MERGED** as `cbcad6f` (PR #169: https://github.com/DaSilvaDub/outlier/pull/169). master is green again: run 598 on `fdb3398` passed.
2. **Files Touched**:
   - `tests/test_nfl_calibration.py`: `test_game_script_generator_output` no longer sources its report from the gitignored `data/NFL/normalized`; it builds games/props in-test from the existing helpers and keeps every assertion. Added 3 regression tests (quoted team totals below the default, default fallback when none quoted, consensus never selects an unpriced line).
   - `outlier_nfl/calibration.py`: `extract_game_script_context` no longer seeds its team-total accumulators with the fallback defaults, so a quoted total below the default is reported instead of the default. The `>= 28.0` deficit trigger is unchanged.
   - `outlier_nfl/consensus.py`: the balanced-line filter skips a side with no `best_odds` instead of coercing it to 0 (which sat inside the -220..180 band and let an unquoted line win the consensus).
   - `scripts/nfl_game_script.py`: same unpriced-side fix in the generator's copy of that filter.
   - **Review round (`0063058`)**: Copilot found the unpriced-line guard incomplete — the fallback below the balanced scoring still ranked every line, so a market entirely off the board returned an arbitrary line; the touchdown branch had the same gap (`best_odds or -9999` orders unpriced candidates last but never excludes them). Both fallbacks now consider only lines carrying a price somewhere. Scoped narrower than the review suggested: requiring a priced OVER *and* UNDER would also discard a genuinely one-sided priced line. Also re-added coverage of `load_data()` against a pipeline run in `tmp_path`. Both review threads answered and resolved.
3. **Verification**: 29/29 NFL tests pass. Each new regression test confirmed to fail against pre-fix source. Pipeline re-run offline: status OK, 10 game lines, 8 props, 8 consensus, 0 errors. Hosted CI green on core, provider, typecheck, Codacy.
4. **Root cause of red master**: `.gitignore:35` excludes `data/*`, so `test_game_script_generator_output` passed only on a machine that had already run the pipeline.
5. **Next Steps / Outstanding**:
   - **Concurrency note for the next ent**: PR #168 ("make the game script test and its report path independent of the machine") fixed the same red test as this PR, from another session, and merged ~40 minutes earlier; a human had to reconcile the two (`3afb638`, `a6c4bd8`). #168 also fixed the CWD-relative `reports/NFL` path that this review reported but deliberately left alone. STEP 0 verifies branch/commit *visibility* but says nothing about which PRs are already open and in flight — check open PRs for the area you are about to touch before starting, not just the sync report.
   - Paid reasoning models were not invoked.

## Daily automated debug review (claude) - 2026-09-17

- **Last Commit SHA**: `85d440a` on `claude/inspiring-fermat-b703ae` (plus a merge of `origin/master` `6e490a4`); PR #165 -> master.
- **Files Touched**: `outlier_nfl/utils.py` (new `coerce_odds` / `coerce_float`), `outlier_nfl/games.py`, `outlier_nfl/props.py`, `tests/test_nfl_normalizer.py` (9 regression tests).
- **Finding (High)**: `extract_game_lines` cast `outcome["bestOdds"]` with a bare `int()` and `extract_player_props` cast the `l5`/`l10`/`l20`/`curSeason` hit rates with a bare `float()`. Neither `normalize_game_markets` nor `normalize_player_props` is guarded at its `pipeline.py` call site, so one unparseable feed value (`"EVEN"`, `"N/A"`, `""`, a fractional price) aborted the entire slate - reproduced: 2 of 6 game lines survived, exception left `run()`. The `bestOdds` fallback four lines above the unguarded stats casts in `props.py` was *already* guarded, which settles the intended behaviour. All 9 tests confirmed failing on parent `1c48795`.
- **Verification**: NFL suites 233 passed / 2 skipped; whole offline suite 826 passed (was 817); ruff clean on changed files; mypy clean on the three changed modules.
- **Review round**: Copilot found a real gap in the first fix - `float()` raises `OverflowError` for an integer too large to convert; fixed in `85d440a`.
- **CI**: all 4 checks green on `85d440a` before the base merge.
- **Next Steps**: review/merge #165. Paid reasoning / AI Research Desk was not invoked at any point.
## Daily Debug Review (Claude) - 2026-09-18
1. **Branch**: `claude/inspiring-fermat-rcb6v7` (from `5d2b30a`), reviewing the NFL consensus/calibration work in `446e718`.
2. **Files Touched**: `tests/test_nfl_calibration.py`, `outlier_nfl/pipeline.py`, `outlier_nfl/consensus.py`.
3. **Fixed**:
   - `test_game_script_generator_output` read `data/NFL/normalized` (gitignored, cwd-relative) and hard-coded 2026-09-17, so it failed in every clean checkout and in CI. It now builds its own DET @ BUF dataset under `tmp_path`; the assertions are unchanged and verified data-driven.
   - `NflPipeline.run(generate_game_script=True)` always wrote into cwd `reports/NFL`, so the test suite left an untracked report in the repo. `run()` now takes `reports_dir` (default unchanged) and the test points it at `tmp_path`.
   - `select_consensus_player_props` keyed groups on (event, player, market) only, letting a 1H/1Q line take the consensus flag away from the full-game line. `scope` is now part of the key, with a regression test.
4. **Reported, not fixed** (see PR body): alternate-ladder `max()` selection for away spread / home team total in `extract_game_script_context`; `--generate-game-script` renders the DET @ BUF narrative and fabricated fallback lines for any slate; `best_odds or 0` treats a missing price as balanced; 2 pre-existing mypy errors and 17 pre-existing ruff F401s.
5. **Environment note**: PyPI is blocked in this sandbox (403), so `structlog`/`sqlalchemy`/`google-genai` could not be installed; 48 collection errors and 50 dependency-only failures are environment, not repo. NFL/offline-stdlib tests all pass. Also: GitHub Actions has not run on master since 2026-09-05, so nothing caught the broken test.
6. **Next Steps**: merge the PR; decide on the alternate-ladder selection rule; PRs #165, #166, #167 remain open. Paid reasoning was not invoked.

>>>>>>> origin/master
## Board A Calibration Hardening & Predictor Gates (Gemini) - 2026-09-18
1. **Last Commit SHA**: `b6942e8` on branch `feat/board-a-calibration-hardening` (PR #167: https://github.com/DaSilvaDub/outlier/pull/167)
2. **Files Touched**:
   - `outlier_scrapers/cards.py`: Added event date extraction, target date filtering, and off-slate card isolation; added cold/zero L5 3PT OVER detection.
   - `outlier_scrapers/slate_quality.py`: Added `low_volume_3pt_shooter`, `star_scorer_usage_up_under`, `team_total_scoring_conflict`, and `opponent_high_k_rate_conflict` gates; added `september_pitcher_so_under_signal` (+1.0 rank boost) and `guard_rebound_over_signal` (+0.5 rank boost).
   - `outlier_scrapers/pack_selection.py`: Registered all new disqualifiers in `DISQUALIFYING_DQ_FLAGS`; wired card/hit-rate context into quality flag evaluation.
   - `tests/test_cards.py`: Regression tests for off-slate isolation and low-volume 3PT shooter card flagging.
   - `tests/test_slate_quality.py`: 5 tests for all new gates, signals, and tie-break boosts.
   - `tests/test_pack.py`: 3 end-to-end integration tests verifying disqualification of Board A actionable status.
3. **Verification**:
   - Targeted unit tests: 245 passed in 11.72s.
   - Full offline repository test suite: 1784 passed, 2 skipped, 0 failed in 355.06s.
   - PR #167 opened targeting `master`.
4. **Next Steps**:
   - Merge PR #167 when ready.
   - Paid reasoning was not invoked.

## NFL Game Script & Calibration Upgrades (Gemini) - 2026-09-18
1. **Last Commit SHA**: `446e718b577312db1e2634e40e6c518eb3074092` on branch `feat/nfl-game-script`
2. **Files Touched**:
   - `outlier_nfl/consensus.py` (NEW): Balanced two-way consensus line selector (-220 to +180) eliminating ladder distortions; touchdown line == 0.5 enforcement.
   - `outlier_nfl/calibration.py` (NEW): Road underdog RB deficit risk haircut (-15%), two-high shell target divergence (+20% slot, +15% TE, -25% deep threat), empirical hit-rate tiering (`TIER_1_ANCHOR` / `TIER_2_STRONG`).
   - `outlier_nfl/models.py`: Added `is_consensus_line`, `confidence_tier`, `calibration_tags`, `calibrated_volume_adjustment`.
   - `outlier_nfl/normalizer.py`: Re-exports for consensus and calibration.
   - `outlier_nfl/pipeline.py`: Integrated consensus and calibration into `run()`; persists `nfl_calibrated_props_*.json` and `nfl_high_prob_props_*.json`; added `--generate-game-script` flag and CLI summary counts.
   - `scripts/nfl_game_script.py`: Full game script generator tool supporting dataclasses and dicts with on-the-fly calibration fallback and calibrated signals.
   - `tests/test_nfl_calibration.py` (NEW): 9 unit/integration tests for consensus, push prob, deficit risk, haircuts, shells, tiering, and report generation.
   - `.agents/skills/nfl-game-script/SKILL.md` (NEW) & `.agents/AGENTS.md`: Persistent rules and runbooks.
   - `reports/NFL/2026-09-17_DET_BUF_Game_Script.md` & `reports/NFL/2026-09-17_DET_BUF_Postgame_Calibration.md`: Generated artifacts.
3. **Next Steps**:
   - Branch `feat/nfl-game-script` contains all implemented and verified work.
   - All 22 NFL tests passing (`pytest tests/test_nfl_normalizer.py tests/test_nfl_pipeline.py tests/test_nfl_calibration.py -v`).
   - Ruff lint passing with zero errors. Paid reasoning was not invoked.

## Previous Handoff (Claude) - 2026-09-17
1. **Last Commit SHA**: `7a9ac7deff2af2a2dfc33549efc5b217d59cd568` (merge of PR #164)
2. **Files Touched**: `outlier_scrapers/desk_snapshot.py`, `outlier_nfl/utils.py`, `scripts/organize_today_run2.py`, `pyproject.toml`, and their tests
3. **Next Steps**:
   - PRs #163 and #164 were both closed unmerged in the 2026-09-16 sweep, then reopened and merged on 2026-09-17 at the user's explicit request (`3b0288b`, `7a9ac7d`). Every finding from the 2026-09-15 and 2026-09-16 debug reviews is now on master; verified against master rather than inferred from the merges.
   - Landed: daily-lock claim recovery is a chain of exclusive creates and never deletes a marker; the NFL atomic write moves the destination aside instead of unlinking it and restores it on failure, without clobbering a newer concurrent write; filing a loose prompt only removes the source once the copy lands; export copies stage and move into place so a failure partway cannot truncate the previous export; `tzdata` is declared and `to_eastern_date`'s fallback derives the DST offset from the rule (checked hourly against the real zone across 2021-2030, zero disagreements).
   - **Outstanding, needs a machine with PyPI access**: `requirements.lock` still omits `tzdata`, `structlog`, `SQLAlchemy` and `psycopg2-binary`. CI resolves them through the `pip install -e .` that follows the frozen install, so those four float on every run. Regenerate with `uv export --frozen --no-hashes --all-extras -o requirements.lock`. Hand-editing it was deliberately avoided.
   - **Outstanding, own change**: the daily lock can still be taken over from a claimer stalled past the 30s `DAILY_LOCK_OWNERLESS_GRACE` between its `os.open` and `os.write`. Same structural trade `_daily_lock_is_abandoned` already makes one level up; closing it needs an atomic compare-and-swap on `owner.json` that the file-per-marker scheme cannot express.
   - Repository clean and in sync with `origin/master`. Paid reasoning models were not invoked.


## Codex merge batch - 2026-09-14
- Last merged commit: 2e6e06400a7824d8b00b485bf6c1820f05cab8f4 (PR #136); prior merge dca927305e9905531891c951807450c37bdb5aa0 (PR #162).
- Files touched by merged PRs: sync hook/docs and AGENTS.md; NFL schema validation; stake calibration fingerprint helper and regression tests; feedback_reporting.py JSON writing. This session added only this handoff.
- Verification: independent review found no blockers and zero review threads; hosted PR tests/typechecks passed. PR #162: 195 targeted Windows tests passed on rerun (initial run had one intermittent unchanged 50-thread contention failure). Ruff reported 12 pre-existing unused imports in unchanged challenger imports. PR #136: 69 feedback tests passed on integration with updated master; changed-file Ruff passed.
- Next steps: review remaining PRs in batches. #149 needs explicit push/void/unfinished grading and actionable-only export semantics. Conflicting PRs require individual reconciliation; #159/#160 overlap #162 and should be compared for supersession before closing. #161 is a larger NFL review. No PRs closed as duplicates this session. Paid reasoning was not invoked.

## PR #158 reconciliation - 2026-09-14
- Base commit: 25e10917ecfc2f7563def87d40d8ae3f769eb028.
- Files touched: pack_publish.py removes the second policy load and weaker duplicate enforce gate; test_pack.py adds missing-market_snapshots fail-closed regression. Handoff conflict resolved preserving current master notes.
- Validation: 161 pack tests passed; Ruff passed for both changed Python files. Independent review found no blocker; prior review thread resolved.
- Next: await fresh hosted CI before merging this PR. #159/#160 were closed as superseded by #162; #152 crash-recovery repair is in a separate worktree.

## Gemini PR Resolution & Merge Completion - 2026-09-15
- **Last Commit SHA**: db0f1d121814dc8614a9a447478a47462f9fa25a
- **Repository Scope Completed (18/18 PRs Resolved -> 0 Open PRs Remaining)**:
  1. **outlier (11/11 resolved)**:
     - PR #162: Merged (CI fixes and sync marker correction).
     - PR #136: Merged (streaming JSON reports).
     - PR #160 & #159: Closed as superseded by PR #162.
     - PR #158: Merged (drop duplicate weaker enforce gate in write_pack; all CI checks green).
     - PR #152: Merged (silent export copy loss, daily lock atomic recovery with dead claim marker crash recovery, mypy pin; all 29 tests and CI green).
     - PR #150: Closed as superseded by newer calibration snapshots on master through 2026-09-14 (commit d40b027).
     - PR #149: Merged (consolidated playable props export with actionable-only filter semantics, explicit push/void/unfinished grading).
     - PR #161: Merged (complete standalone NFL betting pipeline extraction and normalization; 311 unit & adversarial stress tests passing, all CI green).
     - PR #118: Closed as superseded (audit remediation landed via #119, #122, and modular refactors).
     - PR #141: Merged (structlog API latency and throttle tracking with safe response getcode extraction; all CI green).
  2. **nba-props-pipeline (1/1 resolved)**:
     - PR #1: Merged (browser headers on FantasyLabs requests, test isolation; all CI green).
  3. **NBA-SCRIPTS (1/1 resolved)**:
     - PR #1: Merged (removed exposed hardcoded odds API key, added check_system_health.py).
  4. **Sports_Analytics (5/5 resolved)**:
     - PR #5: Merged (CTG scraper hardening, core utilities, output contracts).
     - PR #2: Closed as superseded by PR #5 (duplicate targeting main).
     - PR #3: Closed as superseded by PR #5 (Hard Rock odds service draft).
     - PR #4: Merged (verify_endpoints.py utility added to master).
     - PR #6: Closed as superseded by PR #5 (superseded by modular SQLite odds_service architecture).
- **Verification**: All merged PRs passed local tests, lint, and full hosted GitHub Actions CI suites. Zero uncommitted changes. Paid reasoning models were not invoked.


## Daily automated debug review (claude) - 2026-09-15
- **Last Commit SHA**: 9c291980a79bd20abeaa61dea8ee3b762e7f5eca on `claude/inspiring-fermat-g7a512`; PR #163 -> master (green, `mergeable_state: clean`, awaiting human review).
- **Files Touched**:
  - `outlier_scrapers/desk_snapshot.py`: `_claim_abandoned_daily_lock` recovery no longer deletes a claim marker; new `_abandoned_claim_token` helper.
  - `tests/test_desk_snapshot.py`: two regression tests (empty marker not stolen mid-claim; recovery never deletes the stranded marker).
- **Finding**: `31cdd18` (merged via #152 on 2026-09-15) reintroduced the double-acquire it was meant to guard against. Recovering a stranded claim marker by unlink-then-recreate lets two runs both come away holding the daily lock, so two daily jobs write the same pack and ledger. Reproduced deterministically; both new tests fail on the pre-fix code.
- **Sandbox constraint**: PyPI egress is blocked by proxy policy (403 on CONNECT), so `pytest`/`ruff`/`mypy` could not be installed. Verified by replaying the 8 existing daily-lock test bodies plus the 2 new ones against `desk_snapshot` loaded in isolation (intra-package imports stripped). Hosted CI on PR #163 is the authoritative check.
- **Reported, not fixed** (needs a human call):
  - `outlier_nfl/utils.py:54` `_replace_with_retry` last-ditch branch runs `dst.unlink()` then retries the replace; when that retry also fails the previous good `nfl_*_latest.json` is deleted and nothing replaces it. Confirmed by direct execution. Suggested fix: rename `dst` aside, replace, delete the backup on success and restore it on failure. Untestable from Linux, so left alone.
  - `outlier_nfl/utils.py:183` `to_eastern_date` falls back to a fixed UTC-5 when `zoneinfo` has no tz database, which is wrong during EDT (most of the NFL season). Current kickoff times still bucket to the right date, so it is latent rather than active.
- **Review round**: Copilot found a real defect in the first fix -- the recovery marker could itself strand (a run that won it and died before rewriting owner.json deadlocked the lock permanently, the same failure one level down). Reproduced, fixed in 9c29198: the markers now form a chain, every link recoverable on the same terms, each step still one exclusive create, name kept fixed-length via a hashed trail, walk bounded by MAX_CLAIM_RECOVERY_DEPTH. Thread resolved. Copilot's second (self-suppressed) point -- a claimer stalled past the 30s grace can still be taken over -- was left deliberately and answered on the thread: it is the same structural trade `_daily_lock_is_abandoned` already makes one level up, and closing it needs an atomic compare-and-swap on owner.json that the file-per-marker scheme cannot express. That is a good separate change if anyone wants it.
- **Next Steps**: #163 was closed unmerged on 2026-09-16 in a PR-queue sweep, then reopened and merged on 2026-09-17 at the user's explicit request (master `3b0288b`). Nothing left on it. The two `outlier_nfl/utils.py` findings from this review remain unaddressed on master; PR #164 covers the first. Paid reasoning / AI Research Desk was not invoked at any point.

## Daily automated debug review (claude) - 2026-09-16
- **Branch**: `claude/inspiring-fermat-l965uf`.
- **Files Touched**:
  - `outlier_nfl/utils.py`: `_replace_with_retry` last-resort branch renames the destination aside instead of unlinking it, and restores it when the retry fails.
  - `tests/test_nfl_stress_m1.py`: two platform-independent regression tests for that branch (previous file survives a failed last-resort retry; the branch still lands the replace once the name is free).
- **Finding (fixed)**: the last-resort branch ran `dst.unlink()` then retried `src.replace(dst)`. When that retry also failed, `nfl_games_latest.json` / `nfl_props_latest.json` was already gone, and `safe_write_json`'s `finally` then removed the temp file holding the new data -- both copies lost. Reproduced deterministically by injecting a sharing violation; the new test fails on the pre-fix code (`assert dst.exists()`) and passes after. Carried over from the 2026-09-15 review, which reported it as "untestable from Linux"; injecting the error rather than provoking it with a real Windows handle makes the branch coverable on POSIX.
- **Finding 2 (fixed)**: `scripts/organize_today_run2.py` `copy_prompt_outputs` files each loose `*.txt` prompt into its bucket with `safe_copy(...)` and then removed the source unconditionally. `safe_copy` is non-fatal by design, so a copy it gave up on left the prompt in neither place. `safe_copy` now reports whether the file landed and the source is only removed once it has. The deliberate delete of a loose sequential prompt when `desk2_prompts is None` is preserved (asserted at `tests/test_organize_today_run2.py:253`).
- **Review round**: Copilot found a real regression in the first fix -- rolling the backup into place unconditionally could overwrite a newer write that a concurrent `safe_write_json` landed in the window where the name was free, resurrecting stale contents over a write that succeeded. Concurrent writers on one destination are explicitly supported (`test_safe_write_json_concurrent_same_file`). Fixed: the restore goes through `os.link`, which refuses a name that already exists, so the newer write wins in one step; where the filesystem has no hard links it falls back to a guarded rename. Its two self-suppressed points were also taken: backup removal now retries transient locks instead of leaking a hidden `.bak`, and the restore-failure branch has a test. Reproduced first -- the new race test fails on the pre-fix commit and passes after.
- **Reported, not fixed** (needs a human call):
  - `scripts/organize_today_run2.py:71` `safe_copy`'s `shutil.copyfile` fallback truncates the destination before writing, so a failure partway leaves a truncated export in the `today` folder that looks present. Only warns. Not changed: the recovery would need a temp-and-rename, which is a larger change to a path that is regenerated every run.
  - `requirements.lock` is missing three dependencies `pyproject.toml` declares: `structlog`, `SQLAlchemy`, `psycopg2-binary` (added in `81afcb2`, never re-exported). CI's "Install frozen dependencies" only works because the following `pip install -e .` resolves them unpinned, so those three float on every run. Regenerating needs `uv export` with PyPI access, which this sandbox does not have.
  - `tzdata` is likewise undeclared, and `outlier_scrapers/results.py:42` builds `ZoneInfo("America/New_York")` at import with no fallback. Windows has no system tz database, so a clean Windows install from the lock cannot import `results.py`. Works today only because the canonical box happens to have `tzdata`.
  - `outlier_nfl/utils.py:183` `to_eastern_date` still falls back to a fixed UTC-5 when `zoneinfo` has no database, which is wrong during EDT (most of the NFL season). Latent: current kickoff times still bucket to the right date. Fixing it properly means declaring `tzdata`, above.
- **Verification**: hosted CI on PR #164 green for the first fix (core, provider, typecheck, Codacy). PyPI egress is blocked by proxy policy (403 on CONNECT), so `pytest`/`ruff`/`mypy` could not be installed. Replayed the offline suite against a minimal pytest stand-in: 985 passed, 0 genuine failures; the 65 reported failures and 26 import errors are all `ModuleNotFoundError` for the uninstallable `sqlalchemy` / `google-genai`. The five provider suites were not run. `pyright outlier_nfl/utils.py` clean; `compileall` clean. Hosted CI on the PR is the authoritative check.
- **PR #163** (2026-09-15 lock fix) was closed unmerged in the 2026-09-16 sweep, then reopened and merged on 2026-09-17 at the user's explicit request (master `3b0288b`).
- **Next Steps**: #163 is merged. Regenerate `requirements.lock` from a machine with PyPI access. Paid reasoning / AI Research Desk was not invoked at any point.


## Daily automated debug review (claude) - 2026-09-17
- **Branch**: `claude/inspiring-fermat-ntqiql`; last commit `d838f76`; PR #166 -> master.
- **Files Touched**: `outlier_nfl/games.py` (event-id guard in `extract_game_lines`), `tests/test_nfl_normalizer.py` (two regression tests).
- **Finding (fixed)**: `extract_game_lines` resolved a market's event id as `market.get("eventId") or market.get("id")`. `id` on a market is the *market's own* identifier -- `validate_event_markets_payload` accepts it as the `marketId` fallback and the next line of `extract_game_lines` reads it as one. A markets payload whose markets carry `id` instead of `marketId` and do not repeat the event id (clean by that validator) therefore had every market compared against the event id, mismatched, and skipped: no spread, no total, no moneyline, no team totals, and nothing to flag the empty board. Reproduced first -- the new test extracts 0 of 10 lines on the pre-fix code. Latent against the shape the live feed sends today (`sample_market.json` carries both keys), but `pipeline.py` passes `fetch_event_markets` straight through, so the shape is the feed's to change.
- **Reported, not fixed**:
  - `outlier_nfl/props.py:44` has the same conflation (`outcome.get("eventId") or outcome.get("id")`, where `id` is the outcome's id). Narrower: `validate_player_props_payload` requires outcome `eventId`, so only an already-invalid payload reaches it, and the result is a fabricated `event_id` on the exported prop rather than a lost board. Left alone because an empty `event_id` changes exported records and downstream consumers were not all checkable from here.
  - `outlier_nfl/utils.safe_write_json` flushes but never `os.fsync`s before the replace, so the write it documents as atomic can still land a truncated or empty target across a crash. `runner_common`, `refresh`, `feed_health` and `ultimate_alt_report` all fsync before replacing; `outlier_scrapers/utils._write_csv` and `safe_write_text` do not either, so this is a convention the repo applies unevenly rather than a regression. Worth one decision across all of them.
  - **Still outstanding from 2026-09-16**: `requirements.lock` omits `tzdata`, `structlog`, `SQLAlchemy`, `psycopg2-binary`. Confirmed today that `uv.lock` does not carry them either, so `uv export --frozen` cannot close it offline -- it needs `uv lock` on a machine with PyPI access. CI still resolves them through the trailing `pip install -e .`, so all four float on every run.
  - **Still outstanding from 2026-09-15**: the daily lock can be taken over from a claimer stalled past the 30s `DAILY_LOCK_OWNERLESS_GRACE` between its `os.open` and `os.write`. Needs an atomic compare-and-swap on `owner.json` that the file-per-marker scheme cannot express.
- **Verification**: PyPI egress is still blocked by proxy policy (403 on CONNECT), confirmed directly. Ran the real `pytest` 9.0.2 (a uv tool in the image) against the repo with sandbox-only import shims for `structlog` and `sqlalchemy` kept outside the tree -- the sqlalchemy shim raises on any actual session use, so a test that needs the database fails loudly instead of passing against a fake. Offline suite (`-m "not provider"`): **1686 passed, 45 skipped**; the 23 failures and 9 collection errors are all `ModuleNotFoundError`/`SandboxSQLAlchemyUnavailable` for uninstallable dependencies, and are identical to the pre-change baseline. `ruff check` clean on both changed files (12 pre-existing F401s in `tests/test_challenger_adversarial.py` are untouched and outside the Makefile's changed-file lint). `pyright outlier_nfl/games.py` clean. `mypy outlier_scrapers` reports 2 arg-type errors in `schema.py`/`probable_pitchers.py` -- the known pre-2.3.1 false positives `requirements.txt` documents; the sandbox image carries mypy 1.19.1, below the declared floor, so this is the sandbox and not the code. Hosted CI on #166 is the authoritative check.
- **Outcome**: PR #166 merged to master on 2026-09-19 (`2e2c3fb`). All four checks were green on the head (`core`, `provider`, `typecheck`, Codacy: 0 new issues), `mergeable_state: clean`, and no review threads were opened on it. Verified against master rather than inferred from the merge: `outlier_nfl/games.py` carries the corrected guard.
- **Next Steps**: the three "reported, not fixed" items above are still open on master and are the natural next pickups; `requirements.lock` still needs regenerating from a machine with PyPI access. Open PRs at the time of writing: #165 (another session's NFL slate-abort fix), #167 (board A calibration hardening), #169 (green master / invented calibration inputs) -- none of them this session's. Paid reasoning / AI Research Desk was not invoked at any point.

