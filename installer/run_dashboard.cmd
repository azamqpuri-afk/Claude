@echo off
rem Keeps the dashboard running - scheduled at startup by install_tasks.ps1 as "Smart Layer Dashboard".
cd /d "%~dp0"
if not exist output mkdir output
:loop
echo ==== %date% %time% starting dashboard ==== >> output\dashboard_log.txt
SmartLayer.exe serve >> output\dashboard_log.txt 2>&1
timeout /t 5 /nobreak > nul
goto loop
