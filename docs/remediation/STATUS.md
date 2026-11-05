# Deliverables 7 and 8 remediation - 2026-11-05

**PARTIAL / NOT READY / NO BET. Publication holds remain active.**

## Implemented and installed

- Organizer excludes SQLite/database sidecars, deduplicates unchanged archives,
  propagates copy errors, retains three verified complete runs per sport, and
  enforces a strict combined invocation growth below 50,000,000 bytes including
  manifests. Default free-space floor is more than 20 GiB. Cloud placeholders
  are allowed; redirecting or unknown reparse points are rejected.
- Removed eleven archived ledger copies only after matching their SHA-256 to
  the preserved 619,335,680-byte research copy. Reclaimed 6,812,692,480 bytes.
  Evidence: `C:/Users/dasil/outlier-quarantine/2026-11-05/removed-duplicate-ledgers.json`.
- Applied the user-approved OneDrive Pictures **Free up space** action. The
  provider retained cloud copies and reclaimed local storage. The measured
  organizer acceptance run had 56.15 GiB free and added **0 bytes** to Older_Data;
  all three post-quarantine Fresh_Data sport folders were empty. Free space
  continued increasing afterward. See `storage-acceptance.json`.
- Outlier daily, NBA refresh, universal sync and direct NFL refresh use active
  publication holds. NBA audit is held before any settlement or provider work.
  Native exits, fatal storage/source diagnostics, current slate dates and
  nonempty fresh exports are checked before publication on repaired paths.
  Missing or unverified producer evidence is a failure, not a fallback.
- The scheduled checkout's daily entrypoint also checks the external hold;
  actual execution exited nonzero before producer/status writes.
- Thirteen Gemini NBA early-look simulation/narrative/export entrypoints are
  retired in place. Hash-verified originals are outside the active skill tree;
  tombstones exit nonzero on invocation/import. No replacement pricing was
  invented. See `nba-generator-retirement.md`.
- CFB daily, next-day, sync, refresh and nightly audit are repaired and held.
  [CFB draft PR 25](https://github.com/DaSilvaDub/cfb-analytics/pull/25).
  Actual canonical next-day invocation exited nonzero at the hold before work.

## Verification

- Organizer: 38 temporary-tree tests passed, including cloud reparse tags,
  exclusions, retention, byte budgets, source changes and copy failures.
- Outlier orchestration: nine initial cases plus three new cases passed under
  Windows PowerShell 5.1: failed producer publishes nothing, empty/wrong date
  rejected, holds enforced, successful stderr accepted, failed daily exporter
  withheld, failed universal child prevents later children and summary.
- NBA retirement: five tests passed; actual tombstone invocation failed;
  active allowlisted generators have no numeric-percent literals.
- Quarantine: six tests passed, including the final hold-marker idempotence fix.
  **668 files** were withdrawn, **2 research databases** retained, **18 hold
  markers** written, and **0 move/hash errors** occurred. Eight absent sport
  subfolders are recorded. See `quarantine-scope.md` and execution manifest.
  Scope preserves unrelated MLB/WNBA files.
- CFB: 14 offline cases passed; six PowerShell scripts parsed; Ruff passed.
- No live betting pipeline, paid reasoning, or data/calibration mutation was
  used for these checks. Existing research ledgers remain unverified for staking.
- Pytest exited zero but emitted an existing Temp/pytest-current permission
  warning during its atexit cleanup. Test temporary directories were preserved.

## Unresolved acceptance gates

1. Windows denied `Disable-ScheduledTask` with `Access is denied`. The sports
   tasks remain enabled. Script holds protect repaired paths; missing scheduled
   wrapper files were not recreated. An administrator must pause the saved task
   inventory using `C:/Users/dasil/outlier-quarantine/2026-11-05/pause-scheduled-tasks-admin.ps1`.
2. Quarantine covers explicit publication roots, not every historical source
   artifact or unlocated Deliverable 8 category. Protected repository data and
   calibration were neither moved nor rewritten. They remain prohibited for
   staking until source-specific revalidation.
3. Universal sync and final multi-destination copies are sequential, not a
   transaction. No all-sport rollback guarantee is claimed. Regression evidence
   establishes that the checked upstream failures abort before their publishes.
4. Retirement disables the defective NBA betting source. It does not establish
   valid rosters, current odds, calibration, CLV, or a replacement betting model.

Do not remove the holds or resume betting production merely because tests pass.
