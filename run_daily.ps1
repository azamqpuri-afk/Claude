# Project Smart Layer by Azam Puri - daily run for Windows Task Scheduler.
# Schedule it once a day (e.g. 8:00 AM) with "Start in" set to this folder.
# Refreshes the local copy from the ERP, pushes yesterday's audit and any items
# that ran out of stock, and saves the reports as CSV in the output folder.

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = "python"
$log = "output\daily_run_log.txt"
New-Item -ItemType Directory -Force -Path output | Out-Null
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File $log -Append -Encoding utf8

& $py -m smart_layer sync                         2>&1 | Out-File $log -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { "sync failed - reports skipped" | Out-File $log -Append -Encoding utf8; exit 1 }
& $py -m smart_layer audit --csv --push           2>&1 | Out-File $log -Append -Encoding utf8
& $py -m smart_layer report zero --push           2>&1 | Out-File $log -Append -Encoding utf8
& $py -m smart_layer report production --csv      2>&1 | Out-File $log -Append -Encoding utf8
if ((Get-Date).DayOfWeek -eq "Friday") {
    & $py -m smart_layer report stock --csv       2>&1 | Out-File $log -Append -Encoding utf8
    & $py -m smart_layer report reorder --csv     2>&1 | Out-File $log -Append -Encoding utf8
}
if ((Get-Date).Day -eq 10) {
    $last = (Get-Date).AddMonths(-1).ToString("yyyy-MM")
    & $py -m smart_layer audit --month $last --csv --push 2>&1 | Out-File $log -Append -Encoding utf8
}
