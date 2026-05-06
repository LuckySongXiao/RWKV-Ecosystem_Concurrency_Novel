@echo off
REM RWKV 超级并发多智能体小说共创框架 - 一键启动脚本
REM
REM 功能:
REM   1. 自动检测并启动 RWKV 推理服务
REM   2. 启动 Web UI 界面
REM   3. 提供友好的启动提示和错误处理

setlocal

set PROJECT_DIR=%~dp0.

echo ============================================
echo  RWKV 超级并发多智能体小说共创框架
echo  一键启动脚本
echo ============================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo [INFO] 正在启动项目...
echo [INFO] RWKV 推理服务将自动启动（如果未运行）
echo [INFO] Web UI 将监听在 http://localhost:5000
echo.

cd /d "%PROJECT_DIR%"

REM 启动主程序（Web UI 模式，自动启动 RWKV）
python main.py --web

endlocal
