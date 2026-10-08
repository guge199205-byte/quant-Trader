#!/bin/bash
# Trade Agent 告警检查：服务掉线 / 交易停滞 / 净值冻结(行情停更) / 备份过期
# cron: */5 * * * * bash /home/zbox/BayMax-Trader/scripts/alert.sh

cd "$(dirname "$0")/.."

ALERT_LOG="/home/zbox/backups/baymax/alerts.log"
WEBHOOK_URL="${ALERT_WEBHOOK:-}"   # 可选：钉钉/飞书/Server酱 webhook
mkdir -p "$(dirname "$ALERT_LOG")"

# 维护模式：锁存在且未过期(<30min) → 计划内维护窗口，本轮静默（与 auto-heal.sh 同一把锁）
if [[ -f /tmp/baymax-maintenance.lock ]] && (( $(date +%s) - $(stat -c %Y /tmp/baymax-maintenance.lock) < 1800 )); then
    exit 0
fi

ALERTS=""

# 时区约定：本机是 JST(+09:00)=北京+1h，裸 `date +%F/%H/%u` 全是日本时区——
# 北京时间 23:00（JST 午夜）就翻日，未解决的委托事件会被当成"新的一天"重新公告
# （2026-09-19/09-20 连续两晚实录：周五的事件在两个午夜各重播一次）。
# 所有「按日/按小时去重」的键与面向人的时间戳一律用下面两个变量；
# breaker 块（logs/breaker/ 文件名由 _bj_today 写）本来就是这个口径。
BJ_DATE=$(TZ=Asia/Shanghai date +%F)
BJ_HOUR=$(TZ=Asia/Shanghai date +%H)

# 检查器统一入口。**检查器自己坏掉不能等于「没问题」**：
# 旧写法把 check 的输出喂进命令替换、stderr 一律丢弃，于是三种情形压成同一个空
# 字符串——① 真没故障；② 子命令不存在/版本错配（实测 rc=2，且 "unknown check"
# 只走 stderr，正好被吞）；③ 脚本自身崩了（rc=1）。结果：「告警面瞎了」这个最
# 需要人被叫醒的故障，表现是一直安静（2026-09-02→09-22 分钟因子静默三周是同一
# 形状）。下面按 rc 分流：rc=0 的输出照旧，rc≠0 记到本次运行的结果文件，脚本
# 末尾统一上告警。
CHECKS_FAILED_RUN=$(mktemp /tmp/.baymax_check_failed_run.XXXXXX)
CHECKS_ERR_LOG="logs/alert_checks_errors.log"
acheck() {                       # acheck <子命令> [参数...] → stdout 为检查器输出
    local out rc err
    err=$(mktemp /tmp/.baymax_check_err.XXXXXX)
    out=$(/usr/bin/python3 scripts/alert_checks.py "$@" 2>"$err")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        # 一条记录 = 子命令 + rc + stderr 首行（能有几百字节，够定位）
        printf '%s(rc=%s) %s\n' "$1" "$rc" "$(head -1 "$err" | cut -c1-300)" \
            >> "$CHECKS_FAILED_RUN"
        rm -f "$err"
        return 0                 # 输出空 = 本次该检查无告警；失败另走末尾统一上告警
    fi
    rm -f "$err"
    printf '%s' "$out"
}

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
        echo "[$(TZ=Asia/Shanghai date '+%F %T')] api 探活连续失败 $FAILS 次，自动重启 baymax-api" >> "$ALERT_LOG"
        docker restart baymax-api >/dev/null 2>&1 || \
            echo "[$(TZ=Asia/Shanghai date '+%F %T')] api 自动重启失败，请手动检查" >> "$ALERT_LOG"
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

# 2c. 挂单停滞检测（下单后长时间无回报：盘中在途 >10 分钟。
#      常规停滞 → 告警并重启桥自愈（委托保留在券商端，重启后由 reconcile 按真实
#      成交/撤单收尾，不会重复下单）；
#      保护价挂队（protect=true，止损报跌停价、跌停封死等买盘）→ 只告警不重启：
#      买盘不在桥上，重启没有任何意义，只会打乱队列。
#      判定在 alert_checks.py::pending_stuck（可单测），输出固定一行「常规数|挂队数」）
STUCK=$(acheck pending_stuck data/live_pending_orders.json)
if [ -n "$STUCK" ]; then
    STUCK_N="${STUCK%%|*}"; STUCK_P="${STUCK##*|}"
    if [ "$STUCK_N" -gt 0 ] 2>/dev/null; then
        ALERTS="$ALERTS
