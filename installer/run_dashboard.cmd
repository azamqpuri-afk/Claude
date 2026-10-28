@echo off
rem Keeps the dashboard running - scheduled at startup by install_tasks.ps1 as "Smart Layer Dashboard".
cd /d "%~dp0"
if not exist output mkdir output
:loop
if not exist config.toml (echo %date% %time% waiting for setup to write config.toml ... >> output\dashboard_log.txt & timeout /t 30 /nobreak > nul & goto loop)
rem trim here, before serve opens the log: serve holds the file for its whole run, so this is the one free moment
SmartLayer.exe logtrim "output\dashboard_log.txt" --max-mb 5 --keep-mb 1
echo ==== %date% %time% starting dashboard ==== >> output\dashboard_log.txt
SmartLayer.exe serve >> output\dashboard_log.txt 2>&1
timeout /t 5 /nobreak > nul
goto loop
