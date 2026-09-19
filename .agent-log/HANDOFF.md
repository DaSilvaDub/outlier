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
   - PR #171 opened targeting `master`.
4. **Next Steps**:
   - Merge PR #171 when review completes.
   - Paid reasoning models were not invoked.

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
