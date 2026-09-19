param(
    [string]$MsiPath = "D:\ATEQ\installers\mysql-8.4.11-winx64.msi",
    [string]$InstallDir = "D:\MySQL84",
    [string]$DataDir = "D:\MySQLData",
    [string]$ServiceName = "MySQL84"
)

$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请在远程电脑上右键 PowerShell，以管理员身份运行本脚本。"
}
if (-not (Test-Path -LiteralPath $MsiPath)) { throw "MySQL MSI 不存在: $MsiPath" }
$actualMd5 = (Get-FileHash -LiteralPath $MsiPath -Algorithm MD5).Hash.ToLowerInvariant()
if ($actualMd5 -ne "b5c515a0f410cd6903cd41057ed5d662") {
    throw "MySQL MSI MD5 不匹配，拒绝安装。实际: $actualMd5"
}
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw "服务 $ServiceName 已存在；为避免覆盖现有数据库，本脚本拒绝继续。"
}
if (Get-NetTCPConnection -LocalPort 3306 -State Listen -ErrorAction SilentlyContinue) {
    throw "端口 3306 已被占用，拒绝安装。"
}

New-Item -ItemType Directory -Force -Path $InstallDir,$DataDir | Out-Null
$msiLog = 'D:\ATEQ\mysql_msi.log'
$arguments = @('/i', ('"' + $MsiPath + '"'), '/qn', '/norestart', '/l*v', ('"' + $msiLog + '"'), ('INSTALLDIR="' + $InstallDir + '"'))
$installer = Start-Process -FilePath msiexec.exe -ArgumentList $arguments -Wait -PassThru
if ($installer.ExitCode -notin 0,3010) { throw "MySQL MSI 安装失败，退出码 $($installer.ExitCode)" }

$mysqld = Join-Path $InstallDir "bin\mysqld.exe"
$mysql = Join-Path $InstallDir "bin\mysql.exe"
if (-not (Test-Path -LiteralPath $mysqld)) {
    $candidate = Get-ChildItem -LiteralPath "C:\Program Files\MySQL" -Filter mysqld.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $candidate) { throw "MSI 已完成但未找到 mysqld.exe" }
    $mysqld = $candidate.FullName
    $InstallDir = Split-Path -Parent (Split-Path -Parent $mysqld)
    $mysql = Join-Path $InstallDir "bin\mysql.exe"
}

