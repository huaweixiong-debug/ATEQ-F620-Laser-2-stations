@echo off
rem B 电脑专用：等待 relay 就绪后经 SSH 触发 A 机桌面 UI（由 run_live_ui.bat 自动调用）
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe D:\ATEQ\tools\start_a_ui.py >> D:\ATEQ\a_ui_trigger.log 2>&1
