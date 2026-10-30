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
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).DayOfWeek"') do set DOW=%%d
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).Day"') do set DOM=%%d
if "%DOW%"=="Friday" (
  for %%r in (stock reorder demand closing pilferage sizers yarn-stock fabric-stock) do SmartLayer.exe report %%r --csv --pdf >> "%LOG%" 2>&1
  SmartLayer.exe report weekly-beams --pdf --push >> "%LOG%" 2>&1
)
if "%DOM%"=="1" (
  for %%r in (pnl balances parties store-recon) do SmartLayer.exe report %%r --csv --pdf >> "%LOG%" 2>&1
)
if "%DOM%"=="10" (
  for /f %%m in ('powershell -NoProfile -Command "(Get-Date).AddMonths(-1).ToString('yyyy-MM')"') do SmartLayer.exe audit --month %%m --csv --pdf --push >> "%LOG%" 2>&1
)
