# Project Smart Layer by Azam Puri - daily run (scheduled by install_windows.ps1 as "Smart Layer Daily").
# Refreshes the local copy from the ERP, pushes yesterday's audit and items that ran out of stock, and saves
# reports as CSV in the output folder. Fridays: stock check, reorder list, contract closing, pilferage.
# 10th of the month: full audit of last month. Log: output\daily_run_log.txt

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = if (Test-Path "python_path.txt") { (Get-Content "python_path.txt" -TotalCount 1).Trim() } else { "python" }
New-Item -ItemType Directory -Force -Path output | Out-Null
$log = "output\daily_run_log.txt"
function Run([string[]]$cmdArgs) {
    "--- $(Get-Date -Format 'HH:mm:ss') smart_layer $($cmdArgs -join ' ')" | Out-File $log -Append -Encoding utf8
    & $py -m smart_layer @cmdArgs 2>&1 | Out-File $log -Append -Encoding utf8
    return $LASTEXITCODE
}
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ====" | Out-File $log -Append -Encoding utf8

if ((Run @("sync")) -ne 0) {
    "sync failed - reports skipped" | Out-File $log -Append -Encoding utf8
    exit 1
}
Run @("audit", "--csv", "--push") | Out-Null
Run @("report", "zero", "--push") | Out-Null
Run @("report", "production", "--csv") | Out-Null
if ((Get-Date).DayOfWeek -eq "Friday") {
    foreach ($r in "stock", "reorder", "closing", "pilferage") { Run @("report", $r, "--csv") | Out-Null }
}
if ((Get-Date).Day -eq 10) {
    Run @("audit", "--month", (Get-Date).AddMonths(-1).ToString("yyyy-MM"), "--csv", "--push") | Out-Null
}
# keep the log from growing for ever
if ((Get-Item $log).Length -gt 5MB) { Get-Content $log -Tail 5000 | Set-Content $log -Encoding utf8 }
