# Session Handoff: Daily Debug Review (2026-11-03, agent `claude`)

1. **Work**: Automated daily debug review. Found and fixed a settlement
   correctness regression introduced by PR #318: `results._grade_row` now
   matches `ML_3WAY` / `Moneyline Three Way` / `Total 3-Way` selections, but
   graded them through the two-way branches, so a draw (and a three-way total
   landing on the number) came back `PUSH` instead of `L`. Because
   `calibration.py` keeps only W/L rows for the hit rate and the reliability
   fit, and `feedback_reporting.py` counts PUSH as neither a win nor a loss,
   every three-way draw was dropped out of the performance record instead of
   being booked as a loss — overstating win rate/ROI and biasing the
   calibration fit. Fix detects the three-way label on the matched market text
   and downgrades only that PUSH to L; two-way moneylines and totals still
   push. Corrected one existing test assertion that encoded the bug and added
   two regression tests.
2. **Last Commit SHA**: `f9527fa` (plus this handoff commit).
3. **Files Touched**: `outlier_scrapers/results.py`, `tests/test_results.py`,
   `.agent-log/HANDOFF.md`.
4. **PR Link**: https://github.com/DaSilvaDub/outlier/pull/326
5. **Verification**: `pytest tests/test_results.py` 25 passed. Full runnable
   suite before vs. after: 2236 → 2238 passed with an identical failed/errored
   set (71 failed / 34 collection errors), all caused by missing
   sqlalchemy/numpy/google-genai/dateutil in the sandbox. `ruff check` on both
   touched files: pre-existing findings only. `pyright outlier_scrapers
   outlier_nfl`: 1 error, pre-existing and sandbox-only (`genai`). No reasoning
   / desk models invoked.
6. **Next Steps / open items**:
   - **Backfill question for the user (needs a human decision):** three-way
     rows already settled as PUSH in `feedback.sqlite3` stay wrong until
     re-graded. The ledger is not in this clone, so the blast radius is
     unknown. Decide whether to re-grade historical `ML_3WAY` /
     `*_3WAY` settlements. Do not do this from an agent session (calibration
     lock).
   - **Latent, not fixed (ambiguous, needs a ruling):** `_grade_row` fails
     *open* — it grades any selection whose text matches, with no scope check.
     Two consequences, both reproducible on `master`:
     `"ATL @ WAS WSH Team Total OVER 87.5"` is graded off the **game** total
     (178) because the `^<team> Team Total` and `<team> - Points` patterns both
     miss the matchup prefix and the bare `TOTAL` alternative then matches
     inside `Team Total`; and `"ATL @ WAS 1st Half Total OVER 90.5"` /
     `"1H Spread HOME -2"` are graded off the **full-game** result. Period and
     prefixed-team-total rows should not reach the ledger (generation is
     full-game only), so this is latent today, but the grader would mis-settle
     them rather than skip them. Recommend an explicit period/scope reject and
     anchoring the game-total pattern so it cannot match inside `Team Total`.
   - **Sandbox cannot run the test suite as CI does.** PyPI egress is blocked
     *and* `pip`/`uv` cannot resolve it, so `structlog` (52 test modules),
     `sqlalchemy`, `numpy`, `google-genai` and `dateutil` are unavailable. To
     get any coverage this session ran pytest from the uv wheel cache with a
     hand-written local `structlog` logging shim kept **outside** the repo
     (`PYTHONPATH` only, never committed), and compared before/after runs so
     the shim could not flatter the result. Worth noting in
     `docs/CLOUD-SANDBOX-LIMITATIONS.md` that the documented
     `--continue-on-collection-errors` workaround yields **zero** passing
     tests without structlog.
   - Still open from previous runs (unchanged): `.worktrees/test-pr97/` is 364
     tracked files duplicating the whole repo, and its `outlier_scrapers/pack.py`
     diverges from the real one — ruff lints it as live code. Recommend
     `git rm -r --cached .worktrees` plus a `.gitignore` entry. No
     `.gitattributes`, so 2 of 377 tracked Python files are CRLF.

---

# Session Handoff: Daily Debug Review (2026-11-02, agent `claude`)

1. **Work**: Automated daily debug review. Fixed the silent-skip hole in the
   #316 unmounted-drive export filter (`scripts/organize_today_run2.py`,
   `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`):
   dropped export destinations are now named on stderr, and an export with no
   reachable destination left says so instead of succeeding as a no-op. Added
   six regression tests.
2. **Branch**: `claude/inspiring-fermat-1c1vbd` (PR opened to `master`).
3. **Next Steps / open items** (not changed by this session):
   - PR #323 (off-season guards) is still **open and unmerged**; all checks are
     green. The guards and `scripts/manage_mlb_wnba_tasks.ps1` are therefore not
     on `master` yet, so the 08:00 scheduled pipeline has no off-season fast-exit.
     Merge #323 to make the previous handoff's claims true.
   - The off-season window in #323 is November-March inclusive; MLB Opening Day
     falls in late March, so re-enable (or pass `-Force`) before then.
   - `.worktrees/test-pr97/` is 364 tracked files duplicating the whole repo
     (added accidentally in merge 5c63d66, 2026-10-06) and its copy of
     `outlier_scrapers/pack.py` already diverges from the real one. Recommend
     `git rm -r --cached .worktrees` plus a `.worktrees/` entry in `.gitignore`.
   - No `.gitattributes`: 2 of 377 tracked Python files are CRLF (the two #316
     touched), which rewrote both whole files in that commit's diff.

---

# Session Handoff: MLB & WNBA Pipeline Off-Season Shutdown

1. **Last Commit SHA**: `aa07b06` (PR #323)
2. **Files Touched**:
   - `scripts/run_daily_pipeline.ps1`: Added off-season guard (November - March) to skip execution cleanly and write `status: ok` when leagues are inactive.
   - `scripts/run_nightly_postgame_audit.ps1`: Added off-season guard to skip postgame settlement and results extraction cleanly.
   - `scripts/sync_all_sports_to_drive.ps1`: Guarded MLB & WNBA sync so it only attempts export if today's pack exists, avoiding stale copy loops.
   - `scripts/manage_mlb_wnba_tasks.ps1`: Created management utility to easily check, disable, or re-enable scheduled tasks.
   - Windows Task Scheduler: Disabled `Outlier Afternoon Lineup Refresh`, `Outlier T-30 Reprice Gate`, `Outlier_T30_Reprice`, and `Outlier Nightly Slate Audit`.
3. **PR Link**: https://github.com/DaSilvaDub/outlier/pull/323
4. **Next Steps**:
   - To disable the 8:00 AM `Outlier Daily Pipeline` task in Task Scheduler completely, run `Disable-ScheduledTask -TaskName "Outlier Daily Pipeline"` from an Administrator PowerShell prompt (it requires elevation due to its security descriptor; however, even if left scheduled, `run_daily_pipeline.ps1` in both canonical and `outlier-scheduled` will instantly detect off-season and exit cleanly in milliseconds).
   - In Spring (April), run `powershell -File scripts/manage_mlb_wnba_tasks.ps1 -Enable` to re-enable the daily pipeline tasks when the new MLB/WNBA seasons begin.
