@echo off
rem 现场实时启动（需 live.toml 预检通过）
rem A 电脑（station=A）：只启动本机 UI。
rem B 电脑（station=B）：本机 UI 起来后（relay 就绪），自动经 SSH 拉起 A 机桌面 UI
rem                       （计划任务 ATEQ_A_UI，见 tools\install_a_ui_task.cmd）。
cd /d D:\ATEQ
for /f "tokens=2 delims== " %%s in ('findstr /b /c:"station" config\live.toml') do set ATEQ_STATION=%%s
set ATEQ_STATION=%ATEQ_STATION:"=%
if /i "%ATEQ_STATION%"=="B" start "" /min D:\ATEQ\tools\start_a_ui.bat
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --live-ui --live-config D:\ATEQ\config\live.toml --preflight
