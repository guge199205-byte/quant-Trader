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

# ③ 鉴权 cookie 兜底：dsh-cookies.conf 由 scripts/dsh_auth_cookie.py 生成并挂载进来。
#    缺失时写一个透传 map，保证 nginx 能起（$dsh_auth_cookie 未定义会让 nginx 直接拒启动）。
#    透传 = 回到"需要手动 token 交换"的行为，是安全降级，不是静默失效。
if [ ! -f /etc/nginx/conf.d/dsh-cookies.conf ]; then
  cat > /etc/nginx/conf.d/dsh-cookies.conf <<'EOF'
# 兜底：未生成 dsh-cookies.conf → 透传客户端 cookie（不注入）
# 生成方法：python3 scripts/dsh_auth_cookie.py && docker restart baymax-dsh-proxy
map $http_cookie $dsh_auth_cookie {
    default $http_cookie;
}
EOF
  echo "[dsh-proxy] ⚠️ dsh-cookies.conf 缺失 → 已生成透传兜底 map；浏览器可能需要手动 token 交换"
fi
