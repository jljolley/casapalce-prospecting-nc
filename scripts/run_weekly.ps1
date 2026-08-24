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

# Cap log growth -- plain -Append with no bound would grow forever over
# years of weekly runs. At ~15 lines/run, 2000 lines is roughly 2.5 years of
# history; trim to the most recent half of that before each run rather than
# pulling in a full rotation mechanism for a single flat troubleshooting log.
$MaxLogLines = 2000
if (Test-Path $LogFile) {
    $existingLines = Get-Content $LogFile
    if ($existingLines.Count -gt $MaxLogLines) {
        $existingLines[-($MaxLogLines / 2)..-1] | Set-Content -Path $LogFile -Encoding utf8
    }
}

"[$(Get-Date -Format o)] Starting weekly run (since=$Since)" | Out-File -FilePath $LogFile -Append -Encoding utf8

# Capture stderr to its own file so a failure logs the REAL error, not just
# an exit code. This has to go through cmd.exe, not a native PowerShell
# redirect: PowerShell 5.1 wraps a native exe's stderr lines in
# ErrorRecord/NativeCommandError formatting noise even with a plain "2>" (not
# just "2>&1") -- cmd.exe's redirection doesn't do that, so the file ends up
# with the actual traceback text, not "python.exe : ...At line 1 char 1...".
# A prior version of this script had no stderr capture at all, so three real
# failures in August only ever logged "exited with code 1" with nothing to
# diagnose from.
$StderrFile = Join-Path $RepoRoot "output\run_weekly_stderr.tmp.log"
$cmdLine = "`"$PythonExe`" -m src.main run-all --since $Since 2> `"$StderrFile`""

try {
    cmd /c $cmdLine | Out-File -FilePath $LogFile -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        $errorText = if (Test-Path $StderrFile) { (Get-Content $StderrFile -Raw).Trim() } else { "" }
        throw "run-all exited with code ${LASTEXITCODE}:`n$errorText"
    }
    "[$(Get-Date -Format o)] Completed successfully" | Out-File -FilePath $LogFile -Append -Encoding utf8
} catch {
    "[$(Get-Date -Format o)] FAILED: $_" | Out-File -FilePath $LogFile -Append -Encoding utf8
    throw
} finally {
    Remove-Item -Path $StderrFile -ErrorAction SilentlyContinue
}
