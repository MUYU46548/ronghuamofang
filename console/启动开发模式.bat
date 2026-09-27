@echo off
chcp 65001 >nul 2>&1
title 绒花墨坊 - 开发模式
cd /d "%~dp0"

echo [dev-watch] starting vite + electron...
echo [dev-watch] vite → http://localhost:5180
echo [dev-watch] electron → loads from vite dev server
echo.

set NODE_ENV=development

start "vite" /min cmd /c "npx vite --port 5180"
timeout /t 3 /nobreak >nul

start "electron" /min cmd /c "npx electron ."

echo.
echo [dev-watch] both processes started.
echo [dev-watch] close this window to stop all.
pause
