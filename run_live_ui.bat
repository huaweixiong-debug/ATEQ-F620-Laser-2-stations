@echo off
rem 现场实时启动（需 live.toml 预检通过；A 电脑 station=A，B 电脑 station=B）
cd /d D:\ATEQ
set PYTHONPATH=D:\ATEQ\.venv\Lib\site-packages
D:\Python310\python.exe -u -m app.main --live-ui --live-config D:\ATEQ\config\live.toml --preflight
