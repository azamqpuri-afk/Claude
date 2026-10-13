@echo off
rem Light sync for the live dashboard - scheduled by install_tasks.ps1 as "Smart Layer Live", every 5 minutes.
rem Pulls only the fast-changing weaving data (production, beams, loom monitor) so it is quick and gentle on the
rem ERP. The once-a-day "Smart Layer Daily" task refreshes everything else (finance, store, contracts, history).
cd /d "%~dp0"
if not exist output mkdir output
set LOG=output\live_run_log.txt
echo ==== %date% %time% ==== >> "%LOG%"
SmartLayer.exe licence check >nul 2>&1 || (echo no valid licence - live sync skipped >> "%LOG%" & exit /b 1)
SmartLayer.exe sync --entities loom_production beams beam_movements loom_monitor >> "%LOG%" 2>&1
