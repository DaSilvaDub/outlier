# Shared gates; dot-sourcing performs no pipeline or publication work.
function Assert-PublicationHold([string]$RepoRoot) {
    foreach ($path in @((Join-Path $RepoRoot "docs\remediation\publication_hold.json"), "C:\Users\dasil\outlier-quarantine\2026-11-05\publication_hold.json")) {
        if (Test-Path -LiteralPath $path) {
            $hold = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
            if ($null -eq $hold.active -or $hold.active -isnot [bool]) { throw "Invalid publication hold: $path" }
            if ($hold.active) { throw "Publication hold active: $path. Independent revalidation required." }
        }
    }
}
function Get-EasternSlateDate {
    $tz = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    return [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz).ToString("yyyy-MM-dd")
}
function Assert-SlateDate([string]$Date) {
    $parsed = [DateTime]::MinValue
    if (-not [DateTime]::TryParseExact($Date, "yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsed)) { throw "Invalid slate date: $Date" }
}
function Assert-RequiredFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required file missing: $Path" }
}
function Assert-FreeSpace([string]$Path, [double]$MinGB = 20.0) {
    $drive = (Get-Item -LiteralPath $Path).PSDrive
    if ($null -eq $drive.Free -or $drive.Free -le $MinGB * 1GB) { throw "Insufficient disk space: more than $MinGB GB required." }
}
function Invoke-CheckedStage {
    param([string]$Executable, [string[]]$Arguments)
    $null = Get-Command $Executable -ErrorAction Stop
    $global:LASTEXITCODE = $null
    # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecords.
    # Collect it without letting harmless progress/warnings terminate a good stage;
    # restore Stop before applying the exit-code and fatal-diagnostic gates.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $Executable @Arguments 2>&1)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    $output | ForEach-Object { Write-Output $_ }
    if ($code -ne 0) { throw "Stage failed with exit code ${code}: $Executable $($Arguments -join ' ')" }
    $text = $output -join "`n"
    if ($text -match '(?i)(HTTP(?:Error| status)?[ :]*403|\b403\s+Forbidden|ENOSPC|Errno\s*28|WinError\s*(?:112|39)|no space left|not enough space|database.*locked|slate.date.mismatch|0\s+matches|zero\s+matches)') {
        throw "Stage reported a disqualifying source/storage/slate failure."
    }
}
function Assert-DatedCsv([string]$Path, [string]$Date, [DateTime]$NotBefore) {
    Assert-RequiredFile $Path
    if ((Get-Item -LiteralPath $Path).LastWriteTimeUtc -lt $NotBefore) { throw "Stale output: $Path" }
    $rows = @(Import-Csv -LiteralPath $Path)
    if ($rows.Count -eq 0) { throw "Zero matching rows: $Path" }
    foreach ($row in $rows) {
        if (-not $row.PSObject.Properties['slate_date'] -or $row.slate_date -ne $Date) { throw "Slate-date mismatch or missing slate_date in $Path" }
    }
}