$optionFile = Join-Path $InstallDir "my.ini"
$optionText = @"
[mysqld]
basedir=$($InstallDir.Replace('\','/'))
datadir=$($DataDir.Replace('\','/'))
port=3306
bind-address=127.0.0.1
mysqlx-bind-address=127.0.0.1
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
default-time-zone=+08:00
log-error=$($DataDir.Replace('\','/'))/mysql-error.log

[client]
host=127.0.0.1
port=3306
default-character-set=utf8mb4
"@
[IO.File]::WriteAllText($optionFile, $optionText, (New-Object Text.UTF8Encoding($false)))

if (-not (Test-Path -LiteralPath (Join-Path $DataDir "mysql"))) {
    $savedEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $mysqld "--defaults-file=$optionFile" --initialize-insecure --console 2>&1 | Tee-Object -FilePath (Join-Path $DataDir 'mysql-initialize.log')
    $initExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedEap
    if ($initExitCode -ne 0) { throw "MySQL 数据目录初始化失败，退出码 $initExitCode" }
}
& $mysqld --install $ServiceName "--defaults-file=$optionFile"
if ($LASTEXITCODE -ne 0) { throw "MySQL 服务注册失败" }
Set-Service -Name $ServiceName -StartupType Automatic
Start-Service -Name $ServiceName

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    & $mysql --protocol=TCP -h 127.0.0.1 -u root -N -e "SELECT 1" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}
if ($LASTEXITCODE -ne 0) { throw "MySQL 服务启动后未在 30 秒内就绪" }

function New-SecureHexPassword {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$rootPassword = New-SecureHexPassword
$appPassword = New-SecureHexPassword
$schemaPath = Join-Path $PSScriptRoot "mysql_schema.sql"
$secureSql = Join-Path $env:TEMP ("ateq-mysql-" + [guid]::NewGuid().ToString("N") + ".sql")
try {
    $schema = Get-Content -LiteralPath $schemaPath -Raw
    $accountSql = @"
$schema
CREATE USER IF NOT EXISTS 'ateq_app'@'localhost' IDENTIFIED BY '$appPassword';
ALTER USER 'ateq_app'@'localhost' IDENTIFIED BY '$appPassword';
GRANT SELECT, INSERT, UPDATE ON test.info_A TO 'ateq_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON test.info_B TO 'ateq_app'@'localhost';
CREATE USER IF NOT EXISTS 'ateq_app'@'127.0.0.1' IDENTIFIED BY '$appPassword';
ALTER USER 'ateq_app'@'127.0.0.1' IDENTIFIED BY '$appPassword';
GRANT SELECT, INSERT, UPDATE ON test.info_A TO 'ateq_app'@'127.0.0.1';
GRANT SELECT, INSERT, UPDATE ON test.info_B TO 'ateq_app'@'127.0.0.1';
ALTER USER 'root'@'localhost' IDENTIFIED BY '$rootPassword';
FLUSH PRIVILEGES;
"@
    [IO.File]::WriteAllText($secureSql, $accountSql, (New-Object Text.UTF8Encoding($false)))
    icacls $secureSql /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(F)" | Out-Null
    & $mysql --protocol=TCP -h 127.0.0.1 -u root --default-character-set=utf8mb4 "--execute=source $($secureSql.Replace('\','/'))"
    if ($LASTEXITCODE -ne 0) { throw "数据库、分表或应用账号创建失败" }
} finally {
    Remove-Item -LiteralPath $secureSql -Force -ErrorAction SilentlyContinue
}

$credentialDir = "C:\ProgramData\LeakTest2Channels"
New-Item -ItemType Directory -Force -Path $credentialDir | Out-Null
$credentialPath = Join-Path $credentialDir "db.json"
$credentialJson = @{ user = "ateq_app"; password = $appPassword } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($credentialPath, $credentialJson, (New-Object Text.UTF8Encoding($false)))
$adminCredentialPath = Join-Path $credentialDir "db-admin.json"
$adminCredentialJson = @{ user = "root"; password = $rootPassword } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($adminCredentialPath, $adminCredentialJson, (New-Object Text.UTF8Encoding($false)))
icacls $credentialDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)(RX)" "SYSTEM:(OI)(CI)(F)" "Administrators:(OI)(CI)(F)" | Out-Null
icacls $credentialPath /inheritance:r /grant:r "$($env:USERNAME):(R)" "SYSTEM:(F)" "Administrators:(F)" | Out-Null
icacls $adminCredentialPath /inheritance:r /grant:r "SYSTEM:(F)" "Administrators:(F)" | Out-Null

$python = "D:\Python310\python.exe"
$wheel = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "..\installers") -Filter "PyMySQL-*.whl" | Select-Object -First 1
if (-not (Test-Path -LiteralPath $python) -or -not $wheel) { throw "缺少 D:\Python310\python.exe 或离线 PyMySQL wheel" }
& $python -m pip install --no-index $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "PyMySQL 离线安装失败" }

$env:MYSQL_PWD = $appPassword
try {
    $tables = & $mysql --protocol=TCP -h 127.0.0.1 -u ateq_app -N -e "SELECT table_name FROM information_schema.tables WHERE table_schema='test' ORDER BY table_name"
    if ($LASTEXITCODE -ne 0 -or $tables -notcontains "info_A" -or $tables -notcontains "info_B") {
        throw "应用账号验收失败"
    }
} finally {
    Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue
}

Write-Output "MySQL 8.4 安装完成：service=$ServiceName bind=127.0.0.1:3306 database=test tables=info_A,info_B"
Write-Output "应用凭据已写入受 ACL 保护文件：$credentialPath（未输出密码）"
Write-Output "管理员恢复凭据已写入仅 SYSTEM/Administrators 可读文件：$adminCredentialPath"
