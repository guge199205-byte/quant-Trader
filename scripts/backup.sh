#!/bin/bash
# Trade Agent 每日备份：数据 + 配置 + 关键状态
# 用法: bash scripts/backup.sh            # 手动备份
#       crontab 加: 0 3 * * * bash /home/zbox/BayMax-Trader/scripts/backup.sh

set -e
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-/home/zbox/backups/baymax}"
KEEP_DAYS="${KEEP_DAYS:-7}"
STAMP=$(date +%Y%m%d-%H%M)
DEST="$BACKUP_DIR/baymax-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

tar czf "$DEST" \
    data/agent_data data/agent_data_astock data/agent_data_hk \
    data/HK_stock data/A_stock \
    configs config backend.yaml 2>/dev/null \
    .env .service.env \
    runtime_env.json runtime_env_cn.json runtime_env_hk.json 2>/dev/null \
    2>/dev/null || true

# 清理过期备份
find "$BACKUP_DIR" -name "baymax-*.tar.gz" -mtime +"$KEEP_DAYS" -delete 2>/dev/null

echo "✅ 备份完成: $DEST ($(du -h "$DEST" | cut -f1))"
echo "   保留最近 $KEEP_DAYS 天，目录: $BACKUP_DIR"
