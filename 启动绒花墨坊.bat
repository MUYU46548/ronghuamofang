@echo off
cd /d "%~dp0console"
if not exist "node_modules\electron\dist\electron.exe" (
    echo [ronghua] Electron not found. Run: npm install
    pause
    exit /b 1
)
echo [ronghua] Starting...
node_modules\electron\dist\electron.exe . --disable-gpu
pause
