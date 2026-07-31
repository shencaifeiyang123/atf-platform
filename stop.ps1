# ============================================================
#  AI 智能体测试平台 一键停止脚本（PowerShell）
# ============================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location -Path $PSScriptRoot

$port = 8866

Write-Host ""
Write-Host "=== AI 智能体测试平台 停止器 ===" -ForegroundColor Cyan
Write-Host ""

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Write-Host "[info] 端口 $port 没有服务在监听，无需停止。"
    Start-Sleep -Seconds 1
    exit 0
}

foreach ($conn in $listening) {
    $pidToKill = $conn.OwningProcess
    Write-Host "[info] 正在停止进程 $pidToKill 及其子进程..."
    & taskkill /F /T /PID $pidToKill 2>$null | Out-Null
}

# 等端口释放
$stopped = $false
for ($i = 0; $i -lt 5; $i++) {
    Start-Sleep -Seconds 1
    $still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $still) { $stopped = $true; break }
}

if ($stopped) {
    Write-Host "[info] 已停止，端口 $port 已释放。" -ForegroundColor Green
} else {
    Write-Host "[warn] 端口 $port 仍被占用，请打开「ATF Server」窗口手动关闭。" -ForegroundColor Yellow
    Read-Host "按回车退出"
}
