' Task Scheduler launcher for run_nba_shadow.ps1 with no console flash (the capture
' task ticks every 15 minutes). Usage: wscript.exe run_nba_shadow_hidden.vbs capture|settle
' waitOnReturn = True so the PowerShell exit code reaches Task Scheduler.
Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
script = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "run_nba_shadow.ps1")
rc = objShell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """ -Mode " & WScript.Arguments(0), 0, True)
WScript.Quit rc
