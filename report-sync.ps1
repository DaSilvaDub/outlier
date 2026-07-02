# BLessed one-liner wrapper.
# Every ent, every session, every status or "searched branches" question:
#    & "C:\Users\dasil\OneDrive\Documents\outlier\report-sync.ps1"
#
# This does the full canonical verify (bootstrap + SyncAll + complete report).
# NEVER pipe this to Select-String, Out-String + Select, grep, head, etc.
# Paste the entire output.

& "C:\Users\dasil\OneDrive\Documents\outlier\scripts\verify-sync.ps1" $args
exit $LASTEXITCODE
