param([Parameter(Mandatory=$true)][string]$AppDir)
# Schedules the daily run and the always-on dashboard for the compiled product, and opens the firewall port
# when the dashboard is on the mill network. Run by the installer (SmartLayer.iss) after setup + first sync.
$ErrorActionPreference = "Continue"

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$at = "07:30"

$daily = New-ScheduledTaskAction -Execute "$AppDir\run_daily.cmd" -WorkingDirectory $AppDir
Register-ScheduledTask -TaskName "Smart Layer Daily" -Action $daily -Principal $principal -Force `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $at) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)) | Out-Null

# Live sync: a light pull of the fast-changing weaving data every 5 minutes, so the dashboard stays current.
$live = New-ScheduledTaskAction -Execute "$AppDir\run_live.cmd" -WorkingDirectory $AppDir
$liveTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$liveSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Smart Layer Live" -Action $live -Principal $principal -Force `
    -Trigger $liveTrigger -Settings $liveSettings | Out-Null

$dash = New-ScheduledTaskAction -Execute "$AppDir\run_dashboard.cmd" -WorkingDirectory $AppDir
Register-ScheduledTask -TaskName "Smart Layer Dashboard" -Action $dash -Principal $principal -Force `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)) | Out-Null
Stop-ScheduledTask -TaskName "Smart Layer Dashboard" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "Smart Layer Dashboard"

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