🔴 $STUCK_N 笔在途委托停滞 >10 分钟（下单可能卡住）——自动重启桥并保持委托，reconcile 收尾"
        if [ -d /mnt/tdx-shared/bridge-windows ]; then
            touch /mnt/tdx-shared/bridge-windows/restart_bridge.flag
            ALERTS="$ALERTS
🔧 已投递 restart_bridge.flag → 桥自动重启"
        fi
    fi
    if [ "$STUCK_P" -gt 0 ] 2>/dev/null; then
        ALERTS="$ALERTS
🔴 $STUCK_P 笔保护价卖单挂队 >10 分钟未成交——大概率跌停封死（无买盘本就卖不掉），请在通达信确认真实盘口；系统不再自动重试同一标的"
    fi
fi

# 2c-2. 委托事件（data/live_order_events.json，live_fills.record_event 写）：
#      止损的跌停挂队 / 终态未成交 / 执行开关关闭时触发 / 下单失败 / 委托号回捞失败。
#      2026-09-11 之前这些只写 logs/*.jsonl：止损没卖出去也零告警（当日 600309
#      触发但开关关着，规则被吃掉、持仓裸奔一整天而无人知晓）。按事件 id 去重
#      （BJ_DATE：北京日切，别在 JST 午夜重播）。第二个参数传账本：卖域事件在
#      账本已无该标的持仓时自动消解（结不掉的仍报；见 order_events.py）。
ORD_EVENTS=$(acheck order_events data/live_order_events.json logs/live_ledger.json)
if [ -n "$ORD_EVENTS" ]; then
    ORD_SEEN="/tmp/.baymax_order_events_seen_$BJ_DATE"
    while IFS='|' read -r ev_id ev_msg; do
        [ -z "$ev_id" ] && continue
        grep -qF "$ev_id" "$ORD_SEEN" 2>/dev/null && continue
        echo "$ev_id" >> "$ORD_SEEN"
        ALERTS="$ALERTS
🔴 委托事件：$ev_msg"
    done <<< "$ORD_EVENTS"
fi

# 2c-3. 延期单滞留 / 到期作废（logs/live_ledger.json 的 deferred）
#      延期单 = 「减仓意图落盘、等桥恢复后重放」。replay_deferred.py 里有两条
#      **静默消失**路径：
#        ① 24h 到期丢弃（现在会记 logs/deferred_expired.jsonl）
#        ② 被同代码在途卖单无限期挡住，每分钟"保留延期"，最终仍落到 ①
#      两者丢弃的都是「这笔减仓没做成」这件事本身。原先只打一行日志，没人会看。
#      （设计参照 Vibe-Trading 的 reconcile：歧义必须 classify-and-surface，
#      绝不静默自动纠正／静默丢弃。）
#      按（滞留数, 作废数）去重：情形变化才再报一次。⚠️ 去重键里不能含"最久
#      Xh"——它随墙上时钟连续增长，每约 6 分钟就变成新字符串重复告警（2026-09-18
#      评审 M-2 实测；QQ 侧此前只是被 alert_push 的指纹归一碰巧压住）。
DEFER_OUT=$(acheck deferred_stuck logs/live_ledger.json)
if [ -n "$DEFER_OUT" ]; then
    D_STUCK="${DEFER_OUT%%|*}"; _rest="${DEFER_OUT#*|}"; D_OLD="${_rest%%|*}"; D_EXP="${DEFER_OUT##*|}"
    DEFER_KEY="$D_STUCK|$D_EXP"
    DEFER_SEEN="/tmp/.baymax_defer_seen_$BJ_DATE"
    if ! grep -qxF "$DEFER_KEY" "$DEFER_SEEN" 2>/dev/null; then
        echo "$DEFER_KEY" >> "$DEFER_SEEN"
        if [ "$D_STUCK" -gt 0 ] 2>/dev/null; then
            ALERTS="$ALERTS
🟠 $D_STUCK 笔延期单滞留 >2 小时未重放（最久 ${D_OLD}h）——减仓意图还没执行"
        fi
        if [ "$D_EXP" -gt 0 ] 2>/dev/null; then
            ALERTS="$ALERTS
