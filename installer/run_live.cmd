@echo off
rem Whole-data live sync - scheduled by install_tasks.ps1 as "Smart Layer Live", every 5 minutes.
rem Refreshes EVERY module (production, beams, finance, store, dispatch/gate passes, payroll ...) but only the last
rem few days, so it is quick and gentle on the ERP. The once-a-day "Smart Layer Daily" task does the deep reconcile
rem (full history and back-dated edits) and sends the phone alerts.
cd /d "%~dp0"
if not exist output mkdir output
set LOG=output\live_run_log.txt
if not exist config.toml (echo %date% %time% no config.toml yet - setup not finished, skipping >> "%LOG%" & exit /b 0)
SmartLayer.exe logtrim "%LOG%" --max-mb 5 --keep-mb 1
echo ==== %date% %time% ==== >> "%LOG%"
SmartLayer.exe licence check >nul 2>&1 || (echo no valid licence - live sync skipped >> "%LOG%" & exit /b 1)
SmartLayer.exe sync --live >> "%LOG%" 2>&1
set SYNCRC=%ERRORLEVEL%
rem Urgent, send-once alerts (loom silent, beam out with no backup, confirmed error, negative stock) - each pushed once.
SmartLayer.exe audit --push --urgent >> "%LOG%" 2>&1
set AUDITRC=%ERRORLEVEL%
rem always write a result line, success or failure, so a silent problem (e.g. the ERP login) is visible in the log
echo ---- done %time%: sync exit %SYNCRC%, audit exit %AUDITRC% ---- >> "%LOG%"
