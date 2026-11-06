@echo off
rem Morning summary + night sensor + weft check - scheduled by install_tasks.ps1 as "Smart Layer Morning" (~06:20).
cd /d "%~dp0"
if not exist output mkdir output
set LOG=output\morning_log.txt
if not exist config.toml (exit /b 0)
SmartLayer.exe logtrim "%LOG%" --max-mb 2 --keep-mb 1
echo ==== %date% %time% ==== >> "%LOG%"
SmartLayer.exe licence check >> "%LOG%" 2>&1 || (echo no valid licence - nothing run >> "%LOG%" & exit /b 1)
SmartLayer.exe morning >> "%LOG%" 2>&1
SmartLayer.exe night-sensor >> "%LOG%" 2>&1
SmartLayer.exe weft-check >> "%LOG%" 2>&1
