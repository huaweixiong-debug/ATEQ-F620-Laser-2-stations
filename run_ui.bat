@echo off
rem 模拟模式启动（无硬件）
cd /d D:\ATEQ
set PYTHONPATH=D:\ATEQ\.venv\Lib\site-packages
D:\Python310\python.exe -u -m app.main --config config\default.toml
