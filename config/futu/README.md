# 富途 OpenD RSA 私钥

本目录存放富途 OpenD 网关的 RSA 私钥文件 `rsa.key`（协议加密握手必需）。

- **不入库**：`rsa.key` 已在 `.gitignore` 排除，切勿提交或粘贴到公开场合。
- 获取方式：本机 OpenD（`Futu_OpenD_*.exe/xml` 配置）生成协议加密密钥对时产生的
  私钥文件；或参考 `brokers/futu/` 工具链文档。
- 后端读取顺序（`backend/services/futu_live.py`）：
  1. 环境变量 `FUTU_RSA_KEY` 指定的路径
  2. 本目录的 `rsa.key`（compose 已将 `./config` 挂载到容器 `/app/config`）
- OpenD 网关地址由 `FUTU_OPEND_HOST` / `FUTU_OPEND_PORT` 配置；
  未设置时自动探测（容器内取默认路由网关，宿主机直跑回退 `127.0.0.1`）。
