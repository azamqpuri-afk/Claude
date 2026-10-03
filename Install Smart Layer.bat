@echo off
rem Project Smart Layer by Azam Puri - double-click to install (asks for Administrator permission).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0install_windows.ps1\"'"
