@echo off
chcp 65001 >nul 2>&1
title 绒花墨坊 - 调试模式
cd /d "%~dp0"

echo [ronghua-debug] 绒花墨坊调试模式
echo ================================
echo.

:: 检查 Python
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 未找到 .venv\Scripts\python.exe
    echo 请先运行: python -m venv .venv
    pause
    exit /b 1
)

:: 检查 Node
where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 Node.js
    echo 请先安装: https://nodejs.org/
    pause
    exit /b 1
)

:: 检查依赖
if not exist "console\node_modules\electron\dist\electron.exe" (
    echo [ronghua-debug] 安装 Node 依赖...
    cd console
    call npm install
    cd ..
)

:: 启动 nf_api（后台）
echo [ronghua-debug] 启动 nf_api (127.0.0.1:8765)...
start "nf_api" /min .venv\Scripts\python.exe scripts\nf_api.py --port 8765 --host 127.0.0.1

:: 等待 nf_api 就绪
echo [ronghua-debug] 等待 nf_api 就绪...
timeout /t 3 /nobreak >nul

:: 启动 Electron
echo [ronghua-debug] 启动控制台...
cd console
call npm run dev
cd ..

echo.
echo [ronghua-debug] 调试模式已启动
echo - nf_api: http://127.0.0.1:8765
echo - 控制台: http://localhost:5180
echo.
echo 按任意键关闭...
pause >nul
