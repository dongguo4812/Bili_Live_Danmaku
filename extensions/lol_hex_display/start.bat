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

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到系统 Python，请安装 Python 3.10+ 并确保 python 命令可用。
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] 当前 python 不是 Python 3.10 或更高版本。
    pause
    exit /b 1
)

python -c "import flask, requests, PIL, rapidocr_onnxruntime, onnxruntime, mss, keyboard" >nul 2>nul
if errorlevel 1 (
    echo [错误] 系统 Python 缺少 LOL Hex 运行依赖。
    echo 请在项目根目录手动执行：python -m pip install -r requirements.txt
    pause
    exit /b 1
)

python "%~dp0main.py"

if errorlevel 1 (
    echo.
    echo 程序退出或启动失败，请查看 logs\app.log。
    pause
)
