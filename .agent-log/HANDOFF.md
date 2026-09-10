# Handoff Summary

**Automated Debug & Code-Health Review (2026-09-10)**:
- **Branch**: `claude/inspiring-fermat-b1yfk9` (was 5 commits ahead of `master` at `adb7a26`
  with no open PR; this session adds one commit and opens that PR).
- **Files Touched**: `outlier_scrapers/pack_publish.py`, `tests/test_pack.py`
- **Session Work**:
  1. STEP 0 canonical `report-sync.ps1` could NOT run: unattended Linux cloud session,
     no `pwsh` and no access to the Windows checkout, so the `.claude` PowerShell hooks
     were inert. Review is against this clone's `claude/inspiring-fermat-b1yfk9`; branch /
     worktree conclusions still need a canonical sync run. No reasoning models, desk
     runs or provider calls were invoked at any point (offline throughout).
  2. Removed a duplicated enforce-mode gate in `pack_publish.write_pack`. The function
     carried the immutable-pack + 14-day shadow-window checks **twice** (L197-243 and
     L408-447) against the same policy and the same db. The second copy was the weaker
     of the two — `except sqlite3.OperationalError: pass` swallowed a missing
     `market_snapshots` table and fell through, where the live gate refuses on any
     query failure — and it was unreachable, since the live gate's shadow-day count is
     a subset (it joins `decisions.portfolio_mode = 'shadow'` when that column exists),
     so it raises wherever the copy would have. Deleted rather than aligned: two copies
     of one risk gate is how the weaker one ends up being the one that runs. Behaviour
     is unchanged. NB an earlier commit on this branch (`1e2275b`) reported this as a
     live fail-open; that was wrong — I had read only the second copy. CI caught it.
  2b. Added `test_enforce_refused_when_feedback_db_has_no_market_snapshots_table`: an
     existing-but-empty feedback db must fail closed like a missing file. Nothing
     pinned that shape before, which is why the two copies could disagree unnoticed.
  3. Reviewed the five commits this branch carries over `master` (IL-return SO
     disqualification, MLB `GAME_PROP` NRFI/YRFI pruning, `_scope_token` full-game fold,
     `_to_float` unit hardening). No defects found; `_event_starts_at` is populated
     before `pitcher_returning_from_il` reads it, and `alt_player_props`' `.lower()`
     scope compare is safe because normalized `scope` is always `detect_scope` output.
- **Environment limits**: PyPI egress is blocked by this sandbox's policy (403 direct and
  via proxy), so `sqlalchemy`, `openai`, `anthropic` and `google-genai` are uninstallable.
  29 test modules do not collect; the rest ran 721 passed / 65 failed / 41 skipped, with
  every failure a missing-SDK import error and the count unchanged from baseline.
  `tests/test_pack.py` needs `sqlalchemy`, so the new regression test could not execute
  here — the patched gate was verified in isolation across all five db states (missing
  file / no table / empty table / 13 days / 14 days). CI must confirm it.
- **Next Steps**: Merge this branch's PR, then PR #152 (it pins `mypy>=2.3.1`, which
  clears the 3 local mypy narrowing false positives still reported here). Consider the
  two fail-open reads flagged in the report but not changed: `pack.py:626`
  (`except Exception: pass` around the enforce-sidecar read) and
  `portfolio_report.py:79` (a day counted valid before partial aggregation can throw).

