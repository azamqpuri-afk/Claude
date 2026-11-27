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
# $fallback is the principal to use when the task does NOT already exist. For a task added in a later version
# (e.g. "Smart Layer Morning", new in 0.15.0), a plain SYSTEM default would make it run under a DIFFERENT principal
# than the others on a mill that had switched the existing tasks to the signed-in user - so it could not read the
# user-sealed .env / licence context and its licence check failed while Daily's passed. Passing the Daily principal
# as the fallback makes every new task inherit the same logon as Daily, so they all behave the same.
function Resolve-Principal($name, $fallback = $null) {
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
    if ($fallback) { return $fallback }
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

# Register a task idempotently (item P). On a FRESH install it uses the given trigger. On an UPGRADE (the task
# already exists and we are not forcing -AsSystem) it KEEPS the task's current trigger, so a mill that moved the
# daily run to, say, 06:00 is not silently reset to the default every upgrade; only the action (the new run
# scripts) and settings are refreshed. For a signed-in-user (Interactive) task it first tries an S4U logon, which
# lets the task run even when that user is not currently logged on and still needs no stored password; if this PC
# refuses S4U (some local accounts lack the right) it falls back to the Interactive principal unchanged.
function Save-Task($name, $cmd, $prin, $freshTrigger, $settings) {
    $ex = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    $trigger = if ($ex -and $ex.Triggers -and -not $AsSystem) { $ex.Triggers } else { $freshTrigger }
    $action = New-RunAction $prin $cmd
    if ("$($prin.LogonType)" -eq "Interactive") {
        try {
            $s4u = New-ScheduledTaskPrincipal -UserId $prin.UserId -LogonType S4U -RunLevel $prin.RunLevel
            Register-ScheduledTask -TaskName $name -Action (New-RunAction $s4u $cmd) -Principal $s4u -Force `
                -Trigger $trigger -Settings $settings -ErrorAction Stop | Out-Null
            return
        } catch { Write-Host "$name: S4U logon not available on this PC, using interactive logon ($_)." }
    }
    Register-ScheduledTask -TaskName $name -Action $action -Principal $prin -Force `
        -Trigger $trigger -Settings $settings | Out-Null
}

$dailyPrin = Resolve-Principal "Smart Layer Daily"
Save-Task "Smart Layer Daily" "$AppDir\run_daily.cmd" $dailyPrin `
    (New-ScheduledTaskTrigger -Daily -At $at) `
    (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3))

# Live sync every 5 minutes, repeating INDEFINITELY. A bounded RepetitionDuration overflows the Task Scheduler XML
# (New-TimeSpan -Days 3650 registered as ~9 days, then stopped for good) and [TimeSpan]::MaxValue is REJECTED on
# Windows 10 ("Duration ... out of range"). The reliable way is to build the repetition with the interval only and
# then set Duration to '' (empty = indefinite) before registering.
$livePrin = Resolve-Principal "Smart Layer Live" $dailyPrin
$liveTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$liveTrigger.Repetition.Duration = ''
$liveSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -MultipleInstances IgnoreNew
Save-Task "Smart Layer Live" "$AppDir\run_live.cmd" $livePrin $liveTrigger $liveSettings

# Safety net: confirm the repetition registered as indefinite; if a bounded duration slipped through, clear it.
try {
    $lt = Get-ScheduledTask -TaskName "Smart Layer Live"
    if ($lt.Triggers[0].Repetition.Duration) {
        $lt.Triggers[0].Repetition.Duration = ""
        Set-ScheduledTask -TaskName "Smart Layer Live" -Trigger $lt.Triggers[0] | Out-Null
        Write-Host "Smart Layer Live: repetition duration was bounded; reset to indefinite."
    }
} catch { Write-Host "Could not verify the live-sync repetition: $_" }

# Morning push (~06:20): the morning production summary, the night-shift sensor report and the weft check.
$mornPrin = Resolve-Principal "Smart Layer Morning" $dailyPrin
Save-Task "Smart Layer Morning" "$AppDir\run_morning.cmd" $mornPrin `
    (New-ScheduledTaskTrigger -Daily -At "06:20") `
    (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10))

# Dashboard: always on. AtStartup fires for SYSTEM (runs with no session); an Interactive principal has no session
# at boot, so it must start AtLogOn instead - otherwise the dashboard never comes up on an Interactive install.
$dashPrin = Resolve-Principal "Smart Layer Dashboard" $dailyPrin
# AtLogOn must be scoped to the task's own user: unscoped it needs elevation (Access denied otherwise) and would
# fire at ANY user's logon. SYSTEM has no logon session, so it starts AtStartup instead.
$dashTrigger = if ("$($dashPrin.LogonType)" -eq "Interactive") { New-ScheduledTaskTrigger -AtLogOn -User $dashPrin.UserId } `
               else { New-ScheduledTaskTrigger -AtStartup }
Save-Task "Smart Layer Dashboard" "$AppDir\run_dashboard.cmd" $dashPrin $dashTrigger `
    (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
     -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1))

if ($Start) {                                        # setup has written config.toml - safe to start now
    Stop-ScheduledTask -TaskName "Smart Layer Dashboard" -ErrorAction SilentlyContinue
    # Stop-ScheduledTask only ends the task's cmd wrapper; run_dashboard.cmd launches SmartLayer.exe which keeps
    # serving the OLD config from memory. Kill the exe itself so the restart below actually reloads the new config.
    Start-Sleep -Milliseconds 300
    taskkill.exe /IM SmartLayer.exe /F /T 2>$null | Out-Null
    Start-Sleep -Milliseconds 300
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
