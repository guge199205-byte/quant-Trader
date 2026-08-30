#!/bin/bash
# 宿主侧服务探活，结果写到 logs/service_status.json（bind-mount 进 api 容器）
# 原因：容器内的 /api/metrics 无法探宿主回环上的 dsh（dsh 只绑 127.0.0.1）
# cron: * * * * * bash /home/zbox/BayMax-Trader/scripts/status-probe.sh
cd "$(dirname "$0")/.." || exit 1

python3 - <<'EOF'
import json
import socket
import time

probes = {"api": 8091, "mcp_us": 8100, "mcp_cn": 8200, "mcp_hk": 8300, "dsh": 3081}
out = {}
for name, port in probes.items():
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close()
        out[name] = "up"
    except OSError:
        out[name] = "down"
out["generated_at"] = int(time.time())
with open("logs/service_status.json", "w", encoding="utf-8") as f:
    json.dump(out, f)
EOF
