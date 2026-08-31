#!/bin/bash
# BayMax 容器自愈：常驻服务掉线自动拉起（每分钟 cron）
# 覆盖：mcp×3 / api / dsh / ui-arena（agent 是按需任务，不自动拉起）
# 与 status-probe.sh / alert.sh 同频，保证"断了就补上"，交易中间不中断
cd "$(dirname "$0")/.." || exit 1

LOG="logs/auto-heal.log"
: >> "$LOG"

for svc in mcp-us mcp-cn mcp-hk api dsh ui-arena; do
    if ! docker inspect -f '{{.State.Running}}' "baymax-$svc" 2>/dev/null | grep -q true; then
        echo "$(date '+%F %T') [heal] baymax-$svc 掉线，拉起中..." >> "$LOG"
        docker compose up -d "$svc" >> "$LOG" 2>&1
    fi
done
