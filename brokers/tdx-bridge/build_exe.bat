@echo off
title TDXBridge - Build EXE
echo ============================================
echo  TDX Trading Bridge - Build EXE Package
echo  (Source code will be compiled to binary)
echo ============================================
echo.

cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py
echo [1/4] Checking Python: %PY%
%PY% --version
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.9+
    pause
    exit /b 1
)

echo [2/4] Installing PyInstaller...
%PY% -m pip install --quiet pyinstaller
if errorlevel 1 (
    echo [ERROR] PyInstaller install failed
    pause
    exit /b 1
)

echo [2.5/4] Installing runtime deps (aiohttp/pyyaml)...
%PY% -m pip install --quiet -r requirements.txt

echo [3/4] Building EXE (onefile, windowed)...
%PY% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "TDXBridge" ^
    --add-data "src\static\ui.html;static" ^
    --hidden-import aiohttp ^
    --hidden-import yaml ^
    --collect-all aiohttp ^
    --noconfirm ^
    --clean ^
    main.py

if errorlevel 1 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)

echo [4/4] Build complete!
echo.
echo ============================================
echo  EXE generated: dist\TDXBridge.exe
echo  Double-click to run, auto-opens web console
echo  Source compiled to binary, no source leak
echo ============================================
pause
