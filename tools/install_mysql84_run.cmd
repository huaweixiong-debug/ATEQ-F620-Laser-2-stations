@echo off
title ATEQ MySQL 8.4 安装（管理员）
echo 请确认此窗口标题包含“管理员”。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\ATEQ\tools\install_mysql84_admin.ps1"
echo.
echo 安装脚本已结束，窗口不会自动关闭。
pause
