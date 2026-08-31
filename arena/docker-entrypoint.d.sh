#!/bin/sh
# 在 nginx 官方镜像 entrypoint 阶段：
# ① .env 的 API_TOKEN（可能带引号）清洗后注入 nginx 配置（envsubst）
# ② dsh 上游地址模板化（DSH_UPSTREAM，默认 host.docker.internal:3081——
# 容器经 host-gateway 访问宿主上 host 网络的 dsh；.env 可覆盖）
# ③ dsh.htpasswd 缺失时生成默认凭据（admin/admin123，日志提示改密）——客户 clone 后零配置可登录
set -e

API_TOKEN_CLEAN=$(printf '%s' "$API_TOKEN" | tr -d '"')
export API_TOKEN_CLEAN
export DSH_UPSTREAM="${DSH_UPSTREAM:-http://host.docker.internal:3081}"

if [ ! -f /etc/nginx/dsh.htpasswd ]; then
  PASSWORD_HASH=$(openssl passwd -apr1 'admin123')
  printf 'admin:%s\n' "$PASSWORD_HASH" > /etc/nginx/dsh.htpasswd
  echo "[arena] dsh.htpasswd 不存在 → 已生成默认凭据 admin/admin123（请登录后尽快修改）"
fi

envsubst '$API_TOKEN_CLEAN $DSH_UPSTREAM' < /etc/nginx/arena.conf.tpl > /etc/nginx/conf.d/default.conf
echo "[arena] nginx config rendered (token: ${#API_TOKEN_CLEAN} chars, dsh: ${DSH_UPSTREAM})"
