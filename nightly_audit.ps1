$ErrorActionPreference = "Stop"

# Navigate to the outlier workspace
Set-Location -Path "C:\Users\dasil\Dev\GitHub\outlier"

# Run the nightly calibration audit skill using the Antigravity CLI
# This will spawn a headless agent session to run the skill and perform the checks
Write-Output "Starting Nightly Calibration Audit via AGY..."
agy run --skill nightly-calibration-audit "Run the nightly calibration audit to check for math bugs and validate game results"
Write-Output "Nightly Calibration Audit completed."
