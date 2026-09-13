@echo off
chcp 65001 >nul
title 绒花墨坊
cd /d "%~dp0console"

if not exist "node_modules\electron\dist\electron.exe" (
    echo [绒花墨坊] 未找到 Electron，请先运行 npm install
    pause
    exit /b 1
)

echo [绒花墨坊] 启动中（自动拉起 nf_api 后端）...
node_modules\electron\dist\electron.exe . --disable-gpu
pause
