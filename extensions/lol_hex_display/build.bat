@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo          LOL 海克斯展示 - EXE打包
echo ========================================
echo.

REM 1. 检查必要文件
echo [1/6] 检查必要文件...

if not exist "main.py" (
    echo [错误] 找不到 main.py
    goto failed
)

if not exist "bar_integration.py" (
    echo [错误] 找不到 bar_integration.py
    goto failed
)

if not exist "lol_hex_display.manifest" (
    echo [错误] 找不到 lol_hex_display.manifest
    goto failed
)

if not exist "templates" (
    echo [错误] 找不到 templates 文件夹
    goto failed
)

if not exist "static" (
    echo [错误] 找不到 static 文件夹
    goto failed
)

echo [通过] 必要文件检查完成。

REM 2. 检查网页模板
echo.
echo [2/6] 检查网页模板...

if not exist "templates\manager.html" (
    echo [错误] 找不到 templates\manager.html
    goto failed
)

if not exist "templates\display.html" (
    echo [错误] 找不到 templates\display.html
    goto failed
)

if not exist "templates\bar.html" (
    echo [错误] 找不到 templates\bar.html
    goto failed
)

echo [通过] 网页模板检查完成。

REM 3. 检查 Python
echo.
echo [3/6] 检查系统 Python...

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到系统 Python。
    goto failed
)

python --version
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo [错误] 需要 Python 3.10 或更高版本。
    goto failed
)

REM 4. 检查打包依赖，不自动安装或升级全局 Python 包
echo.
echo [4/6] 检查打包依赖...

python -c "import PyInstaller, flask, requests, PIL, rapidocr_onnxruntime, onnxruntime, mss, keyboard"
if errorlevel 1 (
    echo [错误] 系统 Python 缺少 LOL Hex 打包依赖。
    echo 请手动执行：python -m pip install pyinstaller flask requests pillow rapidocr_onnxruntime mss keyboard
    goto failed
)

REM 5. 执行打包
echo.
echo [5/6] 开始打包...

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --noconsole ^
    --manifest "lol_hex_display.manifest" ^
    --name "lol_hex_display" ^
    --collect-all rapidocr_onnxruntime ^
    --collect-all onnxruntime ^
    --hidden-import keyboard ^
    --hidden-import mss ^
    --hidden-import tkinter ^
    "launcher.py"

if errorlevel 1 goto failed

REM 6. 复制网页资源
echo.
echo [6/6] 复制外部文件...

xcopy /E /I /Y "templates" "dist\lol_hex_display\templates" >nul
if errorlevel 1 goto failed

xcopy /E /I /Y "static" "dist\lol_hex_display\static" >nul
if errorlevel 1 goto failed

REM 检查最终 EXE
if not exist "dist\lol_hex_display\lol_hex_display.exe" (
    echo [错误] 未找到生成的 EXE 文件
    goto failed
)

echo.
echo ========================================
echo              打包成功！
echo.
echo EXE 位置：
echo dist\lol_hex_display\lol_hex_display.exe
echo.
echo 已复制：
echo templates\
echo static\
echo.
echo data、screenshots、logs 不随程序打包，
echo 将在程序运行时自动创建。
echo ========================================
pause
exit /b 0

:failed
echo.
echo ========================================
echo [错误] 打包失败！
echo 请根据上方提示检查错误。
echo ========================================
pause
exit /b 1
