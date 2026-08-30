@echo off
title TDXBridge - Rebuild EXE
echo ============================================
echo  TDX Trading Bridge - Clean Rebuild
echo ============================================
echo.

cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

REM ---- Backup existing config ----
echo [1/5] Backing up config...
set HAS_CFG=0
if exist dist\config.yaml (
    copy /y dist\config.yaml config.bak.yaml >nul
    set HAS_CFG=1
    echo       Backed up dist\config.yaml
) else if exist config.yaml (
    copy /y config.yaml config.bak.yaml >nul
    set HAS_CFG=1
    echo       Backed up config.yaml
)
if "%HAS_CFG%"=="0" echo       No config found, will use defaults

REM ---- Clean old build ----
echo [2/5] Cleaning old build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist TDXBridge.spec del /f /q TDXBridge.spec
for /d /r . %%d in (__pycache__) do rmdir /s /q "%%d" 2>nul
echo       Cleaned

REM ---- Build ----
echo [3/5] Building EXE...
%PY% -m PyInstaller --noconfirm --clean ^
    --onefile ^
    --windowed ^
    --name "TDXBridge" ^
    --add-data "src\static\ui.html;static" ^
    --hidden-import aiohttp ^
    --hidden-import yaml ^
    --collect-all aiohttp ^
    main.py

if errorlevel 1 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)

REM ---- Restore config ----
echo [4/5] Restoring config...
if exist config.bak.yaml (
    copy /y config.bak.yaml dist\config.yaml >nul
    del /f /q config.bak.yaml
    echo       Config restored
) else (
    if exist config.yaml copy /y config.yaml dist\config.yaml >nul
    echo       Config copied
)

echo [5/5] Done!
echo.
echo ============================================
echo  Build complete!
echo  EXE: dist\TDXBridge.exe
echo  Config: dist\config.yaml
echo ============================================
pause
