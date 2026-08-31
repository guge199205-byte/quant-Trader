#!/bin/sh
# dsh-proxy（nginx）启动前：
# ① htpasswd 缺失时生成默认凭据 admin/admin123（与 ui-arena 8093 一致，日志提示改密）
# ② DSH_BIND_IP 模板化注入（默认 172.17.0.1 docker 网关，bridge 容器可经 host-gateway 访问）
set -e

if [ ! -f /etc/nginx/dsh.htpasswd ]; then
  PASSWORD_HASH=$(openssl passwd -apr1 'admin123')
  printf 'admin:%s\n' "$PASSWORD_HASH" > /etc/nginx/dsh.htpasswd
  echo "[dsh-proxy] dsh.htpasswd 不存在 → 已生成默认凭据 admin/admin123（请登录后尽快修改）"
fi

export DSH_BIND_IP="${DSH_BIND_IP:-172.17.0.1}"
envsubst '$DSH_BIND_IP' < /etc/nginx/conf.d/dsh.tpl > /etc/nginx/conf.d/default.conf
echo "[dsh-proxy] nginx config rendered (bind: ${DSH_BIND_IP}:3081)"
