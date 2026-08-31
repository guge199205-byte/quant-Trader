# BayMax Arena 前端（nginx 静态托管 + /api 反代到 api 容器）
# ${API_TOKEN_CLEAN} 由 docker-entrypoint.d 脚本 envsubst 注入（去掉引号的 .env API_TOKEN）
server {
    listen 8092;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    # SPA 路由回退
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 静态资源长缓存（带 hash 文件名）
    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # API 同源反代（浏览器无需持有 token，nginx 注入）
    location /api/ {
        proxy_pass http://api:8091;
        proxy_set_header Host $host;
        proxy_set_header X-API-Token "${API_TOKEN_CLEAN}";
        proxy_read_timeout 60s;
    }
}

# 交易智能体（AI-HARNESS 嵌入）：独立端口 8093，与 8092 同站点（同 host 不同端口），
# iframe 内 localStorage 不受第三方存储隔离 → dsh settings 可用。
# 本身要求 basic auth（默认 admin/admin123，entrypoint 自动生成），并把浏览器凭据透传给上游 dsh-proxy。
# ${DSH_UPSTREAM} 由 entrypoint envsubst 注入（.env DSH_UPSTREAM 可改，默认 http://host.docker.internal:3081）
server {
    listen 8093;
    server_name _;

    auth_basic "dsh - Quant Agent Trader";
    auth_basic_user_file /etc/nginx/dsh.htpasswd;

    location / {
        proxy_pass ${DSH_UPSTREAM};
        proxy_set_header Host $http_host;
        proxy_set_header Authorization $http_authorization;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
