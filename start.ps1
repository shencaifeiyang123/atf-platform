# ============================================================
#  AI 智能体测试平台 一键启动脚本（PowerShell）
#
#  双击 start.bat 已经够用；这个脚本是给习惯 PowerShell 的人。
#
#  - 自动清理端口占用 / 残留 uvicorn 子进程
#  - 弹出新的 PowerShell 窗口跑服务（标题：ATF Server）
#  - 等服务起来后自动打开浏览器
#  - 启动器窗口可关，服务窗口保留
#
#  用法：
#      .\start.ps1
#  若提示「无法加载脚本」：
#      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# ============================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location -Path $PSScriptRoot

$port = 8866
$url  = "http://127.0.0.1:$port/"

Write-Host ""
Write-Host "=== AI 智能体测试平台 启动器 ===" -ForegroundColor Cyan
Write-Host ""

# ------- 1) 选择 Python 解释器 -------
$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pyCmd = "py"
    $pyArgs = @("-3", "run.py")
    $pyProbe = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pyCmd = "python"
    $pyArgs = @("run.py")
    $pyProbe = @()
} else {
    Write-Host "[错误] 系统未找到 Python，请先安装 Python 3.10+ 并加入 PATH。" -ForegroundColor Red
    Write-Host "       下载: https://www.python.org/downloads/"
    Read-Host "按回车退出"
    exit 1
}
Write-Host "[info] Python: $pyCmd $($pyProbe -join ' ')"

# ------- 2) 依赖检测 -------
& $pyCmd @pyProbe -c "import uvicorn, fastapi" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 依赖未安装。请先执行：" -ForegroundColor Red
    Write-Host "       $pyCmd $($pyProbe -join ' ') -m pip install -r requirements.txt"
    Read-Host "按回车退出"
    exit 1
}

# ------- 3) 端口占用检测 + 清理 -------
Write-Host "[info] 检查端口 $port ..."
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    foreach ($conn in $listening) {
        $pidToKill = $conn.OwningProcess
        Write-Host "[warn] 端口 $port 被进程 $pidToKill 占用，正在停止该进程及其子进程..." -ForegroundColor Yellow
        & taskkill /F /T /PID $pidToKill 2>$null | Out-Null
    }
    Start-Sleep -Seconds 2
}

# ------- 4) 弹出新窗口跑服务 -------
Write-Host "[info] 启动服务窗口（标题：ATF Server）..."
$cmdLine = "chcp 65001 >nul & cd /d `"$PSScriptRoot`" & $pyCmd $($pyArgs -join ' ')"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $cmdLine -WorkingDirectory $PSScriptRoot

# ------- 5) 等服务就绪后打开浏览器 -------
Write-Host "[info] 等待服务就绪..."
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    $probe = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($probe) { $ready = $true; break }
}

if ($ready) {
    Write-Host "[info] 服务已就绪，正在打开浏览器..." -ForegroundColor Green
    Start-Process $url
} else {
    Write-Host "[warn] 等待超时，未能确认服务就绪；请手动访问 $url" -ForegroundColor Yellow
    Write-Host "[warn] 服务日志请查看刚弹出的「ATF Server」窗口。"
    Read-Host "按回车退出"
}
