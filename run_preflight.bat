@echo off
rem LIVE preflight report only (no UI).
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --mode live --config D:\ATEQ\config\live.toml --preflight
pause
