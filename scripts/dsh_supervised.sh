#!/bin/bash
# dsh 内核守护启动器
#
# 思路借鉴 dsh_desktop 的「守护瀑布」（内核 boot 链逐级自愈），按本仓库的
# 实际失败模式裁剪——实测到的失败形态是：**一个坏插件导致整棵插件树加载失败**，
# 于是整个 profile 起不来（2026-09-13 宿主机 web profile 实录：
# dshmarket 引用了新版已不存在的 installSettingsSection、finance-data 的 tool
# schema 被新版校验拒绝 → 整树失败）。
#
# 本脚本做的事：
#   1. 启动 dsh；失败后从**日志里的失败签名**识别导致加载失败的插件条目
#      （不用「退出耗时」判定：dsh 先绑端口、后加载插件树，失败可能发生在启动 30s 后）
#   2. 把这些条目写进隔离补丁（disabled: true）后重试——**降级可用优于整树不起**
#   3. 有界重试：到上限就带诊断退出，交给上层告警，而不是无限重启
#
# 用法（其余参数原样透传给 dsh）:
#   dsh_supervised.sh --profile web --patch /app/dsh/baymax.cordis.yml --port 3081 --no-open
#
# 环境变量:
#   DSH_SUPERVISOR_STATE     状态目录（默认 ~/.dsh/.supervisor）
#   DSH_SUPERVISOR_ATTEMPTS  最大尝试次数（默认 3）
#   DSH_SUPERVISOR_NO_ISOLATE=1  只观察不隔离（排查用）
set -uo pipefail

STATE_DIR="${DSH_SUPERVISOR_STATE:-$HOME/.dsh/.supervisor}"
MAX_ATTEMPTS="${DSH_SUPERVISOR_ATTEMPTS:-3}"
ISOLATE_PATCH="$STATE_DIR/isolated.cordis.yml"
DSH_BIN="${DSH_BIN:-dsh}"

mkdir -p "$STATE_DIR"
# 空隔离补丁必须是合法 YAML 数组（空文件会被解析成 null，dsh 不认）
[ -f "$ISOLATE_PATCH" ] || echo '[]' > "$ISOLATE_PATCH"

log() { echo "[supervisor $(date '+%F %T')] $*"; }

# 从启动日志提取导致加载失败的插件条目 id。
# 实测格式（0.1.5-rc.1）:
#   Error: failed to apply loader entry finance-data (dsh-plugin-finance-data): ...
#   Error: failed to import loader entry dsh-market (dshmarket): ...
#
# ⚠️ 必须排除聚合条目 `include (cordis:include)`：它是 include 机制本身，
#    不是坏插件；禁掉它会把整棵树的加载入口一起关掉。
extract_failed_ids() {
    grep -oE 'failed to (apply|import) loader entry [^ ]+ \([^)]+\)' "$1" 2>/dev/null \
        | sed -E 's/.*entry ([^ ]+) \(([^)]+)\)/\1\t\2/' \
        | awk -F'\t' '$2 != "cordis:include" { print $1 }' \
        | sort -u
}

# 把 id 追加进隔离补丁（幂等：已隔离的不重复写）
isolate_ids() {
    local id
    for id in $1; do
        if grep -qE "^- id: ${id}$" "$ISOLATE_PATCH" 2>/dev/null; then
            log "条目 $id 已在隔离补丁中，跳过"
            continue
        fi
        # 占位空数组必须先清掉：`[]` 后面直接跟列表项是非法 YAML
        # （实测报错：end of the stream or a document separator is expected）
        if [ "$(tr -d '[:space:]' < "$ISOLATE_PATCH")" = "[]" ]; then
            : > "$ISOLATE_PATCH"
        fi
        {
            echo "- id: ${id}"
            echo "  disabled: true"
        } >> "$ISOLATE_PATCH"
        log "已隔离坏插件条目: $id （写入 $ISOLATE_PATCH）"
    done
}

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    LOG="$STATE_DIR/boot-${attempt}.log"
    log "第 ${attempt}/${MAX_ATTEMPTS} 次启动（隔离补丁: $ISOLATE_PATCH）"

    # 隔离补丁先于调用方参数传入：dsh --patch 可重复，后写的不覆盖先写的
    "$DSH_BIN" --patch "$ISOLATE_PATCH" "$@" 2>&1 | tee "$LOG"
    rc="${PIPESTATUS[0]}"

    if [ "$rc" -eq 0 ]; then
        exit 0
    fi

    if [ "${DSH_SUPERVISOR_NO_ISOLATE:-0}" = "1" ]; then
        log "rc=${rc}；DSH_SUPERVISOR_NO_ISOLATE=1，只观察不隔离"
        exit "$rc"
    fi

    failed_ids="$(extract_failed_ids "$LOG")"
    if [ -z "$failed_ids" ]; then
        log "rc=${rc} 但日志中无插件加载失败签名——非坏插件问题，原样返回让告警接管"
        log "日志: $LOG"
        exit "$rc"
    fi

    log "检测到插件树加载失败，失败条目: $(echo $failed_ids | tr '\n' ' ')"
    isolate_ids "$failed_ids"
    attempt=$(( attempt + 1 ))
done

log "已达最大尝试次数 ${MAX_ATTEMPTS}，启动仍失败——带诊断退出（上层告警接管，不无限重启）"
log "最后一次日志: $STATE_DIR/boot-${MAX_ATTEMPTS}.log"
log "已隔离条目:"
grep -E '^- id: ' "$ISOLATE_PATCH" 2>/dev/null | sed 's/^/  /'
exit 1
