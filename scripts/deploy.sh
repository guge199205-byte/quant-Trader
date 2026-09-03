#!/bin/bash
# ============================================================
# 一键部署（P0-1）：前端三件套 / 后端热更
# 用法:
#   scripts/deploy.sh            # 全部（前端 + 后端）
#   scripts/deploy.sh --ui       # 仅前端
#   scripts/deploy.sh --api      # 仅后端
# 前端：vite build → index+JS+CSS 三件套原子写入容器 → 引用资源 200 校验
# 后端：py_compile → 拷贝 api_server.py → docker restart baymax-api → 探活
# ============================================================
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
UI=1; API=1
[ "$1" = "--ui" ] && API=0
[ "$1" = "--api" ] && UI=0

# ---------- 前端 ----------
if [ "$UI" = 1 ]; then
  echo "==> 构建前端…"
  (cd arena && npx tsc --noEmit && npx vite build >/dev/null)
  JS=$(ls arena/dist/assets/index-*.js | head -1 | xargs basename)
  CSS=$(ls arena/dist/assets/index-*.css | head -1 | xargs basename)
  echo "==> 部署三件套: $JS $CSS"
  for f in index.html assets/$JS assets/$CSS; do
    docker exec -i baymax-ui-arena sh -c "cat > /usr/share/nginx/html/$f" < "arena/dist/$f"
  done
  # 引用校验（防 JS 404 白屏重演）
  REF=$(curl -s --max-time 6 http://127.0.0.1:8092/ | grep -o 'assets/index-[^"]*\.\(js\|css\)' | sort -u)
  for r in $REF; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:8092/$r")
    echo "   $r → $code"
    [ "$code" != 200 ] && echo "!! 资源校验失败" && exit 1
  done
  echo "==> 前端完成"
fi

# ---------- 后端 ----------
if [ "$API" = 1 ]; then
  echo "==> 部署后端…"
  python3 -m py_compile backend/api_server.py
  docker exec -i baymax-api sh -c 'cat > /app/backend/api_server.py' < backend/api_server.py
  docker restart baymax-api >/dev/null
  echo -n "==> 等待 api…"
  for i in $(seq 1 12); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 -H "X-API-Token: $(sed -n 's/^API_TOKEN="\?\([^"]*\)"\?$/\1/p' .env | head -1)" http://127.0.0.1:8091/api/status 2>/dev/null || echo 000)
    [ "$code" = 200 ] && echo " OK" && break
    [ "$i" = 12 ] && echo " FAIL(api 未探活成功)" && exit 1
    sleep 5
  done
  echo "==> 后端完成"
fi
echo "✅ deploy.sh 全部完成"