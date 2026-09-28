@echo off
chcp 65001 >nul 2>&1
title 绒花墨坊调试面板
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ronghua-debug] Python 未找到，请先运行: python -m venv .venv
    pause
    exit /b 1
)

echo [ronghua-debug] 启动调试面板...
echo [ronghua-debug] 访问: http://127.0.0.1:8766
echo [ronghua-debug] Ctrl+C 停止
echo.

.venv\Scripts\python.exe scripts\nfctl.py serve --port 8766
