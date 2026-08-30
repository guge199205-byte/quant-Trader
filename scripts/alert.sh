#!/bin/bash
# Trade Agent 告警检查：服务掉线 / 交易停滞 / 备份过期
# cron: */5 * * * * bash /home/zbox/BayMax-Trader/scripts/alert.sh

cd "$(dirname "$0")/.."

ALERT_LOG="/home/zbox/backups/baymax/alerts.log"
WEBHOOK_URL="${ALERT_WEBHOOK:-}"   # 可选：钉钉/飞书/Server酱 webhook
mkdir -p "$(dirname "$ALERT_LOG")"

ALERTS=""

# 1. 服务探活
for spec in "api:8091" "mcp_us:8100" "mcp_cn:8200" "mcp_hk:8300" "dsh:3081"; do
    name="${spec%%:*}"; port="${spec##*:}"
    if ! timeout 3 bash -c "echo > /dev/tcp/127.0.0.1/$port" 2>/dev/null; then
        ALERTS="$ALERTS
🔴 服务 $name ($port) 掉线"
    fi
done

# 2. 交易停滞检测（所有市场 48h 无新交易记录）
NOW=$(date +%s)
for market_dir in data/agent_data data/agent_data_astock data/agent_data_hk; do
    latest=0
    for pf in $market_dir/*/position/position.jsonl; do
        [ -f "$pf" ] || continue
        mt=$(stat -c %Y "$pf" 2>/dev/null || echo 0)
        [ "$mt" -gt "$latest" ] && latest=$mt
    done
    if [ "$latest" -gt 0 ] && [ $((NOW - latest)) -gt 172800 ]; then
        ALERTS="$ALERTS
🟡 $market_dir 已 $(( (NOW - latest) / 3600 )) 小时无新交易"
    fi
done

# 3. 备份过期检测（>26h 无备份）
latest_bak=$(ls -t /home/zbox/backups/baymax/baymax-*.tar.gz 2>/dev/null | head -1)
if [ -z "$latest_bak" ] || [ $((NOW - $(stat -c %Y "$latest_bak" 2>/dev/null || echo 0))) -gt 93600 ]; then
    ALERTS="$ALERTS
🟡 备份过期（>26h）"
fi

if [ -n "$ALERTS" ]; then
    MSG="[Trade Agent $(date '+%Y-%m-%d %H:%M')]$ALERTS"
    echo "$MSG" >> "$ALERT_LOG"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -s -m 10 -H "Content-Type: application/json" \
            -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$MSG\"}}" \
            "$WEBHOOK_URL" >/dev/null 2>&1 || true
    fi
    echo "$MSG"
fi

# 更新宿主侧探活结果（供 api /api/metrics 消费，避免容器内探活误报）
bash scripts/status-probe.sh
