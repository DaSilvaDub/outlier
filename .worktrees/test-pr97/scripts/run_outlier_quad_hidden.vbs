Set objShell = CreateObject("WScript.Shell")
' Scheduled path: skip agent worktree sweep (mirrors + GitHub still sync every run).
' Full sweep (incl. worktrees): powershell -File C:\Users\dasil\Scripts\sync_outlier_quad.ps1
'
' waitOnReturn = True so the PowerShell exit code reaches Task Scheduler.
' It was False until 2026-07-25, which is why a four-day sync outage still
' reported "last run result 0" on every single run. Window stays hidden (0).
rc = objShell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\Users\dasil\Scripts\sync_outlier_quad.ps1"" -SkipWorktrees", 0, True)
WScript.Quit rc
