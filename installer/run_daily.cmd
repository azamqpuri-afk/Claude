@echo off
rem Daily run for the compiled product - scheduled by install_tasks.ps1 as "Smart Layer Daily".
cd /d "%~dp0"
if not exist output mkdir output
set LOG=output\daily_run_log.txt
if not exist config.toml (echo %date% %time% no config.toml yet - setup not finished, skipping >> "%LOG%" & exit /b 0)
SmartLayer.exe logtrim "%LOG%" --max-mb 5 --keep-mb 1
echo ==== %date% %time% ==== >> "%LOG%"
SmartLayer.exe licence check --push >> "%LOG%" 2>&1 || (echo no valid licence - nothing run >> "%LOG%" & exit /b 1)
SmartLayer.exe sync >> "%LOG%" 2>&1
if errorlevel 2 (echo WARNING: some data sets did not sync - running on the data we have >> "%LOG%") else if errorlevel 1 (echo sync failed completely - reports skipped >> "%LOG%" & exit /b 1)
SmartLayer.exe audit --csv --pdf --push >> "%LOG%" 2>&1
SmartLayer.exe report zero --push >> "%LOG%" 2>&1
SmartLayer.exe report production --csv --pdf >> "%LOG%" 2>&1
SmartLayer.exe report beams --csv --pdf >> "%LOG%" 2>&1
rem Weekly (Friday) and monthly (1st / 10th) jobs, with catch-up if the PC was off on the due day (item B4).
rem The schedule lives in the app now, not in this script - so a missed Friday still runs on the next daily run.
SmartLayer.exe run-scheduled --push >> "%LOG%" 2>&1
