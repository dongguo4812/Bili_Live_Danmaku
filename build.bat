@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title B站直播互动控制中心 - 打包

echo ========================================
echo       B站直播互动控制中心 - 文件夹版打包
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到项目虚拟环境，请先运行 start.bat 安装依赖。
    pause
    exit /b 1
)

echo [1/4] 初始化并打包 LOL Hex 扩展...
git submodule update --init --recursive
if errorlevel 1 (
    echo [错误] LOL Hex 子模块初始化失败，请确认已安装 Git。
    pause
    exit /b 1
)
if not exist "extensions\lol_hex_display\build.bat" (
    echo [错误] 未找到 LOL Hex 扩展构建脚本。
    pause
    exit /b 1
)
call "extensions\lol_hex_display\build.bat"
if errorlevel 1 (
    echo [错误] LOL Hex 扩展打包失败。
    pause
    exit /b 1
)

echo [2/4] 安装/检查 PyInstaller...
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [错误] PyInstaller 安装失败。
    pause
    exit /b 1
)

echo [3/4] 正在生成 EXE...
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --console ^
  --name Bili_Live_Danmaku ^
  --distpath dist ^
  --workpath build ^
  --specpath build ^
  --collect-all bilibili_api ^
  --collect-all edge_tts ^
  --collect-all selenium ^
  --collect-all obsws_python ^
  --collect-all qrcode ^
  --collect-all PIL ^
  --hidden-import flask ^
  --hidden-import requests ^
  --hidden-import websocket ^
  --hidden-import modules.music.service ^
  --hidden-import modules.video.service ^
  --hidden-import modules.live2d.service ^
  --hidden-import modules.voice.service ^
  main.py

if errorlevel 1 (
    echo [错误] PyInstaller 打包失败。
    pause
    exit /b 1
)

echo [4/4] 正在复制外部资源和配置...
set "OUT=dist\Bili_Live_Danmaku"
if not exist "%OUT%" (
    echo [错误] 未找到打包输出目录。
    pause
    exit /b 1
)

if exist "templates" xcopy "templates" "%OUT%\templates" /E /I /Y >nul
if exist "static" xcopy "static" "%OUT%\static" /E /I /Y >nul
if exist "models" xcopy "models" "%OUT%\models" /E /I /Y >nul
if exist "config" xcopy "config" "%OUT%\config" /E /I /Y >nul
if exist "README.md" copy /Y "README.md" "%OUT%\README.md" >nul
if exist "README.zh-CN.md" copy /Y "README.zh-CN.md" "%OUT%\README.zh-CN.md" >nul
if not exist "%OUT%\credentials" mkdir "%OUT%\credentials"
if not exist "%OUT%\data" mkdir "%OUT%\data"
if not exist "%OUT%\logs" mkdir "%OUT%\logs"
if not exist "%OUT%\extensions" mkdir "%OUT%\extensions"
if not exist "extensions\lol_hex_display\dist\lol_hex_display\lol_hex_display.exe" (
    echo [错误] 未找到 LOL Hex 打包输出。
    pause
    exit /b 1
)
xcopy "extensions\lol_hex_display\dist\lol_hex_display" "%OUT%\extensions\lol_hex_display" /E /I /Y >nul
if errorlevel 1 (
    echo [错误] 复制 LOL Hex 扩展失败。
    pause
    exit /b 1
)
copy /Y "start_exe.bat" "%OUT%\start.bat" >nul

echo.
echo ========================================
echo 打包完成：%OUT%
echo 请将整个文件夹一起分发，不要只复制 EXE。
echo credentials、data 和 logs 是本机数据，不要上传公开仓库。
echo ========================================
pause
