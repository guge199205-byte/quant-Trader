#!/bin/bash
# 启动 DeepSeek Harness (dsh) 作为 BayMax-Trader 的 agent 引擎
# 用法: bash scripts/start_dsh.sh [--port 3081]

set -e
cd "$(dirname "$0")/.."

PORT=3081
if [ "$1" = "--port" ] && [ -n "$2" ]; then
    PORT="$2"
fi

# 加载 .env（DEEPSEEK_API_KEY 等）
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

DSH_BIN="${DSH_BIN:-$HOME/.local/bin/dsh}"
if [ ! -x "$DSH_BIN" ]; then
    echo "❌ 未找到 dsh，先安装: npm install -g --prefix ~/.local @deepseek-ai/dsh"
    exit 1
fi

# 检查 MCP 服务
for port in 8100 8101 8102 8103; do
    if ! curl -s -o /dev/null --max-time 2 "http://localhost:$port/mcp"; then
        echo "⚠️  MCP 服务 $port 未响应，先启动: .venv/bin/python agent_tools/start_mcp_services.py"
    fi
done

echo "🚀 启动 dsh web (端口 $PORT, MCP: baymax_math/search/trade/price)"
echo "🔗 http://localhost:$PORT"
# --patch 是 dsh 顶层选项，须放在 profile 之前
exec "$DSH_BIN" --profile web --patch "$PWD/dsh/baymax.cordis.yml" --port "$PORT" --no-open
