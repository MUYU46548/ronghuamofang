@echo off
chcp 65001 >nul 2>&1
title 绒花墨坊
cd /d "E:\CODE\CangKu\NovelForge\console"

if not exist "node_modules\electron\dist\electron.exe" (
    echo [ronghua] Electron 未找到，请先运行: npm install
    pause
    exit /b 1
)

echo [ronghua] 正在启动绒花墨坊...
node_modules\electron\dist\electron.exe . --disable-gpu
