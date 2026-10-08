#!/bin/bash
# BayMax 容器自愈：常驻服务掉线自动拉起（每分钟 cron）
# 覆盖：mcp×3 / api / dsh / ui-arena（agent 是按需任务，不自动拉起）
# 与 status-probe.sh / alert.sh 同频，保证"断了就补上"，交易中间不中断
cd "$(dirname "$0")/.." || exit 1

LOG="logs/auto-heal.log"
: >> "$LOG"

# 维护模式：/tmp/baymax-maintenance.lock 存在且未过期(<30min) → 计划内维护，跳过本轮。
# （迁移/重建等窗口用；锁自带超时兜底，忘记删锁也不会永久关闭自愈）
LOCK=/tmp/baymax-maintenance.lock
if [[ -f $LOCK ]] && (( $(date +%s) - $(stat -c %Y "$LOCK") < 1800 )); then
    echo "$(date '+%F %T') [heal] 维护模式(lock 未过期)，跳过本轮" >> "$LOG"
    exit 0
fi

for svc in mcp-us mcp-cn mcp-hk api dsh ui-arena; do
    if ! docker inspect -f '{{.State.Running}}' "baymax-$svc" 2>/dev/null | grep -q true; then
        echo "$(date '+%F %T') [heal] baymax-$svc 掉线，拉起中..." >> "$LOG"
        docker compose up -d "$svc" >> "$LOG" 2>&1
    fi
done
