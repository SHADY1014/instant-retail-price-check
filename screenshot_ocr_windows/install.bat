@echo off
cd /d "%~dp0"

echo ========================================
echo   Meituan OCR System - Installer
echo ========================================
echo.

REM ============================================================
REM Step 0: Install VC++ Redistributable (needed by onnxruntime)
REM       Put vc_redist.x64.exe in this folder to auto-install.
REM       Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
REM ============================================================
echo [0/4] Checking VC++ Runtime...
set "VCRED="
for %%f in (vc_redist.x64.exe) do set "VCRED=%%f"

if defined VCRED (
    echo   Found: %VCRED%
    echo   Installing VC++ Runtime silently...
    start /wait "" "%~dp0%VCRED%" /install /quiet /norestart
    echo   VC++ Runtime install finished (exit code: %errorlevel%)
) else (
    echo   No vc_redist.x64.exe in folder, skipping.
    echo   If OCR fails with DLL error, put vc_redist.x64.exe here and re-run.
)

REM ============================================================
REM Step 1: Find Python (3.9 - 3.12 only)
REM ============================================================
echo [1/4] Checking Python...

set "PYTHON="
python --version >nul 2>&1
if %errorlevel% equ 0 set "PYTHON=python"

if not defined PYTHON (
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
        if exist "%%d\python.exe" set "PYTHON=%%d\python.exe"
    )
)
if not defined PYTHON (
    for /d %%d in ("C:\Python*") do (
        if exist "%%d\python.exe" set "PYTHON=%%d\python.exe"
    )
)

if defined PYTHON goto :check_version

REM ---- Python not found: look for installer in this folder ----
set "INSTALLER="
for %%f in (python-3.*-amd64.exe) do set "INSTALLER=%%f"

if defined INSTALLER goto :install_python

echo.
echo [ERROR] Python not found and no installer in this folder!
echo.
echo Please put python-3.11.9-amd64.exe in this folder:
echo   https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
echo.
pause
exit /b 1

:install_python
echo   Found installer: %INSTALLER%
echo   Installing Python silently, please wait (about 1 minute)...
start /wait "" "%~dp0%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_doc=0 Include_test=0 Shortcuts=0
echo   Installer finished (exit code: %errorlevel%)

REM Find the freshly installed python
set "PYTHON="
set "VERFULL=%INSTALLER:python-=%
set "VERFULL=%VERFULL:-amd64.exe=%
for /f "tokens=1,2 delims=." %%a in ("%VERFULL%") do set "PYDIR=Python%%a%%b"

if exist "%LOCALAPPDATA%\Programs\Python\%PYDIR%\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\%PYDIR%\python.exe"
if not defined PYTHON (
    if exist "%LOCALAPPDATA%\Programs\Python\%PYDIR%-32\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\%PYDIR%-32\python.exe"
)
if not defined PYTHON (
    echo.
    echo [ERROR] Could not locate installed Python.
    echo   Please run %INSTALLER% manually (double-click it)
    echo   and make sure "Add Python to PATH" is checked.
    echo.
    pause
    exit /b 1
)

:check_version
"%PYTHON%" --version
"%PYTHON%" -c "import sys; v=sys.version_info[:2]; sys.exit(0 if (3,9)<=v<=(3,12) else 1)"
if %errorlevel% neq 0 goto :version_error
echo   Python version OK.
goto :install_deps

:version_error
echo.
echo [ERROR] Python version not supported (need 3.9 - 3.12)!
echo.
echo Please download python-3.11.9-amd64.exe into this folder:
echo   https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
echo.
echo Then re-run install.bat.
echo.
pause
exit /b 1

REM ============================================================
REM Step 2: Install dependencies (RapidOCR + PyQt5, CN mirrors)
REM       RapidOCR bundles PP-OCRv4 models - no extra download
REM ============================================================
:install_deps
echo.
echo [2/4] Upgrading pip...
"%PYTHON%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo [3/4] Installing RapidOCR, PyQt5, openpyxl, Pillow...
"%PYTHON%" -m pip install "onnxruntime==1.20.1" rapidocr_onnxruntime PyQt5 openpyxl Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo   [WARN] Tsinghua mirror failed, trying Aliyun...
    "%PYTHON%" -m pip install "onnxruntime==1.20.1" rapidocr_onnxruntime PyQt5 openpyxl Pillow -i https://mirrors.aliyun.com/pypi/simple/
)
if %errorlevel% neq 0 (
    echo [ERROR] Dependencies install failed. Check your network.
    pause
    exit /b 1
)

REM ============================================================
REM Step 4: Verify OCR engine loads correctly
REM ============================================================
echo.
echo [4/4] Verifying OCR engine...
"%PYTHON%" -c "from rapidocr_onnxruntime import RapidOCR; ocr = RapidOCR(); print('OCR engine OK')"
if %errorlevel% neq 0 (
    echo.
    echo [WARN] OCR engine check failed, but installation continues.
    echo        Please re-run install.bat if OCR fails.
)

echo.
echo ========================================
echo   Installation complete!
echo.
echo   Double-click start.bat to run
echo ========================================
echo.
pause
