@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title lol_hex_display

:: 检查当前 BAT 是否具有管理员权限
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo 正在请求管理员权限...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

echo ========================================
echo  lol_hex_display  管理页面：http://127.0.0.1:8767/   海克斯展示页：http://127.0.0.1:8767/display   战绩条：http://127.0.0.1:8767/bar
echo  管理员权限已通过 BAT 检查  
echo ========================================
echo.

:: 优先使用项目虚拟环境的 Python
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
) else (
    py -3 "%~dp0main.py"
)

if errorlevel 1 (
    echo.
    echo 程序退出或启动失败，请查看 logs\app.log。
    pause
)

