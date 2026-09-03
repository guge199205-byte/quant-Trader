#!/bin/bash
# Trade Agent 告警检查：服务掉线 / 交易停滞 / 净值冻结(行情停更) / 备份过期
# cron: */5 * * * * bash /home/zbox/BayMax-Trader/scripts/alert.sh

cd "$(dirname "$0")/.."

ALERT_LOG="/home/zbox/backups/baymax/alerts.log"
WEBHOOK_URL="${ALERT_WEBHOOK:-}"   # 可选：钉钉/飞书/Server酱 webhook
mkdir -p "$(dirname "$ALERT_LOG")"

ALERTS=""

# 1. 服务探活
#    api 特殊：HTTP 探活（TCP 通但请求挂死/无响应也能发现），连续 2 次
#    失败自动重启容器自愈（曾出现 event-loop 静默挂死、端口照听不响应）。
API_FAIL_FILE="/tmp/.baymax_api_fail"
API_TOKEN=$(sed -n 's/^API_TOKEN="\?\([^"]*\)"\?$/\1/p' .env 2>/dev/null | head -1)
if curl -s -m 5 -o /dev/null -w "%{http_code}" \
    -H "X-API-Token: $API_TOKEN" http://127.0.0.1:8091/api/status 2>/dev/null | grep -q 200; then
    rm -f "$API_FAIL_FILE"
else
    FAILS=$(($(cat "$API_FAIL_FILE" 2>/dev/null || echo 0) + 1))
    echo "$FAILS" > "$API_FAIL_FILE"
    if [ "$FAILS" -ge 2 ]; then
        echo "[$(date '+%F %T')] api 探活连续失败 $FAILS 次，自动重启 baymax-api" >> "$ALERT_LOG"
        docker restart baymax-api >/dev/null 2>&1 || \
            echo "[$(date '+%F %T')] api 自动重启失败，请手动检查" >> "$ALERT_LOG"
        rm -f "$API_FAIL_FILE"
        ALERTS="$ALERTS
🔴 api (8091) 探活失败已自动重启"
    fi
fi

for spec in "mcp_us:8100" "mcp_cn:8200" "mcp_hk:8300" "dsh:3081"; do
    name="${spec%%:*}"; port="${spec##*:}"
    if ! timeout 3 bash -c "echo > /dev/tcp/127.0.0.1/$port" 2>/dev/null; then
        ALERTS="$ALERTS
🔴 服务 $name ($port) 掉线"
    fi
done

