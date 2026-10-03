# Live sensor-twin alert: copy the scoreboard DB from Y-PC, look for two looms stopping/restarting together in the running shift, push once per episode.
$py = 'C:\Users\DBSERVER\AppData\Local\Programs\Python\Python312\python.exe'
$log = 'C:\Users\DBSERVER\DostTextiles\scoreboard\sensor_twin_live_log.txt'
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File $log -Append -Encoding utf8
& $py 'C:\Users\DBSERVER\DostTextiles\scripts\77_sensor_twin_check.py' --live --copy --push 2>&1 | Out-File $log -Append -Encoding utf8