# Project Smart Layer by Azam Puri - morning push (scheduled ~06:20 as "Smart Layer Morning").
# Morning production summary (scoreboard vs ERP), the night-shift sensor report, and the weft check.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = if (Test-Path "python_path.txt") { (Get-Content "python_path.txt" -TotalCount 1).Trim() } else { "python" }
New-Item -ItemType Directory -Force -Path output | Out-Null
$log = "output\morning_log.txt"
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ====" | Out-File $log -Append -Encoding utf8
& $py -m smart_layer licence check 2>&1 | Out-File $log -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { "no valid licence - morning push skipped" | Out-File $log -Append -Encoding utf8; exit 1 }
& $py -m smart_layer morning 2>&1 | Out-File $log -Append -Encoding utf8
& $py -m smart_layer night-sensor 2>&1 | Out-File $log -Append -Encoding utf8
& $py -m smart_layer weft-check 2>&1 | Out-File $log -Append -Encoding utf8
