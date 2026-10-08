@echo off
rem 在 A 电脑本地运行一次：创建交互式计划任务 ATEQ_A_UI，
rem 供 B 电脑经 SSH 拉起 A 的现场 UI（显示在 A 的桌面会话）。
schtasks /create /tn ATEQ_A_UI /tr "D:\ATEQ\run_live_ui.bat" /sc once /st 00:00 /it /f
if errorlevel 1 (
  echo 创建失败：请确认以 dell 身份在本机运行本脚本
  pause
  exit /b 1
)
schtasks /query /tn ATEQ_A_UI
pause
