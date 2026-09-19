@echo off
rem 只跑 LIVE 预检报告，不启动 UI
cd /d D:\ATEQ
set PYTHONPATH=D:\ATEQ\.venv\Lib\site-packages
D:\Python310\python.exe -u -m app.main --config D:\ATEQ\config\live.toml --preflight
pause
