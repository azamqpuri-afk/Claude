# run_daily_production_report.ps1
# Project : Dost Textiles - Daily Loom Production Report
# Purpose : Wrapper around 13_daily_production_email.py, launched by Task
#           Scheduler instead of calling python.exe directly. Catches
#           failures the Python script's OWN internal try/except can't see
#           - i.e. Task Scheduler successfully firing THIS wrapper, but
#           Python itself failing to even start (wrong interpreter path,
#           missing DLL, etc.) - those never reach the Python script's own
#           exception handler, so nothing would otherwise get logged at all.
#           Always writes a STARTED line before touching Python, so the
#           watchdog (check_daily_report_ran.ps1) can tell "Task Scheduler
#           fired this wrapper" apart from "Task Scheduler never ran
#           anything today" - the latter is invisible to this script (it
#           can't log what never got a chance to run), which is exactly
#           why the watchdog is a separate, independently-scheduled task.

$ErrorActionPreference = 'Stop'

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe    = 'C:\Users\DBSERVER\AppData\Local\Programs\Python\Python312\python.exe'
$PythonScript = Join-Path $ScriptDir '13_daily_production_email.py'
$HeartbeatLog = Join-Path $ScriptDir 'task_heartbeat.txt'
$StatusLog    = Join-Path $ScriptDir 'production_report_status_log.txt'

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
DAILY PRODUCTION REPORT FAILED - $completeStamp - exit code $exitCode
The Python script ran but did not finish successfully. Check the
traceback above (if 13_daily_production_email.py got far enough to log
one) or this task's history in Task Scheduler for more detail.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

"@
    Add-Content -Path $StatusLog -Value $banner2
}

exit $exitCode
