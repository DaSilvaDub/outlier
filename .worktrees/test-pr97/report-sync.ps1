# BLessed one-liner wrapper.
# Every ent, every session, every status or "searched branches" question:
#    & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
#
# This does the full canonical verify (bootstrap + SyncAll + complete report).
# NEVER pipe this to Select-String, Out-String + Select, grep, head, etc.
# Paste the entire output.

# @args (splat), not $args: the latter passes the whole array as ONE positional
# argument. That was harmless while verify-sync.ps1 took no parameters, but it now
# accepts -RepoRoot, so `report-sync.ps1 -RepoRoot X` would fail to bind.
& "C:\Users\dasil\Dev\GitHub\outlier\scripts\verify-sync.ps1" @args
exit $LASTEXITCODE
