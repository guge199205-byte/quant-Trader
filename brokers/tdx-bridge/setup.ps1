# ============================================================
#  通达信量化交易桥 - Windows 一键启动脚本 (PowerShell)
#  功能: 检测Python / 安装依赖 / 设置环境变量
#        自动放行防火墙8550端口 / 启动桥
#  用法: 右键"使用 PowerShell 运行", 或在命令行执行
#    .\setup.ps1 auto          (默认, HTTP+文件双通道)
#    .\setup.ps1 http          (仅 HTTP 通道)
#    .\setup.ps1 file_sync     (仅文件通道)
#  注意: 本文件必须保存为 UTF-8 with BOM, 否则中文会乱码
# ============================================================
param(
    [string]$Mode = "auto"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  通达信量化交易桥 - 一键启动" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  启动模式: $Mode"
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ---- [1] 检测 Python ----
Write-Host "[1/7] 检测 Python 环境..." -ForegroundColor Yellow
$PY = $null
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) { $PY = "python" }
else {
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) { $PY = "py" }
}
if (-not $PY) {
    Write-Host "[错误] 未找到 Python, 请先安装 Python 3.9+" -ForegroundColor Red
    Write-Host "  下载地址: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  安装时务必勾选 'Add Python to PATH'" -ForegroundColor Yellow
    Read-Host "按回车退出"
    exit 1
}
Write-Host "      Python: $PY"
& $PY --version

# ---- [2] 安装依赖 ----
Write-Host "[2/7] 安装依赖包..." -ForegroundColor Yellow
& $PY -m pip install --quiet --upgrade pip
& $PY -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 依赖安装失败, 请检查网络后重试" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}
Write-Host "      桥依赖安装完成 (aiohttp/pyyaml)"

# ---- [2.5] 给通达信 TQ 的 Python 安装 numpy/pandas ----
Write-Host "[2.5/7] 检测并配置通达信 TQ 的 Python 环境 (numpy/pandas)..." -ForegroundColor Yellow
$tdxPythons = @()
# 常见通达信捆绑 Python 路径
$tdxRoots = @("C:\new_tdx_mock", "C:\new_tdx", "C:\通达信金融终端64",
    "C:\Program Files\通达信", "C:\tdx", "D:\new_tdx")
$foundRoot = $null
foreach ($root in $tdxRoots) {
    if (Test-Path $root) { $foundRoot = $root; break }
}
if (-not $foundRoot) {
    # 尝试从共享脚本目录反推 (脚本在 PYPlugins 里, 上级是通达信安装目录)
    $candidate = Split-Path $ScriptDir -Parent
    if (Test-Path $candidate) { $foundRoot = $candidate }
}

if ($foundRoot) {
    Write-Host "      检测到通达信目录: $foundRoot"
    # 搜索其中的 python.exe (捆绑 Python 常见于 PYPlugins\python 或根目录)
    $pythonCandidates = @(
        (Join-Path $foundRoot "PYPlugins\python\python.exe"),
        (Join-Path $foundRoot "python\python.exe"),
        (Join-Path $foundRoot "PYPlugins\TQPython\python.exe"),
        (Join-Path $foundRoot "tqpython\python.exe")
    )
    foreach ($p in $pythonCandidates) {
        if (Test-Path $p) { $tdxPythons += $p }
    }
    # 也尝试递归搜索 (限深, 避免太慢)
    if ($tdxPythons.Count -eq 0) {
        try {
            $tdxPythons += Get-ChildItem -Path $foundRoot -Filter "python.exe" `
                -Recurse -Depth 3 -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
        } catch {}
    }
} else {
    Write-Host "      未找到通达信目录, 回退用系统 Python 装 numpy/pandas" -ForegroundColor Yellow
}

# 去重 + 给每个 python 装 numpy/pandas
$uniquePythons = $tdxPythons | Select-Object -Unique
if ($uniquePythons.Count -eq 0) {
    # 没找到捆绑 python, 用系统 python 装 (通达信 TQ 可能就用系统 python)
    Write-Host "      未找到通达信捆绑 Python, 用系统 Python 安装 numpy/pandas" -ForegroundColor Yellow
    & $PY -m pip install --quiet numpy pandas
} else {
    foreach ($tp in $uniquePythons) {
        Write-Host "      给通达信 Python 装 numpy/pandas: $tp" -ForegroundColor DarkCyan
        & $tp -m pip install --quiet numpy pandas 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "        ✅ $tp 已安装 numpy/pandas" -ForegroundColor Green
        } else {
            Write-Host "        ⚠️ $tp 安装失败, 请手动执行: `"$tp`" -m pip install numpy pandas" -ForegroundColor Yellow
        }
    }
}
Write-Host "      TQ Python 环境配置完成"

# ---- [3] 环境变量 ----
Write-Host "[3/7] 检查环境变量..." -ForegroundColor Yellow
if (-not $env:BRIDGE_AUTH_TOKEN) {
    $env:BRIDGE_AUTH_TOKEN = Read-Host "请输入 BRIDGE_AUTH_TOKEN (64位hex, 与Linux侧一致)"
}
if (-not $env:SHARED_DIR) {
    # 默认指向本脚本所在目录
    $env:SHARED_DIR = $ScriptDir
}
Write-Host "      token: $($env:BRIDGE_AUTH_TOKEN.Substring(0, [Math]::Min(8, $env:BRIDGE_AUTH_TOKEN.Length)))..."
Write-Host "      共享目录: $env:SHARED_DIR"

# ---- [4] 防火墙放行 8550 ----
Write-Host "[4/7] 配置防火墙, 放行 8550 端口..." -ForegroundColor Yellow
$HTTP_PORT = 8550
$FW_RULE_NAME = "TDXBridge-8550"
try {
    netsh advfirewall firewall delete rule name="$FW_RULE_NAME" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="$FW_RULE_NAME" `
        dir=in action=allow protocol=TCP localport=$HTTP_PORT `
        description="通达信交易桥 HTTP 通道 (QuantMind 访问)" 2>$null | Out-Null
    Write-Host "      防火墙已放行 TCP $HTTP_PORT (规则: $FW_RULE_NAME)" -ForegroundColor Green
} catch {
    Write-Host "[警告] 自动添加防火墙规则失败, 请手动放行:" -ForegroundColor Yellow
    Write-Host "      控制面板 - 防火墙 - 高级设置 - 入站规则 - 新建 - 端口 8550" -ForegroundColor Yellow
}

# ---- [5] 检测通达信 ----
Write-Host "[5/7] 检测通达信客户端..." -ForegroundColor Yellow
$tdxRunning = Get-Process TdxW -ErrorAction SilentlyContinue
if (-not $tdxRunning) {
    Write-Host "[警告] 未检测到 TdxW.exe 运行, 请先启动通达信并登录交易账号" -ForegroundColor Yellow
    Write-Host "       桥启动后会尝试连接 127.0.0.1:17709" -ForegroundColor Yellow
} else {
    Write-Host "      检测到通达信正在运行" -ForegroundColor Green
}

# ---- [6] 启动桥 ----
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  启动通达信交易桥..." -ForegroundColor Cyan
Write-Host "  请保持本窗口开启, 关闭窗口即停止桥" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

& $PY -m main --mode $Mode --config config.yaml
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[错误] 桥异常退出" -ForegroundColor Red
    Read-Host "按回车退出"
}
