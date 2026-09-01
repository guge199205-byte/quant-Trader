#!/usr/bin/env bash
# IB Gateway（ib-gateway 容器，quantmind 栈）连接补丁
#
# 问题1: TrustedIPs=127.0.0.1 只信回环——host/BayMax 容器经 docker 桥接
#        （172.21.0.1）连接会被重置。镜像每次启动用 jts.ini.tmpl 覆盖
#        jts.ini，容器重建后补丁丢失 → 跑本脚本重新打。
# 问题2: 凭据在 ~/projects/quantmind/.env（IB_ACCOUNT/IB_PASSWORD），
#        1 字符占位符 = 未登录，API 不启用。
#
# 用法: bash scripts/ib_gateway_patch.sh        # 打 TrustedIPs 补丁 + 重启
#       bash scripts/ib_gateway_patch.sh --check  # 检查登录/端口状态
set -e

CONTAINER=ib-gateway

if [ "$1" = "--check" ]; then
  echo "=== 容器状态 ==="
  docker ps --filter name=$CONTAINER --format '{{.Names}} {{.Status}}'
  echo "=== TrustedIPs ==="
  docker exec $CONTAINER sh -c 'grep TrustedIPs /home/ibgateway/Jts/jts.ini' 2>/dev/null || echo "（无法读取）"
  echo "=== 登录状态（LOGGED_IN 出现即已登录）==="
  docker logs $CONTAINER --since 10m 2>&1 | grep -cE "LOGGED_IN|Login successful" || true
  echo "=== API 握手（有响应=已就绪）==="
  timeout 5 bash -c "printf 'API\\0' | nc 127.0.0.1 4002" | head -c 40 | xxd | head -2
  exit 0
fi

echo "[1/2] 打 TrustedIPs 补丁（127.0.0.1 + docker 桥 172.21.0.1）..."
docker exec $CONTAINER sh -c "sed -i 's/^TrustedIPs=127.0.0.1\$/TrustedIPs=127.0.0.1;172.21.0.1/' /home/ibgateway/Jts/jts.ini.tmpl /home/ibgateway/Jts/jts.ini"
docker exec $CONTAINER sh -c 'grep TrustedIPs /home/ibgateway/Jts/jts.ini'

echo "[2/2] 重启容器（重新登录，等 60s）..."
docker restart $CONTAINER
sleep 60
bash "$0" --check
