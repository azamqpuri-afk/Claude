# Project Smart Layer by Azam Puri - live refresh (scheduled by install_windows.ps1 as "Smart Layer Live").
# Runs every 5 minutes: a quick pull of the fast-changing data (only the last few days, only the window-safe
# modules) so the dashboard stays current, then the urgent send-once alerts. The once-a-day "Smart Layer Daily"
# task does the deep reconcile (full history, ledgers, attendance, back-dated edits) and the daily summary.
# Log: output\live_run_log.txt

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = if (Test-Path "python_path.txt") { (Get-Content "python_path.txt" -TotalCount 1).Trim() } else { "python" }
New-Item -ItemType Directory -Force -Path output | Out-Null
$log = "output\live_run_log.txt"
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====" | Out-File $log -Append -Encoding utf8

& $py -m smart_layer licence check 2>&1 | Out-File $log -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { "no valid licence - live sync skipped" | Out-File $log -Append -Encoding utf8; exit 1 }
& $py -m smart_layer sync --live 2>&1 | Out-File $log -Append -Encoding utf8
$syncRc = $LASTEXITCODE
# urgent, send-once alerts only (loom silent, beam out with no backup, confirmed error, negative stock)
& $py -m smart_layer audit --push --urgent 2>&1 | Out-File $log -Append -Encoding utf8
$auditRc = $LASTEXITCODE
# always write a result line, success or failure, so a silent problem (e.g. the ERP login) is visible in the log
"---- done $(Get-Date -Format 'HH:mm:ss'): sync exit $syncRc, audit exit $auditRc ----" | Out-File $log -Append -Encoding utf8
& $py -m smart_layer logtrim $log --max-mb 5 --keep-mb 1 | Out-Null   # lock-safe trim (temp file + rename)
