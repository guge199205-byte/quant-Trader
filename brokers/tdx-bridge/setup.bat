@echo off
title TDXBridge - Auto Setup
echo ============================================
echo  TDX Trading Bridge - One-click Setup
echo  (Deps + Firewall + Start bridge)
echo ============================================
echo.

cd /d "%~dp0"
set MODE=%1
if "%MODE%"=="" set MODE=auto
echo [1/6] Mode: %MODE%

set PY=python
where python >nul 2>nul && set PY=python
if not defined PY (
    where py >nul 2>nul && set PY=py
)
if not defined PY (
    echo [ERROR] Python not found. Install Python 3.9+ with "Add to PATH"
    pause
    exit /b 1
)
echo       Python: %PY%
%PY% --version

echo [2/6] Installing deps...
%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Deps install failed
    pause
    exit /b 1
)
echo       Deps OK

echo [3/6] Env vars...
if not defined BRIDGE_AUTH_TOKEN (
    set /p BRIDGE_AUTH_TOKEN="Enter BRIDGE_AUTH_TOKEN (64hex, same as Linux): "
)
if not defined SHARED_DIR (
    set "SHARED_DIR=%~dp0"
)
echo       token: %BRIDGE_AUTH_TOKEN:~0,8%...
echo       shared: %SHARED_DIR%

echo [4/6] Firewall port 8550...
set HTTP_PORT=8550
set FW_RULE_NAME=TDXBridge-8550
netsh advfirewall firewall delete rule name="%FW_RULE_NAME%" >nul 2>nul
netsh advfirewall firewall add rule name="%FW_RULE_NAME%" ^
    dir=in action=allow protocol=TCP localport=%HTTP_PORT% >nul 2>nul
echo       Firewall OK

echo [5/6] Checking TDX client...
tasklist | find /i "TdxW.exe" >nul 2>nul
if errorlevel 1 (
    echo [WARN] TdxW.exe not running. Start TDX and login first.
)

echo.
echo ============================================
echo  Starting TDX Bridge...
echo  Keep this window open. Close to stop.
echo ============================================
echo.

%PY% -m main --mode %MODE% --config config.yaml
if errorlevel 1 (
    echo.
    echo [ERROR] Bridge exited abnormally
    pause
)
