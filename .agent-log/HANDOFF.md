# Handoff Summary

**Branch / PR**: `feat/playable-props-export-and-audit-skill` -> https://github.com/DaSilvaDub/outlier/pull/149
- **Last Commit SHA**: `9cff252` (`feat(skills): add Playable Props export and Post-Game Slate Accuracy Audit to export-manual-outlier-packs`)
- **Files Touched**: `.agents/skills/export-manual-outlier-packs/SKILL.md`
- **Session Work**:
  1. Ran the complete local daily pipeline for 2026-09-04 with paid reasoning strictly OFF (`EXIT=0`, 29 candidates, 770 decisions, `PARTIAL`).
  2. Synthesized and organized candidate prompts and datasets to Desktop and Google Drive.
  3. Extracted consolidated `playable_props.md` and `playable_props.csv` directly into `today` export folders.
  4. Executed live/post-game accuracy audit querying official MLB Stats API boxscores (`hydrate=boxscore,linescore`): 4/8 (50.0%) Strikeouts hit, with Board `A_FLAGGED` going 2/3 (66.7%) and hitting top edge dog Andre Pallante U2.5 @ +134.
  5. Codified both workflows into `export-manual-outlier-packs` skill via PR #149.
- **Next Steps**: Merge PR #149 to master; run next daily pipeline tomorrow morning for the 2026-09-05 slate.

**Branch / PR**: `claude/fix-this-bd82a2` -> https://github.com/DaSilvaDub/outlier/pull/145

**Canonical Rerun Attempt (2026-09-01, head `18dc909`)**:
- Canonical sync passed after preserving the pre-existing blend timestamp:
  `REPORT STATUS: OK`, `RUN-NONCE: 693baeadbbbd4a7f`.
- Ran the local MLB/WNBA pipeline with file-backed extraction storage and with
  result collection / feedback maintenance skipped. Paid reasoning stayed off.
- Refresh exported MLB games 6,794, props 474, insights 3,210, projections 472,
  line movement 58/29 markets, game line movement 270, probable pitchers 15,
  cards 29, and game cards 898. WNBA props exported 516; its empty-slate
  projections were correctly skipped under the newly merged gate fix.
- The run exited 1 before pack build. Unified feed health rejected MLB because
  insights age was 6.338h (>6h), and rejected WNBA for stale inputs and 85.71%
  coverage. The existing 45-row `PARTIAL` pack predates this attempt and was not
  overwritten or attributed to it.
- Removed the empty stale `packs/.daily_job_lock` left by the abort and restored
  the pre-rerun `calibration/blend_weights.json` timestamp exactly.

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
