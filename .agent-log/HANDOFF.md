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