# 2. 交易停滞检测：按"最近一笔成功成交"判定（>48h 无成功记录才告警）。
#    2026-09-02 事故修复：行情断开时失败下单也写 live_trade_*.jsonl，
#    旧逻辑统计文件 mtime 被失败记录"刷新鲜"掩盖，净值冻了 3 天零告警。
NOW=$(date +%s)
STALE_HOURS=$(python3 - . <<'PY'
import glob, json, re, datetime
BJ = datetime.timezone(datetime.timedelta(hours=8))
now = datetime.datetime.now(BJ)
def to_ts(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", s or "")
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    return datetime.datetime(y, mo, d, h, mi, tzinfo=BJ)
latest = None
for pf in (glob.glob("logs/live_trade_*.jsonl")
           + glob.glob("logs/live_watch_*.jsonl")):
    try:
        for line in open(pf, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("error"):
                continue          # 拒单/失败不算成交（2026-09 行情断开暴刷）
            t = to_ts(r.get("ts") or r.get("timestamp"))
            if t is not None and (latest is None or t > latest):
                latest = t
    except OSError:
        continue
if latest is None:
    print(99999)                  # 从未成交过
else:
    h = (now - latest).total_seconds() / 3600
    if h > 48:
        print(int(h))
PY
)
if [ -n "$STALE_HOURS" ]; then
    ALERTS="$ALERTS
🟡 实盘成交已 ${STALE_HOURS} 小时无成功记录（失败下单不计入）"
fi

# 2b. A股净值冻结检测（TDX 行情通道死而进程假活：桥 HTTP 通、返回缓存，
#     净值分钟级采样数值纹丝不动 → 盘中 30 分钟同值即告警）。
#     只在北京工作日盘中判定，节假日/盘后天然静止不算。
FROZEN_VAL=$(python3 - logs/live_equity.jsonl <<'PY'
import json, re, sys, datetime
BJ = datetime.timezone(datetime.timedelta(hours=8))
now = datetime.datetime.now(BJ)
if now.weekday() >= 5:
    raise SystemExit(0)
mins = now.hour * 60 + now.minute
if not ((9 * 60 + 30 <= mins < 11 * 60 + 30) or (13 * 60 <= mins < 15 * 60)):
    raise SystemExit(0)
def to_bj(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", s or "")
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    return datetime.datetime(y, mo, d, h, mi, tzinfo=BJ)
rows = []
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("agent") is None and r.get("value") is not None:
                rows.append(r)    # "agent": null 的行 = 桥总资产
except OSError:
    raise SystemExit(0)
cutoff = now - datetime.timedelta(minutes=30)
recent = [r for r in rows
          if (t := to_bj(r.get("ts"))) is not None and t >= cutoff]
if len(recent) < 5:               # 采样不足不判定
    raise SystemExit(0)
vals = {round(float(r["value"]), 2) for r in recent}
if len(vals) == 1:
    print(round(float(vals.pop())))
PY
)
if [ -n "$FROZEN_VAL" ]; then
    ALERTS="$ALERTS
🔴 A股净值冻结于 ¥$FROZEN_VAL 已 30 分钟未变——TDX 行情通道疑似断开（桥假活），快查 Windows 交易机通达信行情连接"
    # 假活自愈：向 Windows 看门狗投递重启信号（共享目录挂载可用时）
    if [ -d /mnt/tdx-shared/bridge-windows ]; then
        touch /mnt/tdx-shared/bridge-windows/restart_bridge.flag
        ALERTS="$ALERTS
🔧 已投递 restart_bridge.flag → 桥看门狗将自动重启"
    else
        ALERTS="$ALERTS
⚠️ 共享目录不可用，无法投递重启信号，须手动重启桥"
    fi
fi

# 2c. 挂单停滞检测（下单后长时间无回报：盘中在途 >10 分钟 → 告警并重启桥自愈；
#      委托保留在券商端，重启后由 reconcile 按真实成交/撤单收尾，不会重复下单）
STUCK=$(python3 - . <<'PY'
import json, os, time
from datetime import datetime, timezone, timedelta
BJ = timezone(timedelta(hours=8))
now = datetime.now(BJ)
if now.weekday() >= 5:
    raise SystemExit(0)
m = now.hour * 60 + now.minute
if not ((9 * 60 + 30 <= m < 11 * 60 + 30) or (13 * 60 <= m < 15 * 60)):
    raise SystemExit(0)
p = "data/live_pending_orders.json"
if not os.path.isfile(p):
    raise SystemExit(0)
try:
    pend = json.load(open(p, encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
n = 0
for x in (pend if isinstance(pend, list) else []):
    ts = str(x.get("ts") or "")
    try:
        t = datetime.fromisoformat(ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=BJ)
        if (now - t.astimezone(BJ)).total_seconds() > 600:
            n += 1
    except Exception:
        continue
if n:
    print(n)
PY
)
if [ -n "$STUCK" ]; then
    ALERTS="$ALERTS
🔴 $STUCK 笔在途委托停滞 >10 分钟（下单可能卡住）——自动重启桥并保持委托，reconcile 收尾"
    if [ -d /mnt/tdx-shared/bridge-windows ]; then
        touch /mnt/tdx-shared/bridge-windows/restart_bridge.flag
        ALERTS="$ALERTS
🔧 已投递 restart_bridge.flag → 桥自动重启"
    fi
fi

# 3. 备份过期检测（>26h 无备份）
latest_bak=$(ls -t /home/zbox/backups/baymax/baymax-*.tar.gz 2>/dev/null | head -1)
if [ -z "$latest_bak" ] || [ $((NOW - $(stat -c %Y "$latest_bak" 2>/dev/null || echo 0))) -gt 93600 ]; then
    ALERTS="$ALERTS
🟡 备份过期（>26h）"
fi

if [ -n "$ALERTS" ]; then
    MSG="[Trade Agent $(date '+%Y-%m-%d %H:%M')]$ALERTS"
    echo "$MSG" >> "$ALERT_LOG"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -s -m 10 -H "Content-Type: application/json" \
            -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$MSG\"}}" \
            "$WEBHOOK_URL" >/dev/null 2>&1 || true
    fi
    echo "$MSG"
fi

# 更新宿主侧探活结果（供 api /api/metrics 消费，避免容器内探活误报）
bash scripts/status-probe.sh
