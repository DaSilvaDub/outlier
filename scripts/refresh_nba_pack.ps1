<# Refresh NBA exports only after every producer and validation gate passes. #>
[CmdletBinding()]
param(
    [string]$Date = "",
    [switch]$SkipPipeline,
    [switch]$SkipDrive,
    [string]$ScratchOut = "",
    [switch]$ForceDrive,
    [switch]$SkipInjuryFilter,
    [double]$MinDiskSpaceGB = 20.0,
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$NbaPipelineRepo = "C:\Users\dasil\Dev\GitHub\nba-props-pipeline",
    [string]$PythonExe = "python",
    [string]$DriveRoot = "",
    [string]$RegistryPath = ""
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "publication_guards.ps1")
if (-not $Date) { $Date = Get-EasternSlateDate }
Assert-SlateDate $Date
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
Assert-PublicationHold $RepoRoot
Assert-FreeSpace $RepoRoot $MinDiskSpaceGB
$started = [DateTime]::UtcNow
$stageDir = Join-Path ([IO.Path]::GetTempPath()) ("outlier-nba-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stageDir | Out-Null
try {
    Push-Location -LiteralPath $RepoRoot
    try {
        if (-not $SkipPipeline) {
            $lineups = Join-Path $NbaPipelineRepo "scripts\refresh_lineups_injuries.ps1"
            Assert-RequiredFile $lineups
            Invoke-CheckedStage "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $lineups, "-Date", $Date, "-Wait")
            foreach ($relative in @("pipeline\nba_data_watcher.py", "pipeline\generate_prop_edges.py", "pipeline\generate_playbook_grades.py")) {
                $script = Join-Path $NbaPipelineRepo $relative
                Assert-RequiredFile $script
                $stageArgs = @($script)
                if ($relative -like "*nba_data_watcher.py") { $stageArgs += "--once" }
                Push-Location -LiteralPath $NbaPipelineRepo
                try { Invoke-CheckedStage $PythonExe $stageArgs } finally { Pop-Location }
            }
        }
        $export = Join-Path $RepoRoot "scripts\export_nba_extra_pack.py"
        Assert-RequiredFile $export
        $stagedCsv = Join-Path $stageDir "nba_only.csv"
        Invoke-CheckedStage $PythonExe @($export, "--date", $Date, "--out", $stagedCsv)
        Assert-DatedCsv $stagedCsv $Date $started
        if (-not $SkipInjuryFilter) {
            $filter = Join-Path $RepoRoot "scripts\filter_nba_ruled_out.py"
            Assert-RequiredFile $filter
            if (-not $RegistryPath) {
                $refreshRoster = Join-Path $RepoRoot "scripts\refresh_nba_roster_registry.py"
                Assert-RequiredFile $refreshRoster
                $RegistryPath = Join-Path $RepoRoot "resources\nba_roster_registry.json"
                Invoke-CheckedStage $PythonExe @($refreshRoster, "--date", $Date, "--out", $RegistryPath)
            }
            Assert-RequiredFile $RegistryPath
            $receiptDir = Join-Path $RepoRoot "resources\nba_filter_receipts"
            New-Item -ItemType Directory -Force -Path $receiptDir | Out-Null
            $filterReceipt = Join-Path $receiptDir ("$Date-" + [Guid]::NewGuid().ToString("N") + ".json")
            Invoke-CheckedStage $PythonExe @($filter, "--csv", $stagedCsv, "--out", $stagedCsv, "--date", $Date, "--registry", $RegistryPath, "--summary-json", $filterReceipt)
            Assert-DatedCsv $stagedCsv $Date $started
        }
        $shouldPublish = -not $SkipDrive -and (-not $ScratchOut -or $ForceDrive)
        if ($shouldPublish -and $SkipInjuryFilter) { throw "Publication requires the injury filter." }
        if ($shouldPublish) {
            if (-not $DriveRoot) {
                foreach ($candidate in @("G:\My Drive", "C:\Users\dasil\My Drive (dasilvadub@gmail.com)")) {
                    if (Test-Path -LiteralPath $candidate) { $DriveRoot = $candidate; break }
                }
            }
            if (-not $DriveRoot -or -not (Test-Path -LiteralPath $DriveRoot)) { throw "Google Drive root not found." }
            $organizer = Join-Path $RepoRoot "scripts\organize_desktop_today.py"
            Assert-RequiredFile $organizer
            Invoke-CheckedStage $PythonExe @($organizer, "--sport", "NBA", "--date", $Date)
        }
        # No publish target is touched before generation, filtering and organizing succeed.
        if ($ScratchOut) {
            $parent = Split-Path -Parent $ScratchOut
            if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -LiteralPath $stagedCsv -Destination $ScratchOut -Force
        }
        if ($shouldPublish) {
            foreach ($target in @((Join-Path $DriveRoot "today\extra_packs_${Date}_latest"), (Join-Path $DriveRoot "today\extra_packs"), (Join-Path $DriveRoot "BETTING REPORTS\NBA"))) {
                New-Item -ItemType Directory -Force -Path $target | Out-Null
                Copy-Item -LiteralPath $stagedCsv -Destination (Join-Path $target "nba_only.csv") -Force
            }
        }
        Write-Output "refresh_nba_pack completed: date=$Date published=$shouldPublish"
    } finally { Pop-Location }
} finally {
    # This path was generated above, never supplied by callers.
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ([IO.Path]::GetFullPath((Split-Path -Parent $stageDir)).TrimEnd('\') -ne $tempRoot -or (Split-Path -Leaf $stageDir) -notmatch '^outlier-nba-[a-f0-9]{32}$') { throw "Unexpected stage path." }
    Remove-Item -LiteralPath $stageDir -Recurse -Force
}
