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

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 需要 Python 3.10 或更高版本。
        echo 请安装 Python 3.10+ 后重试。
        pause
        exit /b 1
    )
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+。
        pause
        exit /b 1
    )
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 需要 Python 3.10 或更高版本。
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 正在创建虚拟环境...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

echo [2/3] 正在检查并安装依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [错误] 更新 pip 失败，请检查网络。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 安装依赖失败，请检查网络或 requirements.txt。
    pause
    exit /b 1
)

echo [3/3] 正在启动服务...
echo 管理页面：http://127.0.0.1:8768/
echo Live2D OBS 页面：http://127.0.0.1:8768/live2d/display
echo 综合展示 OBS 页面：http://127.0.0.1:8768/display/combined
echo 综合展示布局编辑：http://127.0.0.1:8768/dashboard
echo 按 Ctrl+C 可停止服务。
echo.
start "" http://127.0.0.1:8768/
".venv\Scripts\python.exe" main.py

echo.
echo 服务已退出。
pause
