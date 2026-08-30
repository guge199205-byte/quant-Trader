@echo off
title TDXBridge - Register Watchdog
echo ============================================
echo  TDX Trading Bridge - Register Auto-Restart
echo  (Run as Administrator!)
echo ============================================
echo.

cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Administrator privileges required!
    echo Right-click this file, select "Run as administrator"
    pause
    exit /b 1
)

echo [1/2] Register Windows Task...
schtasks /Delete /TN "TDXBridgeWatchdog" /F >nul 2>&1
schtasks /Create /TN "TDXBridgeWatchdog" ^
    /TR "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%~dp0watchdog.ps1\"" ^
    /SC ONSTART ^
    /RL HIGHEST ^
    /F >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Task create failed
    pause
    exit /b 1
)
echo       Task created: TDXBridgeWatchdog

echo [2/2] Start watchdog now...
schtasks /Run /TN "TDXBridgeWatchdog" >nul 2>&1

echo.
echo ============================================
echo  Watchdog running!
echo  - Auto-start on boot
echo  - Check bridge health every 30s
echo  - Auto-restart bridge on crash
echo ============================================
echo.
echo  Manage:
echo    Start:  schtasks /Run /TN "TDXBridgeWatchdog"
echo    Stop:   schtasks /End /TN "TDXBridgeWatchdog"
echo    Remove: schtasks /Delete /TN "TDXBridgeWatchdog" /F
echo.
pause
