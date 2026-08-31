# BayMax Arena 前端镜像：多阶段构建，客户 clone 后 docker compose up 即自动编译
# （无需宿主 Node 环境；dist/ 产物不入仓库）
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
RUN apk add --no-cache openssl    # entrypoint 生成 dsh.htpasswd（apr1 哈希）
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.tpl /etc/nginx/arena.conf.tpl
COPY docker-entrypoint.d.sh /docker-entrypoint.d/05-arena-envsubst.sh
