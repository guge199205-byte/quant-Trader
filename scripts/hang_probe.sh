#!/bin/bash
# api 挂死探针:每 30s 测 async+sync 端点;连续失败 → dump 全部线程栈
#
# 探针选择（2026-09-09 修）：原来拿 /api/overview 当 sync 探针，它本身是重聚合端点，
# 慢起来 >10s 就记 000——历史日志里 991 条 "sync=000 async=200" 多半是这种假阳性，
# 真挂死反而被淹没。改成两个都零依赖的廉价端点：
#   sync  /api/status  （线程池，同步路由）
#   async /api/ping    （事件循环，async 路由，见 api_server.ping 的注释）
# 判据：两者都 200 才算 OK。sync 掉+async 活=线程池饿死；两者同时掉=事件循环被锁死。
API=http://127.0.0.1:8091
TOKEN="h_SZns9-nxIdUZ66_lHqZAwAixKt-OwP"
LOG=/home/zbox/backups/baymax/hang_probe.log
mkdir -p "$(dirname "$LOG")"
FAIL=0
while true; do
  TS=$(date '+%H:%M:%S')
  S=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$API/api/status" -H "X-API-Token: $TOKEN" 2>/dev/null)
  A=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$API/api/ping" -H "X-API-Token: $TOKEN" 2>/dev/null)
  if [ "$S" = "200" ] && [ "$A" = "200" ]; then
    FAIL=0
    if [ $(( $(date +%s) % 600 )) -lt 30 ]; then echo "$TS sync=$S async=$A OK" >> "$LOG"; fi
  else
    FAIL=$((FAIL+1))
    echo "$TS sync=$S async=$A FAIL#$FAIL" >> "$LOG"
    if [ "$FAIL" -ge 3 ]; then
      echo "=== $(date) HANG CONFIRMED, dumping stacks ===" >> "$LOG"
      # SIGUSR1 → api_server 主线程注册的 faulthandler 把**全部线程**的 Python 栈
      # 打到 stderr，再连 docker logs 一起收进日志。另起 python 进程 dump 是无效的
      # （sys._current_frames 只看得到自己那几个线程——旧版就是这么白 dump 了一个月）。
      # 若该版本没注册处理器，SIGUSR1 的默认动作是终止进程 → 容器按
      # restart=unless-stopped 自动拉起（挂死状态下这本就是想要的结果）。
      docker exec baymax-api kill -USR1 1 2>/dev/null
      sleep 1
      docker logs --timestamps --tail 300 baymax-api >> "$LOG" 2>&1
      FAIL=0
    fi
  fi
  sleep 30
done