🔴 今日 $D_EXP 笔延期单 24h 到期作废——这些减仓确定没有执行，需人工确认"
        fi
    fi
fi

# 2d. 实时行情源降级检测（rt_probe 每5分钟写 logs/rt_status.json）
#     fuyao 单点抖动多（一天十几次、5-10 分钟自愈）→ 需连续失败 ≥2 次才报；
#     aidata 降级（熔断打开）不自愈 → 按天去重；人工关闭（disabled）与
#     从未调用（unproven）都是 ok=True，不会走到这里；
#     bridge 是主源 → 立即报 + 自动投重启信号。
RT_OUT=$(acheck rt_down logs/rt_status.json)
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
    # 去重键带状态词（rt_down 的第 3 行，机器可读）：degraded（熔断状态文件坏）与
    # down（通道坏）同一天是两件事，共用一个键 = 先到的那条把另一条吃掉——
    # 2026-09-21 盘满实录：状态文件先被截成 0 字节，此后真有通道故障也报不出来。
    AIDATA_KEY=$(printf '%s\n' "$RT_OUT" | sed -n 3p)
    AIDATA_SEEN="/tmp/.baymax_aidata_alerted_${BJ_DATE}${AIDATA_KEY:+_$AIDATA_KEY}"
    if [ ! -f "$AIDATA_SEEN" ]; then
        touch "$AIDATA_SEEN"
        ALERTS="$ALERTS
🟡 行情备胎降级：${RT_BYDAY}（TdxAiData 仅分钟特征；degraded=熔断状态文件坏、保护失效，不等于通道坏。桥/Fuyao 正常时不阻塞交易）"
    fi
fi

# 2d-2. 账户通道（桥 account/query）掉线：行情能看 ≠ 能下单/能查持仓。
#      2026-09-10 实录：行情全通、账户整日 asset=0，7 轮分析 + 3 次调仓 + 全部哨兵
#      条件位静默哑火，看板只探行情 → 全天零告警。该故障不自愈（要重登交易端），
#      故只在 08:45-15:35 的可行动窗口内报（判定在 alert_checks.account_down），
#      且同一天同一段故障只报一次（按 first_fail_ts 去重，次日仍坏会再提醒一次）。
ACCT_DOWN=$(acheck account_down logs/rt_status.json)
if [ -n "$ACCT_DOWN" ]; then
    ACCT_SEEN="/tmp/.baymax_acct_alerted_$BJ_DATE"
    if [ ! -f "$ACCT_SEEN" ]; then
        touch "$ACCT_SEEN"
        ALERTS="$ALERTS
🔴 桥账户通道掉线（${ACCT_DOWN}）——行情能看但下单/持仓查询全哑火：盘中分析、09:35 调仓、哨兵条件位都会静默停摆。RDP 登录 Windows 交易机 192.168.31.13，在通达信里重新登录交易账号（行情不用动）"
    fi
fi

# 2d-3 行情看板自身的新鲜度（logs/rt_status.json，rt_probe 全天每 5 分钟一轮）。
#      这是**最后一条假绿路径**的兜底：rt_down/account_down 只读板子内容、不看
#      mtime，所以「探针写不出板子」= 六路降级告警整体冻在上一份板上且表面全绿
#      （2026-09-21 盘满时 ENOSPC 打在 write_text 上就是这个形态）。板子停更时，
#      上面 2d/2d-2 那两段看到的是**旧结论**——它们不报 ≠ 六路都健康。
#      按小时去重：停更不是抖动，但也不该每 5 分钟响一次。
RT_STALE=$(acheck rt_stale logs/rt_status.json)
if [ -n "$RT_STALE" ]; then
    RTS_SEEN="/tmp/.baymax_rt_stale_${BJ_DATE}_${BJ_HOUR}"
    if [ ! -f "$RTS_SEEN" ]; then
        touch "$RTS_SEEN"
        ALERTS="$ALERTS
🟠 行情源看板失联：${RT_STALE}"
    fi
fi

