@echo off
rem Live UI launcher (requires a passing preflight in live.toml).
rem Station A: starts only the local UI.
rem Station B: after the local relay is ready, also brings up the A-station UI
rem             via SSH (tools\start_a_ui.bat, task ATEQ_A_UI on station A).
cd /d D:\ATEQ
for /f "tokens=2 delims== " %%s in ('findstr /b /c:"station" config\live.toml') do set ATEQ_STATION=%%s
set ATEQ_STATION=%ATEQ_STATION:"=%
if /i "%ATEQ_STATION%"=="B" start "" /min D:\ATEQ\tools\start_a_ui.bat
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --live-ui --live-config D:\ATEQ\config\live.toml --preflight