**Calibration audit review fixes (2026-09-08)**:
- **Branch**: `fix/calibration-audit-review` (follow-up to merged PR #156).
- **Files Touched**: `outlier_scrapers/pack_selection.py`, `outlier_scrapers/slate_quality.py`, `tests/test_calibration_upgrades.py`, `calibration/stake_calibration.json`
- **Session Work**:
  1. STEP 0: `REPORT STATUS: OK`, `RUN-NONCE: 3c7d09a397e34a41`, head `acf0bfc`.
  2. PR #156 review: UNDER SO was also `A_FLAGGED` because `pitcher_returning_from_il` was stamped into `data_quality_flags` and `_finalize_actionable_row` requires `not dq_flags`.
  3. OVER-only DQ flag; UNDER gets `pitcher_rehab_context` on `signal_flags`. Dropped the post-sizing 25% `projection_mean` haircut.
  4. IL detector uses `ret YYYY-MM-DD` vs slate date (21-day window), rejects "has not been cleared", and no longer treats bare rehab/cleared/first time as a return.
  5. MLB `GAME_PROP` candidates now require NRFI/YRFI or first-inning RUNS (first-inning Hits dropped).
  6. Restored `calibration/stake_calibration.json` to the `market_consensus_prob` artifact. The 17,997-sample refit used `final_blended_prob` and live policy would reject it.
- **Verify**: `pytest tests/test_calibration_upgrades.py tests/test_pack.py tests/test_pack_integrity.py tests/test_predictor_gates.py tests/test_stake_calibration.py` → 195 passed; ruff clean.
- **Next Steps**: Open PR from `fix/calibration-audit-review`. Refit stake calibration with `--source-column market_consensus_prob` when the ledger is available.

**Calibration Audit & Upgrades (2026-09-08)**:
- **Last Commit SHA**: `103a07e`
- **Branch / PR**: `feat/calibration-audit-upgrades` -> https://github.com/DaSilvaDub/outlier/pull/156
- **Files Touched**: `outlier_scrapers/pack_selection.py`, `outlier_scrapers/slate_quality.py`, `tests/test_calibration_upgrades.py`, `calibration/blend_weights.json`, `calibration/stake_calibration.json`, `calibration/alerts/nightly_audit_status.json`
- **Session Work**:
  1. Ran STEP 0 canonical multi-ent verification: `REPORT STATUS: OK`, `RUN-NONCE: 342ec53e2f584285`.
  2. Executed `/calibrate-outlier for last night slate`:
     - Graded completed games and imported 275 settlements for the 2026-09-07 slate via `outlier_scrapers.results`.
     - Analyzed `play_vs_stand_down.csv`: historical PLAY ROI is +2.00% (+4.87 units, +0.0387 price CLV), while STAND_DOWN filter avoided -2,201.01 units in negative EV churn.
     - Refitted `fit-blend` (422 samples, 0.75 market consensus anchor) and `fit-stake-calibration` (17,997 samples).
     - Successfully replayed chronological portfolio (2026-07-01 to 2026-12-31) and regenerated reports in `calibration/reports/latest/`.
  3. Audited 2026-09-07 slate vs actual scores and player box scores:
     - All 185 pipeline decisions issued `STAND_DOWN`. The raw candidate pool went 8-8 (50%), so standing down protected bankroll capital against vig churn.
     - Identified root cause of Nick Pivetta K loss: pitcher returning from 60-day IL on strict pitch limit.
     - Identified stray MLB `GAME_PROP` (`CIN @ LAD Hits OVER 1.5`) in candidate pool.
  4. Implemented code recommendations on `feat/calibration-audit-upgrades`:
     - Added `slate_quality.pitcher_returning_from_il` to detect pitchers returning from IL/rehab. Disqualifies OVER strikeout recommendations (`actionable = false`, units cleared, 25% projection haircut).
     - Enforced strict exclusion of non-first-inning MLB `GAME_PROP` markets from `candidates.csv`.
     - Added `tests/test_calibration_upgrades.py` (5 unit tests passing; 169 total tests passing across suite).
     - Pushed branch and opened PR #156.
- **Next Steps**:
  - Review and merge PR #156 into `master`.
  - Next daily pipeline run will automatically benefit from IL return pitch limit protection and clean candidate markets.

**Daily Debug Review (2026-09-07)**:
- **Last Commit SHA**: `00c9cbb`
- **Branch / PR**: `claude/inspiring-fermat-mzn1j5` -> https://github.com/DaSilvaDub/outlier/pull/155
- **Files Touched**: `outlier_scrapers/game_totals.py`, `tests/test_game_totals.py`, `tests/test_daily_job.py`
- **Session Work**:
  1. Step 0 canonical sync could NOT run: this was an automated cloud session on Linux,
     and `report-sync.ps1` is invoked by CLAUDE.md at a Windows path
     (`C:\Users\dasil\Dev\GitHub\outlier\`). Review was read-only against a fresh clone
     of `master` at `adb7a26`. No reasoning models / desk runs were invoked (offline).
  2. Fixed a silent full-game drop in `period_identity()`: `detect_scope` returns
     `full_game` by default for unrecognised text, so the raw-`periodLabel` fallback also
     fired on full-game labels, and `FULL_GAME_SCOPES` (underscored) never matched the
     human-form `"Full Game"`. Since e7b1be5 gated candidates on `is_full_game_total`,
     such a feed would drop every full-game GAMELINE/total from `candidates.csv`
     with no flag. Labels and scopes now fold through one underscored vocabulary.
  3. Added a parametrized regression test over `Full Game` / `full game` / `full-game` /
     `FULL_GAME`; the 8I/6I/F5 fail-closed behaviour from #154 is unchanged.
  4. Added the file's own `_require_daily_job()` guard to 4 tests in `test_daily_job.py`
     that used the module without it (28 of 36 already did).
- **Environment Gap (needs attention)**: PyPI is blocked by this sandbox's egress policy
  (403), so `sqlalchemy`, `openai`, `anthropic` and `google-genai` could not be installed.
  29 test modules did not collect and 65 tests failed purely on those imports — all
  identical before and after the change. CI (which has the full lockfile) is the
  authority for those. Re-run the full suite locally before merging.

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
