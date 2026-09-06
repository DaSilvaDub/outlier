# Handoff Summary

**Automated Debug & Code-Health Review (2026-09-06)**:
- **Last Commit SHA**: `c32820a`
- **Branch / PR**: `claude/inspiring-fermat-3yokgm` -> https://github.com/DaSilvaDub/outlier/pull/152
- **Files Touched**: `scripts/organize_today_run2.py`, `scripts/diagnose_so_gamelog_calibration.py`, `outlier_scrapers/refresh_plan.py`, `tests/test_organize_today_run2.py`, `tests/test_totals_model.py`
- **Session Work**:
  1. Step 0 canonical `report-sync.ps1` could NOT run: this was an unattended Linux
     scheduled session with no `pwsh` and no access to the Windows checkout, so the
     `.claude` PowerShell hooks were inert. Findings below are from the freshly cloned
     tree at `81263f2` and should be re-confirmed against a canonical sync run.
  2. Found and fixed silent export data loss: `organize_today_run2.safe_copy` swallowed
     the final `OSError` with a bare `pass`, so a cloud-sync lock left the Desktop /
     Drive `today` folder short a file while the run still reported success. It now
     prints a WARNING to stderr and stays non-fatal. Added a regression test that was
     verified to fail pre-fix.
  3. Cleared the three pre-existing ruff errors previously logged here as untouched
     (E741 in `refresh_plan.execute_refresh`, two unused imports). Zero behaviour change.
  4. Reviewed the post-`753d470` fix set (feed-health empty-slate leniency, projections
     skip, feedback salvage, `clear_derived_pack_outputs`); the MLB whitelist,
     `DERIVED_PACK_OUTPUTS`/`verdicts` and desk-verdict invariants all hold in code.
  5. Fixed a race in `acquire_daily_lock`: breaking an abandoned lock was
     rmdir-then-mkdir, so a stale "abandoned" conclusion could delete a *live* lock and
     leave two daily jobs writing the same pack and ledger. Breaking is now an exclusive
     -create claim plus a re-check of lock identity/owner, and the directory is never
     removed to break it. Regression test confirmed to fail against the old path.
  6. Pinned `mypy>=2.3.1,<3` so local `make typecheck` and CI agree.
  7. Paid reasoning / the AI Research Desk were NOT invoked at any point.
- **Environment limits**: PyPI egress is blocked by this sandbox's policy, so
  `sqlalchemy`, `openai`, `anthropic` and `google-genai` were uninstallable. 29 test
  modules do not collect locally; the remaining subset ran 682 passed / 37 skipped with
  the failure count unchanged from baseline (all missing-SDK import errors). `ruff` is
  clean; local `mypy` 1.19.1 reports 3 narrowing false positives that CI's `mypy` 2.3.1
  does not (CI on `81263f2`: "Success: no issues found in 87 source files").
- **Next Steps**: Review and merge PR #152. Re-run `report-sync.ps1` on the canonical
  Windows checkout before trusting any branch/worktree conclusions from this session --
  that verification could not run here and is the one open item from this session.

**Outlier Skill Atlas (2026-09-06)**:
- **Last Content Commit SHA**: `0f37c7f`
- **Branch / PR**: `feat/skill-atlas` -> https://github.com/DaSilvaDub/outlier/pull/151
- **Files Touched**: `docs/skill-catalog/README.md`, `docs/skill-catalog/outlier-skill-atlas.html`, `docs/skill-catalog/skills-inventory.csv`, `docs/skill-catalog/inventory-metadata.json`
- **Session Work**:
  1. Ran the mandatory canonical sync verifier: `REPORT STATUS: OK`, `RUN-NONCE: d7baf901114d4aaa`, head `b2e8731`.
  2. Inventoried all configured Codex, agent, system, plugin, runtime, and Outlier skill roots: 982 physical `SKILL.md` packages and 979 unique callable skill names.
  3. Created a self-contained interactive HTML catalog explaining every skill, its Outlier fit, concrete pipeline use, caution, category, source, and exact path.
  4. Added a full CSV inventory plus machine-readable coverage metadata; completeness checks found 979 unique rows, no empty descriptions, no empty Outlier uses, and no omitted current skill files.
  5. Built and bundled the React artifact, rendered it in the in-app browser, and verified search/filter behavior plus corrected YAML-description parsing.
- **Next Steps**: Review and merge PR #151. Regenerate the catalog when skill installations change.

**Pipeline Run (2026-09-05)**:
- **Last Commit SHA**: `34136fd`
- **Files Touched**: `.agent-log/HANDOFF.md`
- **Session Work**:
  1. Ran Step 0 canonical multi-ent verification: `REPORT STATUS: OK`, `RUN-NONCE: 009ebcc35873432f`.
  2. Executed local daily pipeline: `python -m outlier_scrapers.daily_job --analysis-profile local` with paid reasoning strictly OFF (`exit=0`, `profile=local`, `overall=PARTIAL`).
  3. Automatic result collection: 300 settlements updated, 1,018 CLV records corrected during feedback maintenance.
  4. Captured 795 feedback snapshots and 795 decisions in `calibration/feedback.sqlite3`.
  5. Published pack `packs/2026-09-05` with 33 candidate rows and written `manifest.json`.
  6. Executed `python scripts/organize_today_run2.py` to organize replace-exports and generated 4 Master Prompts to `C:\Users\dasil\OneDrive\Desktop\today` and `G:\My Drive\today`.
- **Next Steps**: Awaiting user instruction or prompt review.

**Branch / PR**: `chore/nightly-calibration-2026-09-05` -> https://github.com/DaSilvaDub/outlier/pull/150
- **Last Commit SHA**: `558ddb4` (`chore(calibration): nightly model recalibration for 2026-09-05`)
- **Files Touched**: `calibration/alerts/nightly_audit_status.json`, `calibration/blend_weights.json`, `calibration/stake_calibration.json`
- **Session Work**:
  1. Ran Step 0 canonical multi-ent verification: `REPORT STATUS: OK`, `RUN-NONCE: cd6eedb445d04908`.
  2. Executed `python -m outlier_scrapers.feedback report`: verified math metrics across 24,782 settlements / 150,581 decisions.
     - PLAY class returned +4.87u (+2.00% ROI, +3.87% CLV).
     - Stand-down filter successfully avoided -2,074.36u in negative EV churn.
     - Whitelisted Pitcher Strikeouts (SO) delivered +72.68% ROI (+7.99u).
  3. Re-tuned probability blend weights via `fit-blend`: Holdout Brier score improved from 0.2533 to 0.2463 (327 eligible samples, status `active`).
  4. Updated stake shrinkage calibration via `fit-stake-calibration`: 17,605 eligible samples fitted, shrunk reliability factor 1.00586 (status `active`).
  5. Verified portfolio replay via `replay-portfolio` across 2026-07-01 to 2026-12-31 without constraint breaches.
  6. Verified 80 offline tests in `test_stake_calibration.py`, `test_feedback.py`, and `test_feedback_decomposition.py` pass cleanly.
  7. Opened PR #150 on branch `chore/nightly-calibration-2026-09-05`.
- **Next Steps**: Merge PR #150 into master; merge PR #149 into master.

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
