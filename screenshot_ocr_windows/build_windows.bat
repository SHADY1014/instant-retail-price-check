@echo off
cd /d "%~dp0"

echo ========================================
echo   Build Price Check OCR EXE (Windows x64)
echo ========================================
echo.

REM ===== 1. Find Python =====
set "PYTHON=python"
%PYTHON% --version >nul 2>&1
if %errorlevel% neq 0 (
    set "PYTHON="
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%d\python.exe" set "PYTHON=%%d\python.exe"
    if not defined PYTHON (
        echo [ERROR] Python not found. Run install.bat first.
        pause
        exit /b 1
    )
)
"%PYTHON%" --version

REM ===== 2. Install build tools =====
echo.
echo [1/4] Installing PyInstaller...
"%PYTHON%" -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

REM ===== 3. Install runtime deps (needed for analysis) =====
echo.
echo [2/4] Ensuring dependencies...
"%PYTHON%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

REM ===== 4. Build EXE =====
echo.
echo [3/4] Building EXE (may take a few minutes)...
"%PYTHON%" -m PyInstaller --noconfirm --clean price_check_ocr.spec

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo [4/4] Creating SHA-256 release manifest...
"%PYTHON%" tools\create_release_manifest.py dist\PriceCheckOCR.exe --version-file VERSION --output dist\release-manifest.json
if %errorlevel% neq 0 (
    echo [ERROR] Release manifest generation failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Build complete!
echo.
echo   Output: dist\PriceCheckOCR.exe
echo   Manifest: dist\release-manifest.json
echo.
echo   Copy dist\PriceCheckOCR.exe to any Windows 10/11 x64 machine.
echo   First launch may be slow (self-extract); data saved to:
echo   %%LOCALAPPDATA%%\LQPriceCheck
echo ========================================
pause
