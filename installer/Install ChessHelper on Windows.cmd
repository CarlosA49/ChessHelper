@echo off
setlocal
title ChessHelper Installer
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
if errorlevel 1 (
  echo.
  echo ChessHelper installation did not finish.
  pause
  exit /b 1
)
exit /b 0
