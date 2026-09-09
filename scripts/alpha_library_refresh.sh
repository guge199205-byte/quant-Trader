#!/usr/bin/env bash
# alpha_library 夜间刷新（Alpha101 + GTJA191 + Alpha158 → 6_ml_datasets/alpha_library）
#
# 为什么有这个脚本：alpha_library_factors.py 一直是手动批处理，从未进 cron →
# 分区滞后日K 5 天（2026-09-09 实测 alpha dt=20260903 vs kline dt=20260908），
# alert.sh 2c 每 30 分钟报一次。已有分区会跳过，日常只算新增日。
#
# 闸门：
#   1) flock 防重入（上一次没跑完就跳过）；
#   2) 已最新则空转（cron 早于 quantdb 夜间同步时不会白跑 36 分钟）；
#   3) 可用内存 < MIN_MEM_GB 跳过——全量计算峰值约 15GB，本机 swap 已用 12/15，
#      不能和盘中/夜间任务抢内存（宁可滞后一天，也不要 OOM 拖垮实盘）。
#
# cron（JST = 北京+1h）：30 2 * * 2-6  ← 北京 01:30，接在 quantdb 夜间同步
# （实测 dt 分区 01:55 JST 落盘）之后。
set -uo pipefail

QM_ROOT=/home/zbox/projects/quantmind
PY=/usr/bin/python3
SCRIPT="$QM_ROOT/backend/scripts/alpha_library_factors.py"
ALPHA_ROOT="$QM_ROOT/data/quantdb/6_ml_datasets/alpha_library"
KLINE_ROOT="$QM_ROOT/data/quantdb/1_kline_data/daily_forward"
LOG="$ALPHA_ROOT/cron_run.log"
LOCK=/tmp/.baymax_alpha_refresh.lock
MIN_MEM_GB=20

ts() { date '+%F %T'; }
log_line() { echo "$(ts) $*" >> "$LOG"; }

mkdir -p "$ALPHA_ROOT"
touch "$LOG" 2>/dev/null

# 1) 防重入
exec 9>"$LOCK"
if ! flock -n 9; then
    log_line "跳过：上一次刷新仍在运行"
    exit 0
fi

# 2) 已最新则空转（比较 8 位日期串，同为整数比较）
KL_DT=$(ls -d "$KLINE_ROOT"/dt=* 2>/dev/null | grep -oE '[0-9]{8}' | sort | tail -1)
AL_DT=$(ls -d "$ALPHA_ROOT"/dt=* 2>/dev/null | grep -oE '[0-9]{8}' | sort | tail -1)
if [ -z "$KL_DT" ]; then
    log_line "跳过：日K 分区目录为空（$KLINE_ROOT）"
    exit 0
fi
if [ -n "$AL_DT" ] && [ "$AL_DT" -ge "$KL_DT" ]; then
    log_line "跳过：已最新（alpha=$AL_DT kline=$KL_DT）"
    exit 0
fi

# 3) 内存闸门
AVAIL_GB=$(awk '/^MemAvailable:/ {print int($2/1024/1024)}' /proc/meminfo)
if [ -z "$AVAIL_GB" ] || [ "$AVAIL_GB" -lt "$MIN_MEM_GB" ]; then
    log_line "跳过：可用内存 ${AVAIL_GB:-?}GB < ${MIN_MEM_GB}GB（alpha=$AL_DT kline=$KL_DT）"
    exit 0
fi

log_line "开始刷新（alpha=${AL_DT:-无} kline=$KL_DT 可用内存=${AVAIL_GB}GB）"
nice -n 10 "$PY" "$SCRIPT" >> "$LOG" 2>&1
rc=$?
AL_NEW=$(ls -d "$ALPHA_ROOT"/dt=* 2>/dev/null | grep -oE '[0-9]{8}' | sort | tail -1)
log_line "结束 rc=$rc（alpha=${AL_NEW:-无}）"
exit "$rc"
