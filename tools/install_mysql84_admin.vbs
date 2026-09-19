Option Explicit
Dim sh, cmd
Set sh = CreateObject("Shell.Application")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File ""D:\ATEQ\tools\install_mysql84_admin.ps1"""
sh.ShellExecute "cmd.exe", "/k powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""D:\ATEQ\tools\install_mysql84_admin.ps1""", "D:\ATEQ\tools", "runas", 1
