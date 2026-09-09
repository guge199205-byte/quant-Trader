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

# 1b. dsh 对外入口（dsh-proxy：host 网络 nginx，绑 DSH_BIND_IP:3081 → 127.0.0.1:3081）。
#     2026-09-08 实录：proxy 容器 09-07 退出（exit 0 且被标记为手动停止 → unless-stopped
#     不会拉起），上面那轮 127.0.0.1:3081 探活照样通（dsh 本体在听），但 8093 反代的是
#     LAN 地址 → 用户看到 502 Bad Gateway nginx/1.31.4，且零告警。这里补探 + 自愈。
DSH_BIND=$(sed -n 's/^DSH_BIND_IP="\?\([^"]*\)"\?$/\1/p' .env 2>/dev/null | head -1)
DSH_BIND="${DSH_BIND:-127.0.0.1}"
if ! timeout 3 bash -c "echo > /dev/tcp/$DSH_BIND/3081" 2>/dev/null; then
    docker start baymax-dsh-proxy >/dev/null 2>&1
    sleep 2
    if timeout 3 bash -c "echo > /dev/tcp/$DSH_BIND/3081" 2>/dev/null; then
        ALERTS="$ALERTS
🔧 dsh-proxy ($DSH_BIND:3081) 掉线已自动拉起"
    else
        ALERTS="$ALERTS
🔴 dsh-proxy ($DSH_BIND:3081) 掉线且自动拉起失败——8093 交易智能体前端会 502"
    fi
fi

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

# 2d. 实时行情源降级检测（rt_probe 每5分钟写 logs/rt_status.json）
#     fuyao 单点抖动多（一天十几次、5-10 分钟自愈）→ 需连续失败 ≥2 次才报；
#     aidata 是静态文件存在性检查（缺了就一直是缺，去抖无效）→ 按天去重；
#     bridge 是主源 → 立即报 + 自动投重启信号。
RT_OUT=$(/usr/bin/python3 scripts/alert_checks.py rt_down logs/rt_status.json 2>/dev/null)
RT_DOWN=$(printf '%s\n' "$RT_OUT" | sed -n 1p)
RT_BYDAY=$(printf '%s\n' "$RT_OUT" | sed -n 2p)
if [ -n "$RT_DOWN" ]; then
    ALERTS="$ALERTS
🔴 实时行情源降级：${RT_DOWN} 不可用——主交易数据以桥为准，Fuyao 为全市场实时备胎"
    if echo "$RT_DOWN" | grep -q bridge && [ -d /mnt/tdx-shared/bridge-windows ]; then
        touch /mnt/tdx-shared/bridge-windows/restart_bridge.flag
        ALERTS="$ALERTS
🔧 桥掉线 → 已投递重启信号"
    fi
fi
if [ -n "$RT_BYDAY" ]; then
    AIDATA_SEEN="/tmp/.baymax_aidata_alerted_$(date +%F)"
    if [ ! -f "$AIDATA_SEEN" ]; then
        touch "$AIDATA_SEEN"
        ALERTS="$ALERTS
🟡 行情备胎降级：${RT_BYDAY}（TdxAiData 仅分钟特征，桥/Fuyao 正常时不阻塞交易）"
    fi
fi

# 2e. 新闻分子新鲜度（盘中每小时一期，>75 分钟无新刊 = 门卫/主编批量截断的
#      静默故障；2026-09-08 实录：latest.json 停在 10:55，交易 agent 吃 230 分钟
#      旧新闻，兜底轮把 state last_ok 照常刷新导致零告警）
#      2026-09-09 修两处误报：下午首判 13:00→13:20（12:55 那期实测 13:08 才落盘，
#      13:00/13:05 看到的是 11:04 旧刊 → 误报 115/120 分钟）；news_brief 进程在跑时不判。
NEWS_RUNNING=""
pgrep -f "scripts/news_brief.py" >/dev/null 2>&1 && NEWS_RUNNING="running"
NEWS_STALE=$(/usr/bin/python3 scripts/alert_checks.py news_stale \
    data/news_brief/latest.json $NEWS_RUNNING 2>/dev/null)
if [ -n "$NEWS_STALE" ]; then
    ALERTS="$ALERTS
🟡 新闻分子停更 ${NEWS_STALE} 分钟（盘中应每小时一期）——查 logs/news_brief.log 门卫/主编是否批量截断，必要时手动跑 news_brief.py；交易侧当前引用的是过期新闻"
fi

# 2g. 新闻抓取桶失败（news_brief 每期把 folder_failures 写进 state.last_run）：
#     ≥3 个桶抓取失败说明上游大面积故障，即便主编兜底出了刊也该报。
#     按（日期 + 失败桶集合）去重——同一个坏桶每期都报会一天刷 5 条。
FOLDER_FAIL=$(/usr/bin/python3 scripts/alert_checks.py folder_failures \
    data/news_brief/state.json 2>/dev/null)
if [ -n "$FOLDER_FAIL" ]; then
    FF_SEEN="/tmp/.baymax_folderfail_seen_$(date +%F)"
    FF_KEY=$(echo "$FOLDER_FAIL" | md5sum | cut -c1-12)
    if ! grep -q "^$FF_KEY$" "$FF_SEEN" 2>/dev/null; then
        echo "$FF_KEY" >> "$FF_SEEN"
        ALERTS="$ALERTS
🟡 新闻抓取降级：$FOLDER_FAIL —— 查 news 上游/网络，交易侧新闻覆盖可能不全"
    fi
fi

