# dsh 访问代理镜像：nginx + openssl（entrypoint 生成 htpasswd / envsubst 绑定 IP）
FROM nginx:alpine
RUN apk add --no-cache openssl
COPY docker-entrypoint.d.sh /docker-entrypoint.d/05-dsh-proxy-envsubst.sh
