#!/bin/bash
# 补跑 deepseek-v4-pro 三市场历史（用临时 config 只启 pro，避免 flash 重跑）
# 用法: bash scripts/backfill_pro.sh [INIT_DATE END_DATE]
# 默认补跑 08-24 ~ 08-28（与 flash 现有历史对齐）
set -e
cd "$(dirname "$0")/.."

INIT="${1:-2026-08-24}"
END="${2:-2026-08-28}"

# 市场 → 正式 config → compose service
declare -A CFG=(
  [us]="configs/deepseek_us_test.json"
  [cn]="configs/astock_config.json"
  [hk]="configs/deepseek_hk_test.json"
)
declare -A SVC=( [us]="agent-us" [cn]="agent-cn" [hk]="agent-hk" )

for mkt in us cn hk; do
  src="${CFG[$mkt]}"
  tmp="configs/.tmp_pro_only_${mkt}.json"
  # 临时 config：只保留 deepseek-v4-pro（enabled）
  python3 - "$src" "$tmp" << 'PYEOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
c = json.load(open(src))
c["models"] = [m for m in c["models"] if m.get("name") == "deepseek-v4-pro"]
for m in c["models"]:
    m["enabled"] = True
json.dump(c, open(dst, "w"), ensure_ascii=False, indent=1)
PYEOF

  echo "===== 补跑 $mkt v4-pro ($INIT ~ $END) ====="
  docker compose --profile agents run --rm \
    -e "INIT_DATE=$INIT" -e "END_DATE=$END" \
    "${SVC[$mkt]}" python main.py "$tmp" || {
      echo "❌ $mkt 补跑失败"
      rm -f "$tmp"
      exit 1
    }
  rm -f "$tmp"
done

echo "🎉 v4-pro 三市场补跑完成"
