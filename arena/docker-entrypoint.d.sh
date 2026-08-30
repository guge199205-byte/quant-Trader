#!/bin/sh
# 在 nginx 官方镜像 entrypoint 阶段把 .env 的 API_TOKEN（可能带引号）清洗后
# 注入 nginx 配置（envsubst 到 conf.d/default.conf）
set -e
API_TOKEN_CLEAN=$(printf '%s' "$API_TOKEN" | tr -d '"')
export API_TOKEN_CLEAN
envsubst '$API_TOKEN_CLEAN' < /etc/nginx/arena.conf.tpl > /etc/nginx/conf.d/default.conf
echo "[arena] nginx config rendered (token: ${#API_TOKEN_CLEAN} chars)"
