@echo off
setlocal
echo This launcher requests Administrator privileges for the MySQL installation.
cscript.exe //nologo "D:\ATEQ\tools\install_mysql84_admin.vbs"
echo UAC prompt sent. Complete the installation in the elevated PowerShell window.
pause
