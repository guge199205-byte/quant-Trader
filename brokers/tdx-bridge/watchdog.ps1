# ============================================================
#  通达信交易桥 - 看门狗 (自动重启)
#
#  功能:
#    - 每 30 秒检查桥 HTTP 健康状态
#    - 桥无响应/崩溃 → 自动重新启动
#    - 启动后自动打开控制台
#    - 崩溃记录到日志
#
#  注册为服务(推荐):
#    .\register_watchdog.bat    # 安装为 Windows 服务, 开机自启
#
#  或后台运行:
#    Start-Process powershell -ArgumentList '-File watchdog.ps1' -WindowStyle Hidden
# ============================================================
param(
    [int]$CheckIntervalSec = 30,   # 健康检查间隔
    [int]$StartupDelaySec = 5,     # 首次启动延迟
    [string]$BridgeUrl = "http://127.0.0.1:8550/api/v1/health",
    [string]$Mode = "auto"
)

$ErrorActionPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogFile = Join-Path $ScriptDir "data\logs\watchdog.log"
$BridgeLog = Join-Path $ScriptDir "data\logs\bridge.log"
$Python = "python"
if (Test-Path (Join-Path $ScriptDir ".venv\Scripts\python.exe")) {
    $Python = Join-Path $ScriptDir ".venv\Scripts\python.exe"
}

# 确保日志目录存在
$LogDir = Split-Path $LogFile -Parent
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function Test-BridgeAlive {
    try {
        $resp = Invoke-WebRequest -Uri $BridgeUrl -TimeoutSec 5 -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-Bridge {
    Write-Log "正在启动通达信交易桥..."
    try {
        # 设置环境变量
        $env:SHARED_DIR = if ($env:SHARED_DIR) { $env:SHARED_DIR } else { $ScriptDir }
        if (-not $env:BRIDGE_AUTH_TOKEN -and (Test-Path (Join-Path $ScriptDir "config.yaml"))) {
            # 尝试从环境读取; 若无则提示 (首次需手动设置)
            Write-Log "警告: BRIDGE_AUTH_TOKEN 未设置, 桥可能鉴权失败"
        }
        # 后台启动桥 (windowed 模式, 不占用本脚本窗口)
        $proc = Start-Process -FilePath $Python -ArgumentList "-m main --mode $Mode --config config.yaml" `
            -WorkingDirectory $ScriptDir -WindowStyle Hidden -PassThru
        Write-Log "桥已启动, PID=$($proc.Id)"
        return $proc
    } catch {
        Write-Log "启动桥失败: $($_.Exception.Message)"
        return $null
    }
}

Write-Log "============================================"
Write-Log "通达信交易桥看门狗启动"
Write-Log "检查地址: $BridgeUrl"
Write-Log "检查间隔: ${CheckIntervalSec}s"
Write-Log "============================================"

# 首次启动桥
Start-Sleep -Seconds $StartupDelaySec
Start-Bridge

$deadCount = 0
while ($true) {
    Start-Sleep -Seconds $CheckIntervalSec
    $alive = Test-BridgeAlive
    if ($alive) {
        if ($deadCount -gt 0) {
            Write-Log "桥已恢复健康"
            $deadCount = 0
        }
        continue
    }

    # 桥无响应
    $deadCount++
    Write-Log "桥无响应 (连续 $deadCount 次)"

    # 连续 2 次无响应才重启 (避免短暂抖动)
    if ($deadCount -ge 2) {
        Write-Log "桥疑似崩溃, 正在重启..."
        # 杀掉旧的 python 进程 (只杀 bridge 相关的, 通过端口判断)
        try {
            $conns = Get-NetTCPConnection -LocalPort 8550 -State Listen -ErrorAction SilentlyContinue
            foreach ($c in $conns) {
                $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Log "杀掉旧桥进程 PID=$($proc.Id)"
                    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {}
        Start-Sleep -Seconds 3
        Start-Bridge
        $deadCount = 0
    }
}
