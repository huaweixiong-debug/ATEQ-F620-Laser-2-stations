@echo off
rem Run once on station A (as dell): create the interactive task that
rem station B triggers over SSH so the A UI opens on the A desktop.
schtasks /create /tn ATEQ_A_UI /tr "D:\ATEQ\run_live_ui.bat" /sc once /st 00:00 /it /f
if errorlevel 1 (
  echo FAILED: run this script locally on station A as dell
  pause
  exit /b 1
)
schtasks /query /tn ATEQ_A_UI
pause
