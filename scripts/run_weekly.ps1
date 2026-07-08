# Weekly automated run (Phase 7 of the build spec): OSHA + five permit feeds,
# scored and exported to a dated xlsx in output/. No CRM refresh here by
# design -- HubSpot has no live API in v1, so crm-load stays a manual step
# (run `python -m src.main crm-load --file <export>` whenever you have a
# fresh one; the scoring/export steps below just use whatever's already
# loaded, and are a no-op on the CRM side if nothing's loaded yet).
#
# Registered via Windows Task Scheduler -- see README.md "Scheduling
# (Phase 7)" for the exact schtasks command. Not registered automatically;
# this script does nothing until something runs it.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot "venv\Scripts\python.exe"
$LogFile = Join-Path $RepoRoot "output\run_weekly.log"

# 10-day lookback on a weekly cadence -- a few days of overlap buffer past the
# strict 7-day gap, since upsert is idempotent (re-fetching overlapping
# records just updates them, never duplicates) and permit feeds occasionally
# lag a day or two before a record appears.
$Since = (Get-Date).AddDays(-10).ToString("yyyy-MM-dd")

New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "output") | Out-Null

"[$(Get-Date -Format o)] Starting weekly run (since=$Since)" | Out-File -FilePath $LogFile -Append -Encoding utf8

try {
    & $PythonExe -m src.main run-all --since $Since | Out-File -FilePath $LogFile -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw "run-all exited with code $LASTEXITCODE"
    }
    "[$(Get-Date -Format o)] Completed successfully" | Out-File -FilePath $LogFile -Append -Encoding utf8
} catch {
    "[$(Get-Date -Format o)] FAILED: $_" | Out-File -FilePath $LogFile -Append -Encoding utf8
    throw
}
