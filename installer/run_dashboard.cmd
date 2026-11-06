@echo off
rem Keeps the dashboard running - scheduled at startup by install_tasks.ps1 as "Smart Layer Dashboard".
cd /d "%~dp0"
if not exist output mkdir output
set FAILS=0
:loop
if not exist config.toml (echo %date% %time% waiting for setup to write config.toml ... >> output\dashboard_log.txt & timeout /t 30 /nobreak > nul & goto loop)
rem trim here, before serve opens the log: serve holds the file for its whole run, so this is the one free moment
SmartLayer.exe logtrim "output\dashboard_log.txt" --max-mb 5 --keep-mb 1
echo ==== %date% %time% starting dashboard ==== >> output\dashboard_log.txt
set T0=0
set T1=0
for /f %%t in ('powershell -NoProfile -Command "[int][double]::Parse((Get-Date -UFormat %%s))"') do set T0=%%t
SmartLayer.exe serve >> output\dashboard_log.txt 2>&1
for /f %%t in ('powershell -NoProfile -Command "[int][double]::Parse((Get-Date -UFormat %%s))"') do set T1=%%t
rem how long did serve run? a healthy run lasts a long time; a quick exit (e.g. no dashboard password, bad config)
rem means a misconfiguration - count those and back off so a crash loop never floods the log every 5 seconds.
set /a RAN=T1-T0
if %RAN% GEQ 60 (set FAILS=0) else (set /a FAILS+=1)
if %FAILS% GEQ 10 (
  echo %date% %time% dashboard keeps exiting quickly (%FAILS% times) - check config/.env; backing off 60s >> output\dashboard_log.txt
  timeout /t 60 /nobreak > nul
) else (
  timeout /t 5 /nobreak > nul
)
goto loop
