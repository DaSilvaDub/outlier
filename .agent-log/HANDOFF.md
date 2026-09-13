Last Commit SHA: cd6cc0f (branch `claude/inspiring-fermat-nma87w`)
Files Touched: outlier_nfl/schema.py, outlier_scrapers/stake_calibration.py,
tests/test_calibration_upgrades.py, tests/test_challenger_adversarial.py,
tests/test_nfl_stress_m1.py

Summary: Daily automated debug review. Offline Pytest had been red on master
since a3ad900 (2026-09-11) with 4 failures; all four are fixed here.

1. `_validate_book_entry` (outlier_nfl/schema.py) guarded `to_dict()` raising but
   not the attribute lookup. `hasattr`/`getattr` only swallow AttributeError, so
   a `to_dict` property raising anything else aborted the whole NFL dataset
   validation pass. Now resolved defensively.
2. The two `_replace_with_retry` lock tests in tests/test_nfl_stress_m1.py assume
   Windows mandatory file locking; POSIX `replace()` never blocks, so they always
   failed on ubuntu-latest. Skipped off win32.
3. tests/test_calibration_upgrades.py pinned `artifact_version` /
   `eligible_samples` of the nightly-regenerated calibration/stake_calibration.json.
   Replaced with a self-consistency check against the new
   `stake_calibration.compute_artifact_fingerprint()` helper.

Next Steps:
- Watch the PR's Offline Pytest run; it should be the first green master-bound
  run since ec02340.
- Open item, not fixed here: `calibration/stake_calibration.json` and
  `calibration/blend_weights.json` are live artifacts committed by the
  auto-snapshot job. Any test that pins their values will break the same way —
  prefer structural assertions over snapshot values.
- Open item: AGENTS.md "Cloud / Sandbox Agents" STEP 0 greps for
  `_acquire_pack_lock` in daily_job.py. That symbol no longer exists (it is
  `_acquire_writer_lock` since 451d219), so marker 5 always reports MISSING on
  cloud ents. The Windows report-sync.ps1 path may have the same stale marker.
- Open item: CI type-checks `outlier_scrapers` only; `outlier_nfl` is not covered
  by mypy or pyright in .github/workflows/typecheck.yml.