# 2e. 新闻管线/分子新鲜度（判定在 alert_checks.py::news_freshness，2026-09-18 重构）：
#      旧判据看 latest.json 的 mtime + pgrep 到进程就豁免——09-18 实录：主机冻结打掉
#      09:25/09:55 两轮，白天交易 agent 引用 14 小时前的分子（>14h），上午判定窗口
#      零告警；下午报出后又因文案分钟数变化刷了 11 条。现在读 state.json 的成功
#      游标（last_end，跑满五段且无桶失败/主编失败才推进），进程豁免带过期：
#      存活 >30 分钟的 news_brief 视为卡死、不再豁免（正常单轮 5-15 分钟）。
#      按（类型, 小时）去重。2026-09-09 两处误报修复（下午首判 13:20、
#      进程在跑不判）语义不变。
NEWS_RUNNING=""
for p in $(pgrep -f "scripts/news_brief.py"); do
    ET=$(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ')
    if [ -n "$ET" ] && [ "$ET" -le 1800 ]; then NEWS_RUNNING="running"; break; fi
done
NEWS_FRESH=$(acheck news_stale \
    data/news_brief/state.json $NEWS_RUNNING)
if [ -n "$NEWS_FRESH" ]; then
    NEWS_SEEN="/tmp/.baymax_news_seen_$BJ_DATE"
    NEWS_KEY="${NEWS_FRESH%%|*}@$BJ_HOUR"
    if ! grep -q "^$NEWS_KEY$" "$NEWS_SEEN" 2>/dev/null; then
        echo "$NEWS_KEY" >> "$NEWS_SEEN"
        ALERTS="$ALERTS
🟡 ${NEWS_FRESH#*|}"
    fi
fi

# 2g. 新闻抓取桶失败（news_brief 每期把 folder_failures 写进 state.last_run）：
#     ≥3 个桶抓取失败说明上游大面积故障，即便主编兜底出了刊也该报。
#     按（日期 + 失败桶集合）去重——同一个坏桶每期都报会一天刷 5 条。
FOLDER_FAIL=$(acheck folder_failures \
    data/news_brief/state.json)
if [ -n "$FOLDER_FAIL" ]; then
    FF_SEEN="/tmp/.baymax_folderfail_seen_$BJ_DATE"
    FF_KEY=$(echo "$FOLDER_FAIL" | md5sum | cut -c1-12)
    if ! grep -q "^$FF_KEY$" "$FF_SEEN" 2>/dev/null; then
        echo "$FF_KEY" >> "$FF_SEEN"
        ALERTS="$ALERTS
🟡 新闻抓取降级：$FOLDER_FAIL —— 查 news 上游/网络，交易侧新闻覆盖可能不全"
    fi
fi

# 2j. 因子库新鲜度：alpha_library 分区滞后日K ≥5 自然日 → 提醒重跑
#     （alpha_library_factors.py 已进夜间 cron：scripts/alpha_library_refresh.sh，
#      北京 01:30 全量刷新（实测 ~80 分钟/峰值 RSS ~24GB，内存<30GB 或分区不可写
#      自动跳过）；仍滞后说明 cron 没跑成/闸门连续跳过，查 alpha_library/cron_run.log）
if [ $((NOW % 1800)) -lt 300 ]; then   # ~每 30 分钟检查一次，防刷屏
    KL_DT=$(ls /home/zbox/projects/quantmind/data/quantdb/1_kline_data/daily_backward 2>/dev/null \
        | grep -oE '[0-9]{8}' | sort | tail -1)
    AL_DT=$(ls /home/zbox/projects/quantmind/data/quantdb/6_ml_datasets/alpha_library 2>/dev/null \
        | grep -oE 'dt=[0-9]{8}' | grep -oE '[0-9]{8}' | sort | tail -1)
    if [ -n "$KL_DT" ] && [ -n "$AL_DT" ]; then
        LAG=$(( ( $(date -d "${KL_DT:0:4}-${KL_DT:4:2}-${KL_DT:6:2}" +%s) \
                - $(date -d "${AL_DT:0:4}-${AL_DT:4:2}-${AL_DT:6:2}" +%s) ) / 86400 ))
        if [ "$LAG" -ge 5 ] && [ "$(TZ=Asia/Shanghai date +%u)" -le 5 ]; then
            AL_SEEN="/tmp/.baymax_alpha_lag_seen"
            if ! grep -q "^$BJ_DATE:${LAG}$" "$AL_SEEN" 2>/dev/null; then
                echo "$BJ_DATE:${LAG}" >> "$AL_SEEN"
                ALERTS="$ALERTS
🟡 因子库 alpha_library 滞后日K ${LAG} 天（${AL_DT} vs ${KL_DT}）——夜间 cron 未生效，查 alpha_library/cron_run.log"
            fi
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
    RISK_SEEN="/tmp/.baymax_risk_alerted_$BJ_DATE"
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
    LLM_TIER=$(acheck llm_tier logs/live_llm_trade_state.json)
    if [ -n "$LLM_TIER" ]; then
        LLM_SEEN="/tmp/.baymax_llm_trade_alerted_$BJ_DATE"
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

# 2h-1. 整点轮死进程对账（必须在 round_stuck 判定**之前**跑）。
#     2026-09-21 实录：14:45 尾盘决策轮撞 ENOSPC 中断，failed 心跳同样写不进去 →
#     状态停在 running=true + 死 pid；round_stuck 只按心跳新鲜度判定，于是每 5 分钟
#     一条、QQ 每 2 小时重推，一直报到次日新轮覆盖，且「锁会被跳过」对死进程是
#     假陈述（flock 随进程终止自动释放）。对账把死轮显式收尾成 failed 并只报一次。
ROUND_RECONCILE=$(/usr/bin/python3 scripts/round_reconcile.py 2>/dev/null)
if [ -n "$ROUND_RECONCILE" ]; then
    ALERTS="$ALERTS
🔴 $ROUND_RECONCILE"
fi

# 2h-2. 整点轮心跳（logs/live_analysis_round.json，live_hourly_analysis.round_heartbeat 写）。
#     2026-09-18 实录：数据源退避把单个分析实例拖了数小时，后续 cron 轮全部
#     「已有分析实例在跑」静默跳过——当天只剩 1 轮、零告警。心跳停滞 ≥20 分钟即报
#     （判定在 alert_checks.py::round_stuck；alert_push 指纹去重防持续刷屏）。
ROUND_STUCK=$(acheck round_stuck logs/live_analysis_round.json)
if [ -n "$ROUND_STUCK" ]; then
    ALERTS="$ALERTS
🔴 $ROUND_STUCK"
fi

# 2h-2b. 磁盘水位（2026-09-21 实录：盘满 ENOSPC 北京 15:43-17:44——rsyslog 266 万行
#     错误刷爆 journal、心跳写失败误判「主机离线 177 分钟」、告警丢块、尾盘决策轮
#     中断；全程没有一条直接指向磁盘的告警，全是下游症状）。阈值 88%。
DISK_WATER=$(acheck disk_water)
if [ -n "$DISK_WATER" ]; then
    ALERTS="$ALERTS
$DISK_WATER"
fi

# 2h-3. 循环熔断（live_breaker 触发 → 该 agent 当日禁止新开仓，卖出照常）。
#       2026-09-18 前只落 logs/breaker/{day}.json、alert.sh 无接线——某 agent
#       全天停买而人不知道。判定在 alert_checks.py::breaker_lines；按 agent
#       去重（熔断是当日状态，同一场只打扰一次）。日期取**北京**（文件名由
#       live_breaker 的 _bj_today 写，本机是 JST，直接 date +%F 会差一天）。
BREAKER_DAY="$BJ_DATE"
BREAKER=$(acheck breaker "logs/breaker/$BREAKER_DAY.json")
if [ -n "$BREAKER" ]; then
    BR_SEEN="/tmp/.baymax_breaker_seen_$BREAKER_DAY"
    while IFS='|' read -r br_agent br_reason br_ts; do
        [ -z "$br_agent" ] && continue
        grep -qxF "$br_agent" "$BR_SEEN" 2>/dev/null && continue
        echo "$br_agent" >> "$BR_SEEN"
        ALERTS="$ALERTS
🛑 [$br_agent] 循环熔断：${br_reason}（${br_ts}）——该账户当日禁止新开仓；若为数据异常请查 logs/breaker/ 与 live_breaker 判据"
    done <<< "$BREAKER"
fi

# 2i. L2 快照新鲜度（live_l2_capture 交易日每 5 分钟写 data/l2_factors_live.json）：
#     它是候选池「实时价注入 + 按资金量裁剪」的唯一数据源，停更则两条链路同时静默失效
#     （模型拿不到池内价 → 只能 hold 或凭评分瞎报）。按天去重：真停更往往停一整天，
#     不去重就是每 5 分钟一条。
L2_STALE=$(acheck l2_stale data/l2_factors_live.json)
if [ -n "$L2_STALE" ]; then
    L2_SEEN="/tmp/.baymax_l2_stale_$BJ_DATE"
    if [ ! -f "$L2_SEEN" ]; then
        touch "$L2_SEEN"
        ALERTS="$ALERTS
🟡 L2 快照${L2_STALE}——候选池实时价注入与资金量裁剪的数据源，停更则模型只能 hold/凭评分报单；查 logs/live_l2_capture.log 与桥状态"
    fi
fi

# 2i-2 分钟因子降级（data/l2_status.json 的 capabilities.minute_k）。
#      与上面的 l2_stale 是两个故障：那时是「采集整个停了」，这里是「采集在跑、
#      L2 因子也在写，但分钟因子单独死了」——2026-09-02→09-22 正是后者：617 轮
#      全跑了、产物里 minute_feats 出现 0 次、零告警。状态机的 warmup/idle 已判成
#      ok=True（开盘头 30 分钟与盘后本就无因子），这里只在真降级且连续 ≥2 轮时报。
L2_MIN=$(acheck l2_minute data/l2_status.json)
if [ -n "$L2_MIN" ]; then
    L2M_SEEN="/tmp/.baymax_l2_minute_$BJ_DATE"
    if [ ! -f "$L2M_SEEN" ]; then
        touch "$L2M_SEEN"
        ALERTS="$ALERTS
🟡 分钟因子降级：${L2_MIN}——30 分钟动量/5 分钟波动/量比/内外盘是模型盘中判断的依据，缺了只能凭 L2 因子与盘口；查 logs/min_snapshots/ 是否有新写入（采样器 cron）"
    fi
fi

# 2z. 检查器自身失败汇总（见文件头 acheck 的说明）。
#     这些检查在本次运行里**没有执行**——不是「没故障」。按天去重（与其它检查一致）：
#     脚本坏掉不会 5 分钟自愈，一天一条足够把人叫起来；期间每轮仍会把明细写进
#     logs/alert_checks_errors.log（覆盖写，条数恒定，不会长成第二个撑爆盘的文件）。
if [ -s "$CHECKS_FAILED_RUN" ]; then
    cp "$CHECKS_FAILED_RUN" "$CHECKS_ERR_LOG" 2>/dev/null
    FAILED_CHECKS=$(cut -d'(' -f1 "$CHECKS_FAILED_RUN" | sort -u | tr '\n' ',' | sed 's/,$//')
    CHK_SEEN="/tmp/.baymax_check_failed_$BJ_DATE"
    if [ ! -f "$CHK_SEEN" ]; then
        touch "$CHK_SEEN"
        ALERTS="$ALERTS
🟡 告警检查器自身失败：${FAILED_CHECKS}——这些检查本次**没有在跑**（≠ 没问题），上面因此可能少了告警；明细 ${CHECKS_ERR_LOG}，先确认 scripts/alert_checks.py 与运行环境是否匹配"
    fi
fi
rm -f "$CHECKS_FAILED_RUN"

# 3. 备份过期检测（>26h 无备份）
latest_bak=$(ls -t /home/zbox/backups/baymax/baymax-*.tar.gz 2>/dev/null | head -1)
if [ -z "$latest_bak" ] || [ $((NOW - $(stat -c %Y "$latest_bak" 2>/dev/null || echo 0))) -gt 93600 ]; then
    ALERTS="$ALERTS
🟡 备份过期（>26h）"
fi

if [ -n "$ALERTS" ]; then
    MSG="[Trade Agent $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M')]$ALERTS"
    echo "$MSG" >> "$ALERT_LOG"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -s -m 10 -H "Content-Type: application/json" \
            -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$MSG\"}}" \
            "$WEBHOOK_URL" >/dev/null 2>&1 || true
    fi
    # 转发到 QQ（2026-09-18 修复：此前只落本地 alerts.log + 可选 webhook，
    # ALERT_WEBHOOK 未配置时全部告警零推送——委托被拒/需人工核对/新闻停更
    # 19h 都是哑的。alert_push 内部做指纹去重+2h TTL 防持续故障刷屏。）
    ALERT_PY="/home/zbox/baymax/.venv/bin/python"
    [ -x "$ALERT_PY" ] || ALERT_PY="$(command -v python3)"
    "$ALERT_PY" scripts/alert_push.py "$MSG" >/dev/null 2>&1 || true
    echo "$MSG"
fi

# 更新宿主侧探活结果（供 api /api/metrics 消费，避免容器内探活误报）
bash scripts/status-probe.sh
