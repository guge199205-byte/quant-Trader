#!/bin/bash
# 补跑 US agent 08-25/26/27（position.jsonl 已有 08-24 初始 + 08-28 真实交易）。
# 逻辑：暂时移走 08-28 行（否则 get_trading_dates 的 max_date=08-28 会返回空列表），
#       逐天补跑（INIT_DATE=END_DATE=当天），最后恢复 08-28 行（追加到文件尾）。
# 用法: bash scripts/backfill_us_agent.sh
set -euo pipefail
cd "$(dirname "$0")/.."

POS=data/agent_data/deepseek-v4-flash/position/position.jsonl
TS=$(date +%s)
CFG=configs/deepseek_us_test.json   # 真实运行用的配置（deepseek-v4-flash）；default_config 已是 gpt-5 勿用

# 0) 前置检查：价格数据必须已更新（最后一笔交易日 08-28 有价格才算齐）
grep -q '"2026-08-28"' data/daily_prices_AAPL.json || { echo "❌ 价格未更新到 08-28，先跑 scripts/update_prices.py"; exit 1; }

# 1) 备份 + 移走 08-28 行（trap 保证任何失败都自动恢复，防止数据丢失）
cp "$POS" "/tmp/us_position_full_${TS}.bak"
grep -v '"date": "2026-08-28"' "$POS" > /tmp/us_pos_now.jsonl
grep '"date": "2026-08-28"' "$POS" > "/tmp/us_pos_0828_${TS}.jsonl" || true
cp /tmp/us_pos_now.jsonl "$POS"
trap 'grep -q "\"date\": \"2026-08-28\"" "$POS" 2>/dev/null || cat "/tmp/us_pos_0828_${TS}.jsonl" >> "$POS"' EXIT
echo "🔒 已备份全文件 -> /tmp/us_position_full_${TS}.bak；移走 08-28 行（$(wc -l < /tmp/us_pos_now.jsonl) 行保留）"

# 2) 逐天补跑（显式传配置 + 覆盖 command，跳过 compose 默认的 default_config.json）
for D in 2026-08-25 2026-08-26 2026-08-27; do
  echo "==== 补跑 $D ===="
  docker compose --profile agents run --rm -e "INIT_DATE=$D" -e "END_DATE=$D" agent-us python main.py "$CFG"
  echo "尾部: $(tail -1 "$POS" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["date"], "id="+str(d.get("id",-1)), "CASH="+str(d["positions"].get("CASH")))')"
done

# 3) 恢复 08-28 行（trap 在 EXIT 时兜底；此处显式执行一次，再清空 trap 防重复）
grep -q '"date": "2026-08-28"' "$POS" || cat "/tmp/us_pos_0828_${TS}.jsonl" >> "$POS"
trap - EXIT
echo "✅ 08-28 行已恢复，文件尾部日期:"
tail -1 "$POS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["date"])'
echo "ℹ️  恢复文件含 08-28 行数: $(grep -c '"date": "2026-08-28"' "$POS")"
