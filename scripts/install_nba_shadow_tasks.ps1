$ErrorActionPreference = "Stop"

# Registers the NBA shadow tasks (re-run safely; -Force replaces them). Times are local.
#   Outlier NBA Capture : 10:00 daily, every 15 min for 15 h (last tick ~00:45, so tips
#                         ~10:30-01:15 local get a verified close; the machine runs ET)
#   Outlier NBA Settle  : 06:00 daily, catches up on wake if the PC was asleep
# Tasks run only while you are logged on and do not wake the PC; a sleeping evening
# misses that night's closes. Points at the canonical checkout (Outlier-Quad-Sync keeps
# it current), so install after these scripts have merged to master.

$scripts = "C:\Users\dasil\Dev\GitHub\outlier\scripts"
$launcher = Join-Path $scripts "run_nba_shadow_hidden.vbs"

function Register-NbaTask {
    param([string]$Name, [string]$Mode, $Trigger, [string]$Description)
    $action = New-ScheduledTaskAction -Execute "wscript.exe" `
        -Argument "`"$launcher`" $Mode" -WorkingDirectory $scripts
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings $settings -Description $Description -Force | Out-Null
    Write-Output "Registered: $Name"
}

$capture = New-ScheduledTaskTrigger -Daily -At "10:00"
$repeat = New-ScheduledTaskTrigger -Once -At "10:00" `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 15)
$capture.Repetition = $repeat.Repetition

Register-NbaTask -Name "Outlier NBA Capture" -Mode "capture" -Trigger $capture `
    -Description "NBA shadow capture; skips unless a tip within 60 min needs a verified close."
Register-NbaTask -Name "Outlier NBA Settle" -Mode "settle" `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "06:00") `
    -Description "NBA settlement against box scores (3-day lookback)."
