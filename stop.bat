@echo off
REM ============================================================
REM  ATF Platform - Windows one-click stop script
REM
REM  - Find process(es) listening on PORT
REM  - taskkill /F /T to kill the whole process tree (incl. uvicorn reloader children)
REM  - Double-click to use
REM ============================================================
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM Keep in sync with PORT in start.bat / .env
set "PORT=8866"

echo.
echo === ATF Platform stopper ===
echo.

set "FOUND_ANY="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    set "FOUND_ANY=1"
    echo [info] Killing PID %%a and its child processes...
    taskkill /F /T /PID %%a >nul 2>nul
)

if not defined FOUND_ANY (
    echo [info] No service listening on port %PORT%, nothing to stop.
    timeout /t 2 /nobreak >nul
    exit /b 0
)

REM Wait for port to be released (up to 5 seconds)
set "STOPPED=0"
for /l %%i in (1,1,5) do (
    if "!STOPPED!"=="0" (
        timeout /t 1 /nobreak >nul
        set "STILL="
        for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do set "STILL=1"
        if not defined STILL set "STOPPED=1"
    )
)

if "!STOPPED!"=="1" (
    echo [info] Stopped, port %PORT% released.
) else (
    echo [warn] Port %PORT% is still held; please close the "ATF Server" window manually.
    pause
)

timeout /t 2 /nobreak >nul
endlocal
exit /b 0
