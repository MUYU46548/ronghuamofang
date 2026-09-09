@echo off
rem 绒花墨坊控制台启动器
rem 报错写入 %LOCALAPPDATA%\Temp\nf_console_smoke.log，不弹 GUI 错误框
cd /d E:\CODE\CangKu\NovelForge\console
set ELECTRON_ENABLE_LOGGING=1
"node_modules\electron\dist\electron.exe" . >> "%LOCALAPPDATA%\Temp\nf_console_smoke.log" 2>&1
