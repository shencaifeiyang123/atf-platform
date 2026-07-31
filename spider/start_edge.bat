@echo off
REM 调试模式启动 Edge（使用你平时使用的 Edge profile，已登录态）
REM
REM 前提：需要手动关闭所有 Edge 窗口（包括后台进程）
REM 否则新进程会被旧进程接管，debug 端口不生效

setlocal

set "EDGE_PATH=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE_PATH%" set "EDGE_PATH=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

REM 获取 Edge 用户数据的 profile 路径
set "USER_DATA=%LOCALAPPDATA%\Microsoft\Edge\User Data"

echo ============================================================
echo 启动调试模式 Edge（复用登录态）
echo Profile: %USER_DATA%
echo ============================================================
echo.
echo [重要] 先关闭所有 Edge 窗口：
echo        包括任务管理器里的 Edge 后台进程。
echo        否则 debug 端口不生效。
echo.
pause

REM 杀残留 Edge 进程（webview2 除外）
taskkill /F /IM msedge.exe /T 2>nul
timeout /t 2 /nobreak >nul

echo 正在启动 Edge...
start "" "%EDGE_PATH%" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%USER_DATA%" ^
  "https://bailian.console.aliyun.com/cn-beijing/?tab=app&productCode=p_efm#/app-center"

echo.
echo Edge 启动完成，页面应已加载（登录态应该自动保留）。
echo 然后打开另一个终端：python spider/bailian_spider.py
echo.
echo 需要关闭此 Edge 窗口：
pause
