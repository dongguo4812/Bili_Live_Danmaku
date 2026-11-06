@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title B站直播弹幕助手

echo [检查] 正在检查管理服务端口 8768...
powershell.exe -NoProfile -Command "$listener = Get-NetTCPConnection -LocalPort 8768 -State Listen -ErrorAction SilentlyContinue; if ($listener) { exit 1 } else { exit 0 }" >nul 2>nul
if errorlevel 1 (
    echo [错误] 端口 8768 已被占用，程序可能已经在运行。
    echo 请勿重复启动；如确认程序已关闭，请检查占用 8768 端口的进程。
    pause
    exit /b 1
)

echo ========================================
echo       B站直播弹幕助手 - 启动程序
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到系统 Python，请先安装 Python 3.10+ 并确保 python 命令可用。
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] 当前 python 不是 Python 3.10 或更高版本。
    echo 请检查 PATH 中 python 命令指向的解释器。
    pause
    exit /b 1
)

echo [1/2] 正在检查系统 Python 依赖...
python -c "import flask, requests, aiohttp, qrcode, bilibili_api, websocket, edge_tts, obsws_python, selenium, keyboard, mss, rapidocr_onnxruntime" >nul 2>nul
if errorlevel 1 (
    echo [错误] 系统 Python 缺少项目依赖，或依赖导入失败。
    echo 请在项目目录手动执行：python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [2/2] 正在启动服务...
echo Python： 
python -c "import sys; print(sys.executable)"
echo 管理页面：http://127.0.0.1:8768/
echo Live2D OBS 页面：http://127.0.0.1:8768/live2d/display
echo 综合展示 OBS 页面：http://127.0.0.1:8768/display/combined
echo 综合展示布局编辑：http://127.0.0.1:8768/dashboard
echo 按 Ctrl+C 可停止服务。
echo.
start "" http://127.0.0.1:8768/
python main.py

echo.
echo 服务已退出。
pause
