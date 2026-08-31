# ============================================================
#  通达信交易桥 - 强制重启（加载最新代码）
#
#  用法: 右键本文件 → 使用 PowerShell 运行
#  作用: 杀掉占用 8550 的旧桥进程, 用当前目录源码重新启动
# ============================================================
$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "[restart] 查找占用 8550 的进程..."
$conns = Get-NetTCPConnection -LocalPort 8550 -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[restart] 杀掉旧桥进程 PID=$($proc.Id) ($($proc.ProcessName))"
            Stop-Process -Id $proc.Id -Force
        }
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "[restart] 8550 无监听进程"
}

Write-Host "[restart] 启动新桥 (源码模式)..."
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
Write-Host "[restart] Python: $Python"

Start-Process -FilePath $Python -ArgumentList "-m main --mode auto --config config.yaml" `
    -WorkingDirectory $ScriptDir -PassThru | ForEach-Object {
    Write-Host "[restart] 桥已启动 PID=$($_.Id)"
}

Start-Sleep -Seconds 5
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8550/api/v1/health" -TimeoutSec 5
    Write-Host "[restart] 健康检查: $($health.status) | TDX 连通: $($health.tdx_connected)"
} catch {
    Write-Warning "[restart] 健康检查失败: $($_.Exception.Message)"
}

Write-Host "[restart] 完成。验证方向解析: 打开设置页看委托 side 应为 buy/sell"
Read-Host "按回车关闭窗口"
