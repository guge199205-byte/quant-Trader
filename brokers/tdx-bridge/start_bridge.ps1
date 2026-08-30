param(
    [string]$Mode = "auto"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Config = Join-Path $ScriptDir "config.yaml"

Write-Host "[bridge-windows] 启动模式: $Mode"

# 确认通达信 17709 服务
$tdxOk = $false
try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:17709/" -Method Post `
        -ContentType "application/json; charset=utf-8" -TimeoutSec 3 `
        -Body '{"id":1,"method":"get_match_stkinfo","params":{"key_word":"茅台"}}'
    $tdxOk = $true
} catch {
    Write-Warning "[bridge-windows] 通达信 17709 不可达, 请确认 TdxW.exe 已运行并登录"
}

if (-not $env:BRIDGE_AUTH_TOKEN) {
    # 提示设置 token
    $env:BRIDGE_AUTH_TOKEN = Read-Host "请输入 BRIDGE_AUTH_TOKEN (64位hex)"
}
if (-not $env:SHARED_DIR) {
    $env:SHARED_DIR = Read-Host "请输入共享目录路径 (如 C:\new_tdx_mock\PYPlugins 或 Z:\tdx-shared)"
}

# 创建数据目录
$DataDir = Join-Path $ScriptDir "data"
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }

# 虚拟环境
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

Write-Host "[bridge-windows] 使用 Python: $Python"
& $Python -m main --mode $Mode --config $Config
