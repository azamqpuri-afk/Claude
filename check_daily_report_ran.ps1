# check_daily_report_ran.ps1
# Project : Dost Textiles - Daily Loom Production Report
# Purpose : Watchdog, runs a couple hours after the main report task's
#           scheduled time. Checks whether TODAY has a STARTED entry in
#           task_heartbeat.txt at all - if not, Task Scheduler never even
#           launched the main task today (disabled, trigger didn't fire,
#           PC was off/locked, etc.) - the deepest, most invisible failure
#           mode, since nothing else (including run_daily_production_
#           report.ps1's own failure handling) can catch a launch that
#           never happened. This is the specific gap flagged 2026-08-08:
#           "a silent scheduler failure is worse than a silent 'not
#           entered yet', since we won't even know to check."
#
#           Deliberately silent on success (both STARTED and COMPLETED
#           present) - the main wrapper has already logged the real
#           outcome (OK / not entered yet / missing days / error) above;
#           this watchdog's only job is to catch the case where NOTHING
#           got logged at all, and it should stay quiet otherwise so
#           failures don't get lost in routine noise.

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$HeartbeatLog = Join-Path $ScriptDir 'task_heartbeat.txt'
$StatusLog    = Join-Path $ScriptDir 'production_report_status_log.txt'
$today        = Get-Date -Format 'yyyy-MM-dd'
$stamp        = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

$startedToday   = $false
$completedToday = $false

if (Test-Path $HeartbeatLog) {
    $lines = Get-Content $HeartbeatLog
    $startedToday   = ($lines | Where-Object { $_ -like "STARTED $today*" }).Count -gt 0
    $completedToday = ($lines | Where-Object { $_ -like "COMPLETED $today*" }).Count -gt 0
}

if (-not $startedToday) {
    $banner = @"

################################################################################
##  TASK SCHEDULER FAILURE - $stamp
##  The daily production report task DID NOT RUN AT ALL today ($today).
##  This is NOT a "data not entered yet" case - Task Scheduler itself
##  never launched the script. Check: is "DostTextiles Daily Production
##  Report" enabled in Task Scheduler? Did its trigger fire? Was this PC
##  on and this user session active around the scheduled time?
################################################################################

"@
    Add-Content -Path $StatusLog -Value $banner
} elseif (-not $completedToday) {
    $banner = @"

################################################################################
##  TASK SCHEDULER WARNING - $stamp
##  The daily production report task STARTED today ($today) but never
##  reached completion - it may have hung, been killed, or crashed
##  outside its own error handling. Check "DostTextiles Daily Production
##  Report" in Task Scheduler's history directly.
################################################################################

"@
    Add-Content -Path $StatusLog -Value $banner
}
# Else: both present - main task ran and logged its own real outcome
# already. Nothing to add here.
