@echo off
cd /d "%~dp0"


REM Find Python - prefer 3.11/3.12 installed by install.bat,
REM fall back to PATH python
set "PYTHON="
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYTHON set "PYTHON=python"

%PYTHON% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please run install.bat first.
    pause
    exit /b 1
)

REM Check PyQt5 installed
"%PYTHON%" -c "import PyQt5" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Dependencies not installed.
    echo.
    echo Please run install.bat first, then try again.
    echo.
    pause
    exit /b 1
)

echo Starting Meituan OCR System...
"%PYTHON%" main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Program exited with code %errorlevel%
    echo.
    echo If first time use, please run install.bat first.
    echo.
    pause
)
