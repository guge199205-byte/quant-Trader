#!/bin/bash
# 多市场并行交易启动器：US（美股）+ CN（A股）同时跑
#
# 架构：每市场一组独立 MCP 服务（端口隔离）+ 独立 runtime_env（状态隔离）
#   US: MCP 8100-8103, runtime_env.json, 数据 data/agent_data/
#   CN: MCP 8200-8203, runtime_env_cn.json, 数据 data/agent_data_astock/
#
# 用法:
#   bash scripts/run_multi_market.sh                 # 起服务 + 两市场 agent
#   bash scripts/run_multi_market.sh --services-only # 只起 MCP 服务组
#   bash scripts/run_multi_market.sh --agents-only   # 只起 agent（需服务已就绪）

set -e
cd "$(dirname "$0")/.."
export PATH="/home/zbox/BayMax-Trader/.venv/bin:$PATH"

US_CFG="${US_CFG:-configs/deepseek_us_test.json}"
CN_CFG="${CN_CFG:-configs/astock_config.json}"
HK_CFG="${HK_CFG:-configs/deepseek_hk_test.json}"

start_us() {
    echo "🇺🇸 启动 US MCP 服务 (8100-8103)..."
    cd agent_tools
    nohup python start_mcp_services.py > /tmp/baymax-mcp-us.log 2>&1 &
    cd ..
    sleep 3
}

start_cn() {
    echo "🇨🇳 启动 CN MCP 服务 (8200-8204, runtime_env_cn.json)..."
    cd agent_tools
    MATH_HTTP_PORT=8200 SEARCH_HTTP_PORT=8201 TRADE_HTTP_PORT=8202 GETPRICE_HTTP_PORT=8203 MEMORY_HTTP_PORT=8204 \
    RUNTIME_ENV_PATH="/home/zbox/BayMax-Trader/runtime_env_cn.json" \
    nohup python start_mcp_services.py > /tmp/baymax-mcp-cn.log 2>&1 &
    cd ..
    sleep 3
}

start_hk() {
    echo "🇭🇰 启动 HK MCP 服务 (8300-8304, runtime_env_hk.json)..."
    cd agent_tools
    MATH_HTTP_PORT=8300 SEARCH_HTTP_PORT=8301 TRADE_HTTP_PORT=8302 GETPRICE_HTTP_PORT=8303 MEMORY_HTTP_PORT=8304 \
    RUNTIME_ENV_PATH="/home/zbox/BayMax-Trader/runtime_env_hk.json" \
    nohup python start_mcp_services.py > /tmp/baymax-mcp-hk.log 2>&1 &
    cd ..
    sleep 3
}

run_us_agent() {
    echo "🇺🇸 启动 US agent: $US_CFG"
    nohup python main.py "$US_CFG" > /tmp/baymax-agent-us.log 2>&1 &
}

run_cn_agent() {
    echo "🇨🇳 启动 CN agent: $CN_CFG"
    RUNTIME_ENV_PATH="/home/zbox/BayMax-Trader/runtime_env_cn.json" \
    nohup python main.py "$CN_CFG" > /tmp/baymax-agent-cn.log 2>&1 &
}

run_hk_agent() {
    echo "🇭🇰 启动 HK agent: $HK_CFG"
    RUNTIME_ENV_PATH="/home/zbox/BayMax-Trader/runtime_env_hk.json" \
    nohup python main.py "$HK_CFG" > /tmp/baymax-agent-hk.log 2>&1 &
}

case "$1" in
    --services-only) start_us; start_cn; start_hk ;;
    --agents-only)   run_us_agent; run_cn_agent; run_hk_agent ;;
    *)               start_us; start_cn; start_hk; sleep 2; run_us_agent; run_cn_agent; run_hk_agent ;;
esac

echo "✅ 完成。服务/进程日志: /tmp/baymax-*.log"
