# Handoff Summary

**Branch / PR**: `claude/fix-this-bd82a2` -> https://github.com/DaSilvaDub/outlier/pull/145

**Pipeline Run (2026-09-01)**:
- Canonical sync passed: `REPORT STATUS: OK`, `RUN-NONCE: 266ff636751b4850`, head `9fb67a9`.
- `packs/2026-09-01` **published** (`EXIT=0`, profile `local`, leagues `MLB`,
  45 candidate rows, 822 decisions, `overall: PARTIAL` because no reasoning
  layer ran and WNBA is out of scope today).
- Candidate market mix is whitelist-clean: SO 26, GAMELINE 17, TEAM_PROP 2.
  Board A=1 actionable, A_FLAGGED=34, B=10.
- Paid reasoning / the AI Research Desk were **not** invoked at any point.

**Root cause of the 2026-08-31/09-01 failures**:
`a7b2aad` removed `write_json(props _latest.json)` in favour of
`save_extraction(...)`, but the matching read interception was never written --
nothing in the tree calls `load_extraction`. `props_latest.json` therefore froze
on the previous slate while `props_export_status_latest.json` kept reporting
`normalized_latest` as if it had been written. Feed health ("stale by 21.63h"),
line movement, and projections (slate-date mismatch -> 0 records -> DAG exit 1
-> no pack) were all downstream of that one dead write.

**Fixed on the branch** (1347 offline tests pass, `mypy` clean):
1. `props.py` -- writes `_latest.json` again via `safe_write_text`.
2. `refresh_plan.py` -- `projections` now `depends_on=("props", "probable_pitchers")`;
   it previously declared no dependencies and raced its own producers.
3. `projections.py` -- a current feed with no games on the target date is
   `skipped`, not `error`; a *stale* feed is still an error.
4. `database.py` -- Postgres fallback is a shared WAL file
   (`data/outlier-pipeline.sqlite3`, `OUTLIER_SQLITE_FALLBACK_PATH`) instead of
   `sqlite:///:memory:`, which was private per connection.
5. `feedback_recovery.py` -- salvage resumes past damaged pages (253 -> 148,470
   rows on the real ledger) and no longer emits orphaned decisions.
6. `desk_snapshot.py` -- an abandoned `.daily_job_lock` is reclaimed, in place
   when the filesystem refuses `rmdir`.

**Ledger**: `calibration/feedback.sqlite3` rebuilt -- 148,470 market snapshots,
148,226 decisions, 24,047 settlements, `quick_check ok`, identity invariant
satisfied. Corrupt original kept at `calibration/feedback.corrupt-2026-09-01.sqlite3`.
`pack_snapshot_memberships` was unrecoverable (b-tree root and both indexes
destroyed); it repopulates from future pack captures.

**Next Steps**:
- **Merge PR #145 promptly.** The fixes are also applied *uncommitted* in the
  canonical working tree so today's run could proceed; the next
  `report-sync.ps1 -SyncAllWorktrees` will materialise `outlier_scrapers/` from
  `origin/master` and silently revert them.
- Decide on WNBA: `check_freshness` fails the whole pipeline when any league is
  unsafe, and an out-of-season league is permanently unsafe (0 games, stale
  games/insights/injuries, coverage 85.71 < 90). Today was worked around with
  `--leagues MLB`. Either teach the gate about an empty slate or drive league
  selection from the schedule. This is a safety gate -- do not weaken it casually.
- Decide whether `calibration/feedback.sqlite3` should move off this checkout.
  The path sits behind a cloud-sync/AV filter (PINNED attribute, `.fuse_hidden*`
  files, an undeletable `packs/.daily_job_lock`), which is the likely cause of
  the repeated corruption (Aug 22, Aug 25, Aug 31). Moving it means changing the
  hardcoded `feedback_db.py:DEFAULT_DB_PATH` and five other call sites.
- Pre-existing `ruff` errors on master, untouched here: `refresh_plan.py:174`
  (E741) and `tests/test_totals_model.py:15` (F401).
