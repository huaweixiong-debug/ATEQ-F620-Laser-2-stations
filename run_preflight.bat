@echo off
rem 只跑 LIVE 预检报告，不启动 UI
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --mode live --config D:\ATEQ\config\live.toml --preflight
pause
