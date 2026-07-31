@echo off
REM ============================================================
REM  ATF Platform - Windows one-click start script
REM
REM  Double-click to use:
REM    1. Auto-clean stale port listeners / leftover uvicorn workers
REM    2. Open a new server log window (title: ATF Server)
REM    3. Wait for server up, then open browser
REM    4. Launcher window auto-closes; server log window stays open
REM ============================================================
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM Keep in sync with PORT in .env; used here for port-check + browser URL.
set "PORT=8866"
set "URL=http://127.0.0.1:%PORT%/"

echo.
echo === ATF Platform launcher ===
echo.

REM ------- 1) Pick Python interpreter -------
set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [error] Python not found. Install Python 3.10+ and add to PATH.
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [info] Python: !PY_CMD!

REM ------- 2) Dependency check -------
!PY_CMD! -c "import uvicorn, fastapi" >nul 2>nul
if errorlevel 1 (
    echo [error] Dependencies missing. Run:
    echo         !PY_CMD! -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM ------- 3) Port check + cleanup -------
REM   Important: when several servers all listen on 0.0.0.0:PORT, netstat
REM   prints multiple lines. We must taskkill EACH PID, otherwise stale
REM   listeners survive and the new server fails to bind / responds wrong.
echo [info] Checking port %PORT% ...
set "CLEANED="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    set "CLEANED=1"
    echo [warn] Port %PORT% held by PID %%a, killing process tree...
    taskkill /F /T /PID %%a >nul 2>nul
)
if defined CLEANED (
    timeout /t 2 /nobreak >nul
)

REM ------- 4) Spawn server window (background) -------
REM   /D sets the child cmd's working dir directly (avoids fragile cd /d).
REM   "|| pause" keeps the window open if Python crashes, so you can read
REM   the traceback instead of seeing the window flash and disappear.
echo [info] Launching server window...
start "ATF Server" /D "%~dp0" %COMSPEC% /k "chcp 65001 >nul && %PY_CMD% run.py || pause"

REM ------- 5) Wait for server, then open browser -------
echo [info] Waiting for server to become ready...
set "READY=0"
for /l %%i in (1,1,20) do (
    if "!READY!"=="0" (
        timeout /t 1 /nobreak >nul
        REM Use PowerShell to probe the listening port (no curl dependency)
        powershell -NoProfile -Command "if ((Test-NetConnection -ComputerName 127.0.0.1 -Port %PORT% -InformationLevel Quiet -WarningAction SilentlyContinue)) { exit 0 } else { exit 1 }" >nul 2>nul
        if not errorlevel 1 set "READY=1"
    )
)

if "!READY!"=="1" (
    echo [info] Server is up, opening browser...
    start "" "%URL%"
) else (
    echo [warn] Timed out waiting for server. Open %URL% manually.
    echo [warn] See the "ATF Server" window for server logs / errors.
    pause
)

endlocal
exit /b 0
