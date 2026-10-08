@echo off
rem Simulate mode (no hardware).
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --config config\default.toml
