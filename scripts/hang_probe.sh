#!/bin/bash
# api 挂死探针:每 30s 测 async+sync 端点;连续失败 → dump 线程栈
API=http://127.0.0.1:8091
TOKEN="h_SZns9-nxIdUZ66_lHqZAwAixKt-OwP"
LOG=/home/zbox/backups/baymax/hang_probe.log
mkdir -p "$(dirname "$LOG")"
FAIL=0
while true; do
  TS=$(date '+%H:%M:%S')
  S=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$API/api/overview" -H "X-API-Token: $TOKEN" 2>/dev/null)
  A=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$API/api/quantmind/tdx/config" -H "X-API-Token: $TOKEN" 2>/dev/null)
  if [ "$S" = "200" ] && [ "$A" = "200" ]; then
    FAIL=0
    if [ $(( $(date +%s) % 600 )) -lt 30 ]; then echo "$TS sync=$S async=$A OK" >> "$LOG"; fi
  else
    FAIL=$((FAIL+1))
    echo "$TS sync=$S async=$A FAIL#$FAIL" >> "$LOG"
    if [ "$FAIL" -ge 3 ]; then
      echo "=== $(date) HANG CONFIRMED, dumping stacks ===" >> "$LOG"
      docker exec baymax-api python3 - <<'PYEOF' >> "$LOG" 2>&1
import sys, threading, traceback
print("--- thread dump ---")
for tid, stack in sys._current_frames().items():
    print(f"--- thread {tid} ---")
    traceback.print_stack(stack)
print("--- threading.enumerate:", threading.enumerate()[:20])
PYEOF
      FAIL=0
    fi
  fi
  sleep 30
done
