# Project Smart Layer by Azam Puri - keeps the dashboard running (started with Windows by the
# "Smart Layer Dashboard" task). If it stops for any reason it starts again after 10 seconds.
# Log: output\dashboard_log.txt

Set-Location $PSScriptRoot
$py = if (Test-Path "python_path.txt") { (Get-Content "python_path.txt" -TotalCount 1).Trim() } else { "python" }
New-Item -ItemType Directory -Force -Path output | Out-Null
$log = "output\dashboard_log.txt"
while ($true) {
    # trim here, before serve opens the log: serve holds the file for its whole run, so this is the one moment it is free
    & $py -m smart_layer logtrim $log --max-mb 5 --keep-mb 1 | Out-Null
    "==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') dashboard starting" | Out-File $log -Append -Encoding utf8
    & $py -m smart_layer serve 2>&1 | Out-File $log -Append -Encoding utf8
    "dashboard stopped (exit $LASTEXITCODE) - restarting in 10 s" | Out-File $log -Append -Encoding utf8
    Start-Sleep -Seconds 10
}
