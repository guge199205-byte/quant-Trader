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
