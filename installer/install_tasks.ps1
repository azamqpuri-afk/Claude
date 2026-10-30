param([Parameter(Mandatory=$true)][string]$AppDir, [switch]$Start, [switch]$AsSystem)
# Schedules the daily run and the always-on dashboard for the compiled product, and opens the firewall port
# when the dashboard is on the mill network.
# Registering happens during install (no config.toml needed - the run scripts wait for it). The dashboard and
# live sync are STARTED only with -Start, which the setup wizard calls once it has written config.toml, so no
# task runs against a missing config.
$ErrorActionPreference = "Continue"

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$at = "07:30"

# Preserve an EXISTING task's logon on upgrade. Default (fresh install) is SYSTEM, which needs no password and
# reads the machine-sealed licence/.env. But a mill may have switched a task to run as a signed-in user
# (LogonType Interactive, no stored password - e.g. on a Microsoft-account PC with PIN sign-in). This -Force
# re-register would otherwise silently revert that working setup every upgrade, so we keep each task's current
# principal when it already exists and only refresh its action/trigger/settings (the new run scripts, the
# repetition fix). A clean reinstall as SYSTEM is still available with -AsSystem.
function Resolve-Principal($name) {
    if ($AsSystem) { return $principal }
    $ex = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($ex -and $ex.Principal -and $ex.Principal.UserId) {
        try {
            # A stored-password logon (LogonType Password) cannot be re-registered without the password, so the
            # Register would fail; run as the signed-in user instead (Interactive, no password needed).
            $lt = "$($ex.Principal.LogonType)"
            if ($lt -eq "Password") { $lt = "Interactive" }
            return New-ScheduledTaskPrincipal -UserId $ex.Principal.UserId -LogonType $lt -RunLevel $ex.Principal.RunLevel
        } catch { return $principal }
    }
    return $principal
}

# A task run under an INTERACTIVE principal (a signed-in user, no stored password) would flash a black console
# window every run if it launched run_*.cmd directly. run_hidden.vbs launches the .cmd hidden and waits, so wrap
# the command in it for Interactive principals; a SYSTEM principal has no visible session, so it runs the .cmd raw.
function New-RunAction($prin, $cmd) {
    if ("$($prin.LogonType)" -eq "Interactive") {
        return New-ScheduledTaskAction -Execute "wscript.exe" `
               -Argument "`"$AppDir\run_hidden.vbs`" `"$cmd`"" -WorkingDirectory $AppDir
    }
    return New-ScheduledTaskAction -Execute $cmd -WorkingDirectory $AppDir
}

$dailyPrin = Resolve-Principal "Smart Layer Daily"
Register-ScheduledTask -TaskName "Smart Layer Daily" -Action (New-RunAction $dailyPrin "$AppDir\run_daily.cmd") `
    -Principal $dailyPrin -Force `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $at) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)) | Out-Null

# Live sync every 5 minutes, repeating INDEFINITELY. A bounded RepetitionDuration overflows the Task Scheduler XML
# (New-TimeSpan -Days 3650 registered as ~9 days, then stopped for good) and [TimeSpan]::MaxValue is REJECTED on
# Windows 10 ("Duration ... out of range"). The reliable way is to build the repetition with the interval only and
# then set Duration to '' (empty = indefinite) before registering.
$livePrin = Resolve-Principal "Smart Layer Live"
$liveTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$liveTrigger.Repetition.Duration = ''
$liveSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Smart Layer Live" -Action (New-RunAction $livePrin "$AppDir\run_live.cmd") `
    -Principal $livePrin -Force -Trigger $liveTrigger -Settings $liveSettings | Out-Null

# Safety net: confirm the repetition registered as indefinite; if a bounded duration slipped through, clear it.
try {
    $lt = Get-ScheduledTask -TaskName "Smart Layer Live"
    if ($lt.Triggers[0].Repetition.Duration) {
        $lt.Triggers[0].Repetition.Duration = ""
        Set-ScheduledTask -TaskName "Smart Layer Live" -Trigger $lt.Triggers[0] | Out-Null
        Write-Host "Smart Layer Live: repetition duration was bounded; reset to indefinite."
    }
} catch { Write-Host "Could not verify the live-sync repetition: $_" }

# Dashboard: always on. AtStartup fires for SYSTEM (runs with no session); an Interactive principal has no session
# at boot, so it must start AtLogOn instead - otherwise the dashboard never comes up on an Interactive install.
$dashPrin = Resolve-Principal "Smart Layer Dashboard"
# AtLogOn must be scoped to the task's own user: unscoped it needs elevation (Access denied otherwise) and would
# fire at ANY user's logon. SYSTEM has no logon session, so it starts AtStartup instead.
$dashTrigger = if ("$($dashPrin.LogonType)" -eq "Interactive") { New-ScheduledTaskTrigger -AtLogOn -User $dashPrin.UserId } `
               else { New-ScheduledTaskTrigger -AtStartup }
Register-ScheduledTask -TaskName "Smart Layer Dashboard" -Action (New-RunAction $dashPrin "$AppDir\run_dashboard.cmd") `
    -Principal $dashPrin -Force -Trigger $dashTrigger `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)) | Out-Null

if ($Start) {                                        # setup has written config.toml - safe to start now
    Stop-ScheduledTask -TaskName "Smart Layer Dashboard" -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName "Smart Layer Dashboard"
    Start-ScheduledTask -TaskName "Smart Layer Live" -ErrorAction SilentlyContinue   # first live pull now
}

# Firewall: only when the owner chose to open the dashboard to the mill network (host 0.0.0.0).
try {
    $host0 = (& "$AppDir\SmartLayer.exe" --config "$AppDir\config.toml" status 2>$null)   # best-effort
} catch {}
$cfg = Join-Path $AppDir "config.toml"
if (Test-Path $cfg) {
    $hostLine = (Select-String -Path $cfg -Pattern 'host\s*=\s*"0\.0\.0\.0"' -ErrorAction SilentlyContinue)
    if ($hostLine) {
        $portMatch = (Select-String -Path $cfg -Pattern 'port\s*=\s*(\d+)' | Select-Object -First 1)
        $port = if ($portMatch) { $portMatch.Matches[0].Groups[1].Value } else { "8080" }
        Get-NetFirewallRule -DisplayName "Smart Layer Dashboard" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName "Smart Layer Dashboard" -Direction Inbound -Protocol TCP `
            -LocalPort $port -Action Allow -Profile Domain, Private | Out-Null
    }
}
