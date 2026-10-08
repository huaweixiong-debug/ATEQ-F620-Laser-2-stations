@echo off
rem 模拟模式启动（无硬件）
cd /d D:\ATEQ
D:\ATEQ\.venv\Scripts\python.exe -u -m app.main --config config\default.toml
