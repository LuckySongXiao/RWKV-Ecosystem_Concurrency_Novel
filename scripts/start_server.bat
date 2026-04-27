@echo off
REM 启动 rwkv_lightning 模型服务
REM
REM 用法:
REM   scripts\start_server.bat
REM
REM 前置条件:
REM   1. 模型文件已转换为 .st 格式 (运行 scripts\convert_model.py)
REM   2. CUDA 驱动已安装

setlocal

set PROJECT_DIR=%~dp0..
set LIGHTNING_DIR=%PROJECT_DIR%\rwkv_lightning_libtorch_win
set MODEL_DIR=%PROJECT_DIR%\rwkv_models

REM 模型文件路径 (需要 .st 格式)
set MODEL_PATH=%MODEL_DIR%\rwkv7-g1c-13.3b-20251231-ctx8192.st
set VOCAB_PATH=%LIGHTNING_DIR%\rwkv_vocab_v20230424.txt

REM 检查模型文件是否存在
if not exist "%MODEL_PATH%" (
    echo [ERROR] 模型文件不存在: %MODEL_PATH%
    echo.
    echo 请先运行模型转换:
    echo   python scripts\convert_model.py --input %MODEL_DIR%\rwkv7-g1c-13.3b-20251231-ctx8192.pth --output %MODEL_PATH%
    echo.
    echo 注意: 需要 Python 3.10-3.12 + PyTorch + safetensors
    pause
    exit /b 1
)

REM 检查 vocab 文件
if not exist "%VOCAB_PATH%" (
    echo [ERROR] Vocab 文件不存在: %VOCAB_PATH%
    pause
    exit /b 1
)

echo ============================================
echo  启动 RWKV Lightning 模型服务
echo ============================================
echo  模型: %MODEL_PATH%
echo  Vocab: %VOCAB_PATH%
echo  端口: 8000
echo ============================================
echo.

cd /d "%LIGHTNING_DIR%"

rwkv_lightning.exe --model-path "%MODEL_PATH%" --vocab-path "%VOCAB_PATH%" --port 8000

endlocal
