@echo off
rem 现场实时启动（需 live.toml 预检通过；A 电脑 station=A，B 电脑 station=B）
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --live-ui --live-config D:\ATEQ\config\live.toml --preflight
