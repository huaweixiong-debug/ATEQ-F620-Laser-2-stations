@echo off
rem B-station only: wait for the relay, then trigger the A-station desktop UI via SSH.
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe D:\ATEQ\tools\start_a_ui.py >> D:\ATEQ\a_ui_trigger.log 2>&1
