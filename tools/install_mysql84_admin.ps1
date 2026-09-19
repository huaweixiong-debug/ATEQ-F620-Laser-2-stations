$ErrorActionPreference = 'Continue'
$log = 'D:\ATEQ\mysql_install.log'
"[$(Get-Date -Format s)] MySQL install started" | Out-File -FilePath $log -Encoding utf8
& 'D:\ATEQ\tools\install_mysql84.ps1' 2>&1 | Tee-Object -FilePath $log -Append
Write-Host "Install finished. Log: $log" -ForegroundColor Yellow
Read-Host 'Press ENTER to close'
