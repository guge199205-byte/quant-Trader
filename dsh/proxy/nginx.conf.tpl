# dsh 访问代理：dsh 官方硬拒绑 0.0.0.0（防 RCE 外露），
# 保持 dsh 绑 127.0.0.1:3081，此 nginx 绑 ${DSH_BIND_IP}:3081 + basic auth，
# 转发回本机 127.0.0.1:3081。本机 localhost:3081 直连 dsh 不受影响。
# ${DSH_BIND_IP} 由 entrypoint envsubst 注入（.env DSH_BIND_IP 可改：
# 默认 172.17.0.1 docker 网关——让 bridge 容器经 host.docker.internal 可达；
# 局域网共享场景设为本机局域网 IP，如 192.168.31.68）
server {
    listen ${DSH_BIND_IP}:3081;
    server_name _;

    auth_basic "dsh - Quant Agent Trader";
    auth_basic_user_file /etc/nginx/dsh.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:3081;
        # 用 $http_host 保留端口（$host 会丢端口，dsh 的 trusted-host fence 按 host:port 精确匹配）
        proxy_set_header Host $http_host;
        # 禁掉上游压缩：sub_filter 只能在明文响应上替换（否则注入不了 polyfill）
        proxy_set_header Accept-Encoding "";
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE / WebSocket 支持（dsh UI 实时流）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;

        # crypto.randomUUID 只在 secure context（HTTPS/localhost）可用；
        # 局域网 HTTP 访问时 undefined → dsh 工作区/提供方目录加载失败。
        # 在 </head> 前注入 UUID v4 polyfill（crypto.getRandomValues 非 secure context 可用）。
        sub_filter '</head>' "<script>if(!globalThis.crypto||!globalThis.crypto.randomUUID){var c=globalThis.crypto||(globalThis.crypto={});c.randomUUID=function(){var a=new Uint8Array(16);if(typeof crypto.getRandomValues==='function'){crypto.getRandomValues(a)}else{for(var i=0;i<16;i++)a[i]=Math.floor(Math.random()*256)}a[6]=a[6]&15|64;a[8]=a[8]&63|128;return Array.prototype.map.call(a,function(v,i){return(i===4||i===6||i===8||i===10?'-':'')+(v<16?'0':'')+v.toString(16)}).join('')}}</script></head>";
        sub_filter_once on;
        sub_filter_types text/html;
    }
}
