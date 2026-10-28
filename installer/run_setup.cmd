@echo off
rem Admin launcher for "Smart Layer setup (change answers)". Setup registers/starts scheduled tasks and may touch
rem the firewall, which need administrator rights, so this re-launches SmartLayer.exe setup elevated (UAC prompt).
cd /d "%~dp0"
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0SmartLayer.exe' -ArgumentList 'setup' -WorkingDirectory '%~dp0' -Verb RunAs"
