1. **Last Commit SHA**: `0b6a2acd27f23f35c60d7d1412d25f1871ea125b`
2. **Files Touched**:
   - `calibration/alerts/daily_pipeline_status.json`: Daily pipeline status receipt
   - `calibration/blend_weights.json`: Blend weights refit from settlement collection
   - `today/playable_props*` (Desktop & Google Drive): Consolidated playable props export
3. **Next Steps**:
   - Slate for 2026-09-15 executed with paid reasoning strictly OFF (`daily_job.py --analysis-profile local --leagues MLB,WNBA`).
   - Pitcher identity audit clean: `status=ok`, `so_rows=14`, 0 mismatches, 0 unconfirmed, 0 fail-closed.
   - All prompt and dataset exports distributed to Desktop and Google Drive `today` folders.
   - Consolidated `playable_props.md` and `playable_props.csv` generated: 0 actionable Board A plays qualified on tonight's 29-candidate evening slate; all 29 candidates logged with disqualification/status flags for full auditability.
   - Run post-game accuracy audit tomorrow morning (`2026-09-16`) after evening games conclude.


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
- **PR #163** (2026-09-15 lock fix) is still open, all four checks green, `mergeable_state: clean` -- waiting on a human merge, nothing left for an agent.
- **Next Steps**: merge #163; regenerate `requirements.lock` from a machine with PyPI access. Paid reasoning / AI Research Desk was not invoked at any point.