# 2c. 因子库新鲜度：alpha_library 分区滞后日K ≥5 自然日 → 提醒重跑
#     （alpha_library_factors.py 为手动批处理，quantdb 日K 每日同步会领先它）
if [ $((NOW % 1800)) -lt 300 ]; then   # ~每 30 分钟检查一次，防刷屏
    KL_DT=$(ls /home/zbox/projects/quantmind/data/quantdb/1_kline_data/daily_backward 2>/dev/null \
        | grep -oE '[0-9]{8}' | sort | tail -1)
    AL_DT=$(ls /home/zbox/projects/quantmind/data/quantdb/6_ml_datasets/alpha_library 2>/dev/null \
        | grep -oE 'dt=[0-9]{8}' | grep -oE '[0-9]{8}' | sort | tail -1)
    if [ -n "$KL_DT" ] && [ -n "$AL_DT" ]; then
        LAG=$(( ( $(date -d "${KL_DT:0:4}-${KL_DT:4:2}-${KL_DT:6:2}" +%s) \
                - $(date -d "${AL_DT:0:4}-${AL_DT:4:2}-${AL_DT:6:2}" +%s) ) / 86400 ))
        if [ "$LAG" -ge 5 ] && [ "$(date +%u)" -le 5 ]; then
            ALERTS="$ALERTS
🟡 因子库 alpha_library 滞后日K ${LAG} 天（${AL_DT} vs ${KL_DT}）——重跑 alpha_library_factors.py 增量刷新"
        fi
    fi
fi

# 2f. 持仓命中事件风险清单（解禁窗口内 / 近期负面新闻）：要"告警跑"的仓位
#     清单由 risk_list.py 每交易日重建（data/risk_block.json），买入侧已硬拦；
#     这里管的是"买完才进清单"的存量仓位——按 (日期, 代码) 去重，一天只报一次。
RISK_HIT=$(python3 - logs/live_ledger.json data/risk_block.json <<'PY'
import json, sys, datetime
BJ = datetime.timezone(datetime.timedelta(hours=8))
today = datetime.datetime.now(BJ).date().isoformat()
try:
    ledger = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
try:
    doc = json.load(open(sys.argv[2], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
items = doc.get("items") if isinstance(doc, dict) else {}
if not isinstance(items, dict):
    raise SystemExit(0)
seen = {}
for agent, rec in (ledger.get("agents") or {}).items():
    for code in (rec.get("positions") or {}):
        it = items.get(str(code).split(".")[0])
        if not it or str(it.get("expire") or "") < today:
            continue
        seen.setdefault(code, (agent, it.get("reason") or ""))
for code, (agent, reason) in sorted(seen.items()):
    print(f"{code}|{agent}|{reason}")
PY
)
if [ -n "$RISK_HIT" ]; then
    RISK_SEEN="/tmp/.baymax_risk_alerted_$(date +%F)"
    RISK_NEW=""
    while IFS='|' read -r code agent reason; do
        [ -z "$code" ] && continue
        grep -q "^$code$" "$RISK_SEEN" 2>/dev/null && continue
        echo "$code" >> "$RISK_SEEN"
        RISK_NEW="$RISK_NEW
🔴 持仓 $code（$agent）命中事件风险：$reason —— 评估减仓/退出"
    done <<< "$RISK_HIT"
    [ -n "$RISK_NEW" ] && ALERTS="$ALERTS$RISK_NEW"
fi
# 清单本身的新鲜度：>3 自然日未重建 = event_radar/news_brief 的刷新链路断了，
# 买入侧会拿旧清单（过期条目会自动失效，但新增解禁/负面新闻会漏拦）
if [ -f data/risk_block.json ]; then
    RISK_AGE_D=$(( ( $(date +%s) - $(stat -c %Y data/risk_block.json) ) / 86400 ))
    if [ "$RISK_AGE_D" -ge 3 ]; then
        ALERTS="$ALERTS
🟡 事件风险清单已 ${RISK_AGE_D} 天未重建（应每交易日刷新）——查 event_radar/news_brief 是否正常，或手动跑 scripts/risk_list.py refresh"
    fi
fi

# 2h. 09:35 主入口执行状态（2026-09-09 实录：桥断线 → BrokerError 裸崩退出，
#     全天 0 笔调仓，日志只有 traceback、零告警；09-07 同样）。
#     判据见 scripts/alert_checks.py::llm_trade_tier：北京 10:00 后 state 仍非 ok:true，
#     且**没有班正在跑**（flock 被占则不判）。两级各报一次/天。
if flock -n /home/zbox/baymax/logs/live_llm_trade.lock -c true 2>/dev/null; then
    LLM_TIER=$(/usr/bin/python3 scripts/alert_checks.py llm_tier logs/live_llm_trade_state.json 2>/dev/null)
    if [ -n "$LLM_TIER" ]; then
        LLM_SEEN="/tmp/.baymax_llm_trade_alerted_$(date +%F)"
        LLM_KEY="${LLM_TIER%%|*}"; LLM_WHY="${LLM_TIER#*|}"
        if ! grep -q "^$LLM_KEY$" "$LLM_SEEN" 2>/dev/null; then
            echo "$LLM_KEY" >> "$LLM_SEEN"
            if [ "$LLM_KEY" = "t2" ]; then
                ALERTS="$ALERTS
🔴 当日调仓两班补跑均未成功：$LLM_WHY —— 今日可能一单未动，请人工确认"
            else
                ALERTS="$ALERTS
🔴 09:35 调仓未正常完成：$LLM_WHY —— 需人工确认是否已下单（10:05/11:05 自动补跑）"
            fi
        fi
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
