# run_daily_zero_stock_check.ps1
# Project : Dost Textiles - Daily New-Zero-Stock Push Alert
# Purpose : Wrapper around 36_daily_zero_stock_check.py, launched by Task
#           Scheduler ("DostTextiles Daily Zero-Stock Alert", daily
#           6:00 PM) instead of calling python.exe directly - same
#           launch-failure-safety pattern as run_daily_production_report.ps1
#           and run_weekly_store_stock_check.ps1 (catches Task Scheduler
#           successfully firing this wrapper but Python itself failing to
#           even start, which would otherwise never reach the Python
#           script's own exception handler).
#
#           DELIBERATELY SEPARATE from run_weekly_store_stock_check.ps1 -
#           own heartbeat/status log, own Task Scheduler job, per explicit
#           request (2026-09-18) to keep the daily push alert and the
#           weekly PDF review independent of each other.

$ErrorActionPreference = 'Stop'

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe    = 'C:\Users\DBSERVER\AppData\Local\Programs\Python\Python312\python.exe'
$PythonScript = Join-Path $ScriptDir '36_daily_zero_stock_check.py'
$HeartbeatLog = Join-Path $ScriptDir 'daily_zero_stock_heartbeat.txt'
$StatusLog    = Join-Path $ScriptDir 'daily_zero_stock_status_log.txt'

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -Path $HeartbeatLog -Value "STARTED $stamp"

try {
    & $PythonExe $PythonScript
    $exitCode = $LASTEXITCODE
} catch {
    $exitCode = 1
    $err = $_.Exception.Message
    $banner = @"

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
TASK LAUNCH FAILURE - $stamp
Could not launch Python at all (not a data/query error - the script never
even started running). Check the Python install path in this wrapper and
in report_config.ini.
Error: $err
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

"@
    Add-Content -Path $StatusLog -Value $banner
}

$completeStamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -Path $HeartbeatLog -Value "COMPLETED $completeStamp exit=$exitCode"

if ($exitCode -ne 0) {
    $banner2 = @"

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
DAILY ZERO-STOCK CHECK FAILED - $completeStamp - exit code $exitCode
The Python script ran but did not finish successfully. Check the
traceback above (if 36_daily_zero_stock_check.py got far enough to log
one) or this task's history in Task Scheduler for more detail.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

"@
    Add-Content -Path $StatusLog -Value $banner2
}

exit $exitCode
