chcp 65001 >nul 2>&1
@echo off
REM RWKV Super Concurrent Multi-Agent Novel Co-Creation Framework
REM ============================================================
REM One-Click Startup with port conflict auto-handling
REM ============================================================

setlocal

set PROJECT_DIR=%~dp0.
set RWKV_PORT=5000

echo ============================================
echo  RWKV Super Concurrent Novel Co-Creation
echo  One-Click Startup
echo ============================================
echo.

REM Check if Python is installed
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+ and add to PATH.
    echo.
    echo Press any key to close...
    pause >nul
    exit /b 1
)

REM Check if port 5000 is occupied
echo [INFO] Checking port %RWKV_PORT% availability...
set "OCCUPIED_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%RWKV_PORT% " ^| findstr "LISTENING"') do (
    set "OCCUPIED_PID=%%P"
    goto :port_check_done
)
:port_check_done
if defined OCCUPIED_PID (
    echo [WARNING] Port %RWKV_PORT% is already occupied by PID: %OCCUPIED_PID%
    echo [INFO] Querying process info...
    for /f "tokens=1,*" %%A in ('tasklist /FI "PID eq %OCCUPIED_PID%" /FO LIST 2^>nul ^| findstr /C:"Image Name"') do (
        echo [INFO] Process: %%B ^(PID %OCCUPIED_PID%^)
    )
    echo.
    echo [INFO] Auto-killing the old process to free port %RWKV_PORT%...
    taskkill /F /PID %OCCUPIED_PID% >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to kill PID %OCCUPIED_PID%. Please close it manually.
        echo.
        echo Options:
        echo   1) Close the application using port %RWKV_PORT%, then run this script again
        echo   2) Or change the port in pipeline.config.json
        echo.
        pause >nul
        exit /b 1
    )
    echo [INFO] Old process killed. Port %RWKV_PORT% is now free.
    REM Also try to kill any orphaned RWKV inference service
    taskkill /F /IM rwkv_lightning.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
) else (
    echo [INFO] Port %RWKV_PORT% is free.
)

echo.
echo [INFO] Starting project...
echo [INFO] RWKV inference service will auto-start (if not running).
echo [INFO] Web UI will listen on http://localhost:%RWKV_PORT%
echo [INFO] Close this window or press Ctrl+C to stop the service.
echo.

cd /d "%PROJECT_DIR%"

REM Launch main program (Web UI mode, auto-start RWKV)
python main.py --web
set EXIT_CODE=%errorlevel%

echo.
echo ============================================
if %EXIT_CODE% neq 0 (
    echo  [ERROR] Program exited abnormally, code: %EXIT_CODE%
) else (
    echo  [INFO] Program exited.
)
echo  Press any key to close window...
echo ============================================
pause >nul
endlocal
