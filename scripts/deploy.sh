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
  python3 -m py_compile backend/api_server.py backend/services/market_lab.py backend/services/lab_batch.py backend/services/pine_library.py
  docker exec -i baymax-api sh -c 'cat > /app/backend/api_server.py' < backend/api_server.py
  # 行情实验室：服务模块 + 策略模板（Pyne 代码，运行时直接读文件）
  docker exec -i baymax-api sh -c 'cat > /app/backend/services/market_lab.py' < backend/services/market_lab.py
  # 批量回测：只读 data/lab_batch/*.json（宿主跑批产物，/app/data 已 rw 挂载）
  docker exec -i baymax-api sh -c 'cat > /app/backend/services/lab_batch.py' < backend/services/lab_batch.py
  # Pine 策略库：读 data/pine_library/index.json + source/（宿主 scripts/pine_library_index.py 生成）
  docker exec -i baymax-api sh -c 'cat > /app/backend/services/pine_library.py' < backend/services/pine_library.py
  docker exec -i baymax-api sh -c 'mkdir -p /app/backend/services/lab_strategies'
  # 清掉 PyneCore 自动生成的 <策略>.toml：里面的 value= 会在运行时覆盖 input 默认值，
  # 是「同一策略两次回测结果不同」的隐患源（market_lab 已设 PYNE_SAVE_SCRIPT_TOML=0）
  docker exec baymax-api sh -c 'rm -f /app/backend/services/lab_strategies/*.toml'
  for f in backend/services/lab_strategies/*.py; do
    docker exec -i baymax-api sh -c "cat > /app/backend/services/lab_strategies/$(basename "$f")" < "$f"
  done
  # 宿主 worker 与容器共写的目录：容器以 root 建目录、写文件，而 unlink/rename 只看
  # **目录**写权限——目录若被容器建成 root:755，宿主 worker 连摘牌都做不到
  # （2026-09-09 对话队列实翻车：job 卡在 running，worker 只剩 PermissionError）。
  for d in data/pine_library/chat/queue data/pine_library/chat/jobs \
           data/pine_library/notes data/pine_library/edited data/pine_library/versions \
           data/pine_transpile/queue; do
    mkdir -p "$d"
    if ! chmod 777 "$d" 2>/dev/null; then
      # 容器以 root 建过这个目录 → 自动把属主还给宿主用户（1000:1000）再试一次
      docker exec baymax-api chown -R 1000:1000 "/app/data/${d#data/}" 2>/dev/null
      chmod 777 "$d" 2>/dev/null || echo "!! $d 属主非当前用户，宿主 worker 可能摘不了牌（docker exec baymax-api chown 1000:1000 /app/data/${d#data/} 修）"
    fi
  done
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