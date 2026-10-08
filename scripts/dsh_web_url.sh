#!/bin/bash
# 打印 dsh web 当前可用的带 token 入口 URL
#
# 背景：dsh 0.1.5-rc.1 起，web 端新增**强制 token 鉴权**（0.1.1-rc.2 没有）。
# 浏览器首次访问必须打开带 ?token=... 的 URL 完成交换，服务端下发签名 cookie
# （有效期 30 天），此后同一浏览器/设备即可直接访问干净根路径。
#
# 关键性质（2026-09-13 实测）：
#   - cookie 的签名密钥持久化在 $DSH_HOME/.credentials.yaml，**容器重启不失效**
#     → 正常情况这是一次性操作，不必每次重启都做
#   - URL 里的 token 是**进程级**的，随 dsh 重启更换 → 仅用于「cookie 丢了」时重新交换
#   - cookie 按 authority(host:port) 绑定 → **必须在你实际访问的那个地址上做交换**
#     （从 3081 换来的 cookie 不能用在 8093 上，反之亦然）
#
# 访问拓扑（两条路，别搞混）：
#   ① arena 前端「交易智能体」(/harness)  →  http://<host>:8093
#      （SPA 里写死 `http://${window.location.hostname}:8093`）
#   ② 直连 dsh-proxy                       →  http://<host>:3081
#   两条都在 dsh-proxy 一层要求 nginx Basic 认证，过了才到 dsh 的 token 鉴权。
#
# 用法:
#   bash scripts/dsh_web_url.sh
#   LAN_HOST=其他主机名 bash scripts/dsh_web_url.sh
set -uo pipefail

CONTAINER="${DSH_CONTAINER:-baymax-dsh}"

while [ $# -gt 0 ]; do
    case "$1" in
        --container) CONTAINER="${2:-}"; shift 2 ;;
        --host) LAN_HOST="${2:-}"; shift 2 ;;
        --help|-h) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "未知参数: $1（可用 --container / --host）" >&2; exit 2 ;;
    esac
done

token_line=$(docker logs "$CONTAINER" 2>&1 | grep -oE 'http://[^ ]*token=[A-Za-z0-9_-]+' | tail -1)
if [ -z "$token_line" ]; then
    echo "❌ 未在 $CONTAINER 的日志里找到带 token 的启动行。" >&2
    echo "   可能原因：容器未运行 / 日志已轮转。" >&2
    echo "   注意：重启会换 token，但**已交换过的 cookie 仍然有效**，" >&2
    echo "         所以只有 cookie 丢了才需要重新取。" >&2
    exit 1
fi

token="${token_line#*token=}"
# 容器内监听 127.0.0.1:<port>，对外走代理
port="$(echo "$token_line" | sed -nE 's|.*:([0-9]+)/?\?token=.*|\1|p')"
LAN_HOST="${LAN_HOST:-192.168.31.68}"
ARENA_PORT="${ARENA_PORT:-8093}"

echo "请用浏览器打开【你实际访问的那个地址】对应的一条（首次需要，之后同一浏览器免 token）："
echo
echo "  ① 走 arena 前端「交易智能体」(/harness)："
echo "     http://${LAN_HOST}:${ARENA_PORT}/?token=${token}"
echo
echo "  ② 直连 dsh（代理端口 ${port}）："
echo "     http://${LAN_HOST}:${port}/?token=${token}"
echo
echo "说明：会先要求 nginx Basic 认证（默认 admin/admin123，如已改密用新的），"
echo "      然后 dsh 下发签名 cookie（30 天）。"
echo
echo "⚠️  cookie 按 host:port 绑定：在 3081 换来的 cookie 不能用在 8093 上（反之亦然），"
echo "    所以必须选对你实际访问的那个地址。若用别的主机名访问，加 --host <主机名>。"
echo "ℹ️  签名密钥持久化在 \$DSH_HOME/.credentials.yaml，容器重启不会让 cookie 失效。"
