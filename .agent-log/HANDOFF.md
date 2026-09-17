## Daily automated debug review (claude) - 2026-09-17

- **Last Commit SHA**: `c4c5b12` on `claude/inspiring-fermat-b703ae`; PR #165 -> master.
- **Files Touched**: `outlier_nfl/utils.py` (new `coerce_odds` / `coerce_float`), `outlier_nfl/games.py`, `outlier_nfl/props.py`, `tests/test_nfl_normalizer.py` (9 regression tests).
- **Finding (High)**: `extract_game_lines` cast `outcome["bestOdds"]` with a bare `int()` and `extract_player_props` cast the `l5`/`l10`/`l20`/`curSeason` hit rates with a bare `float()`. Neither `normalize_game_markets` nor `normalize_player_props` is guarded at its `pipeline.py` call site, so one unparseable feed value (`"EVEN"`, `"N/A"`, `""`, a fractional price) aborted the entire slate - reproduced: 2 of 6 game lines survived, exception left `run()`. The `bestOdds` fallback four lines above the unguarded stats casts in `props.py` was *already* guarded, which settles the intended behaviour. All 9 tests confirmed failing on parent `1c48795`.
- **Verification**: NFL suites 233 passed / 2 skipped; whole offline suite 826 passed (was 817); ruff clean on changed files; mypy clean on the three changed modules. Sandbox has no PyPI egress (403 on CONNECT), so `structlog`/`sqlalchemy`/provider SDKs are uninstallable - every other reported failure is a `ModuleNotFoundError` for one of those. Hosted CI is authoritative.
- **Reported, not fixed**:
  - `pipeline.py:119/169/122/184` call the two normalizers unguarded. Per-event `try/except` would contain any future extraction crash to one event, but partial-slate output is a behavioural call for a human.
  - `requirements.lock` declares none of `structlog`, `SQLAlchemy`, `psycopg2-binary` (all in `pyproject.toml`) or `tzdata`; CI only works because the following `pip install -e .` resolves them unpinned, so they float every run. Needs PyPI access to regenerate. Also noted on #164.
  - `outlier_scrapers/api.py:17` calls `structlog.configure()` at import time, which mutates global structlog state for whatever imports it first. Not reproducible here (structlog uninstallable), so reported rather than touched.
- **Already covered, not duplicated**: PR #164 (open, all 4 checks green, `mergeable_state: clean`, rebased on current master) still carries the `outlier_nfl/utils.py` `_replace_with_retry` data-loss fix and the `organize_today_run2.py` counterpart. The handoff below recorded it as closed; it has since been reopened. Needs a human merge.
- **Master CI**: green on `1c48795` (core, provider, typecheck).
- **Next Steps**: review/merge #165 and #164. Paid reasoning / AI Research Desk was not invoked at any point.

1. **Last Commit SHA**: `3b0288b6dfccaefc840b6e300b9d505fb9e606da` (merge of PR #163)
2. **Files Touched**: `outlier_scrapers/desk_snapshot.py`, `tests/test_desk_snapshot.py`
3. **Next Steps**:
   - PR #163 reopened and merged at the user's explicit request, superseding the 2026-09-16 closure sweep recorded below. The daily-lock double-acquire it fixes was still live on master at that point (verified: `desk_snapshot.py` untouched since the PR branched).
   - On master now: claim-marker recovery is a chain of exclusive creates, a marker is never deleted to recover it, and an empty marker is only stranded past `DAILY_LOCK_OWNERLESS_GRACE` instead of 0.1s. Three regression tests landed with it. All CI green on the merged head (core, provider, typecheck, Codacy 0 issues).
   - **Still open**: PR #164 (`fix: two paths that delete the only remaining copy of a file`, branch `claude/inspiring-fermat-l965uf`, head 37d72a0) was closed unmerged in the same sweep. It covers the `outlier_nfl/utils.py` `_replace_with_retry` data-loss path from the 2026-09-15 debug review plus a second instance of the same defect class, and that finding is still live on master. Reopen it if wanted.
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
