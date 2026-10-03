# Project Smart Layer by Azam Puri - removes the scheduled tasks and the firewall rule.
# The folder (settings, local data, reports) is left as it is; delete it yourself if no longer needed.
# Run as Administrator.

foreach ($t in "Smart Layer Dashboard", "Smart Layer Daily") {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
}
Get-NetFirewallRule -DisplayName "Smart Layer Dashboard" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*smart_layer serve*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "Smart Layer tasks, dashboard and firewall rule removed." -ForegroundColor Green
