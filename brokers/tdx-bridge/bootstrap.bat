@echo off
title TDXBridge - Start
cd /d "%~dp0"
set MODE=%1
if "%MODE%"=="" set MODE=auto
echo [bridge-windows] Mode: %MODE%

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

if "%BRIDGE_AUTH_TOKEN%"=="" set /p BRIDGE_AUTH_TOKEN="BRIDGE_AUTH_TOKEN (64hex): "
if "%SHARED_DIR%"=="" set /p SHARED_DIR="Shared dir path (e.g. C:\new_tdx_mock\PYPlugins): "

%PY% -m main --mode %MODE% --config config.yaml
pause
