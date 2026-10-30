# Project Smart Layer by Azam Puri - daily run (scheduled by install_windows.ps1 as "Smart Layer Daily").
# Refreshes the local copy from the ERP, pushes yesterday's audit and items that ran out of stock, and saves
# reports (CSV + PDF) in the output folder. Fridays: stock, reorder, closing, pilferage, beams, sizers, yarn and
# fabric stock. 1st of the month: P&L, balances, parties, store reconciliation.
# 10th: full audit of last month. Log: output\daily_run_log.txt

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

if ((Run @("licence", "check", "--push")) -ne 0) {
    "no valid licence - nothing run (see the message above)" | Out-File $log -Append -Encoding utf8
    exit 1
}
$sync = Run @("sync")
if ($sync -eq 1) {                                   # every data set failed (ERP unreachable / bad login): stop
    "sync failed completely - reports skipped" | Out-File $log -Append -Encoding utf8
    exit 1
} elseif ($sync -eq 2) {                             # some data sets failed: carry on with the data we have
    "WARNING: some data sets did not sync - running on the data we have (see the lines above)" | Out-File $log -Append -Encoding utf8
}
Run @("audit", "--csv", "--pdf", "--push") | Out-Null
Run @("report", "zero", "--push") | Out-Null
Run @("report", "production", "--csv", "--pdf") | Out-Null
Run @("report", "beams", "--csv", "--pdf") | Out-Null
if ((Get-Date).DayOfWeek -eq "Friday") {
    foreach ($r in "stock", "reorder", "demand", "closing", "pilferage", "sizers", "yarn-stock", "fabric-stock") {
        Run @("report", $r, "--csv", "--pdf") | Out-Null
    }
    # the weekly beam report is saved AND pushed (the PDF is attached to the ntfy alert so it reaches the owner's phone)
    Run @("report", "weekly-beams", "--pdf", "--push") | Out-Null
}
if ((Get-Date).Day -eq 1) {
    foreach ($r in "pnl", "balances", "parties", "store-recon") { Run @("report", $r, "--csv", "--pdf") | Out-Null }
}
if ((Get-Date).Day -eq 10) {
    Run @("audit", "--month", (Get-Date).AddMonths(-1).ToString("yyyy-MM"), "--csv", "--pdf", "--push") | Out-Null
}
# keep the log from growing for ever (lock-safe: writes a temp file and renames it over the original)
& $py -m smart_layer logtrim $log --max-mb 5 --keep-mb 1 | Out-Null
