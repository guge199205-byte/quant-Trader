# AI 可执行部署指南（Deployment Runbook）

> 本文档是给 **AI agent**（Claude / GPT / DeepSeek 等）执行的部署 runbook，
> 也可供人类运维参考。每步包含：目标 → 命令 → 预期输出 → 失败处理。
> **严格按顺序执行**，每步验证通过后才进入下一步。遇到文档未覆盖的错误，
> 先看第 7 节故障排查表，再结合日志判断，不要跳过验证步骤。

执行环境：Linux（Debian/Ubuntu 系）+ Docker ≥ 24 + docker compose 插件。
全程在项目根目录执行（下称 `$ROOT`）。

---

## 0. 前置检查

```bash
docker --version && docker compose version
# 预期:两行版本号;docker compose 报 command not found → 先装 compose 插件
```

```bash
# 检查关键端口未被占用(若有进程占用先处理,否则服务起不来)
for p in 8091 8092 8093 8887 3081 8100 8104 8200 8204 8300 8304; do
  ss -tlnp 2>/dev/null | grep -q ":$p " && echo "⚠️  端口 $p 被占用" || true
done
```

## 1. 获取代码

```bash
cd $ROOT
git clone <仓库地址> quant-agent-trader && cd quant-agent-trader
```

```bash
# 关键文件齐全性检查(全部应存在,缺失说明 clone 不完整)
for f in docker-compose.yml .env.example Dockerfile arena/Dockerfile docker/entrypoint.sh \
         runtime_env.json.example runtime_env_cn.json.example runtime_env_hk.json.example \
         configs/default_config.json config/backend.yaml scripts/bootstrap_data.py; do
  [ -f "$f" ] && echo "✓ $f" || echo "✗ 缺失: $f"
done
```

## 2. 环境配置

```bash
cp .env.example .env
```

编辑 `.env`（至少配一个模型的 key，模拟盘才能跑；全部留空则 agent 无法调用 LLM）：

| 变量 | 必填 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` / `OPENAI_API_BASE` | 二选一 | DeepSeek 等 OpenAI 兼容 API（base 形如 `https://api.deepseek.com/v1`） |
| `GLM_API_KEY` / `GLM_API_BASE` | 可选 | 智谱（`https://open.bigmodel.cn/api/paas/v4`） |
| `JINA_API_KEY` | 可选 | 市场信息搜索（Jina Reader） |
| `API_TOKEN` | 可选 | 留空 = 不鉴权；公网暴露建议填随机串 |
| `DSH_UPSTREAM` | 可选 | 交易智能体面板反代地址，默认 `http://host.docker.internal:3081`（容器经 host-gateway 访问宿主 dsh） |

```bash
# 验证:key 已填(至少一个模型供应商)
grep -c "API_KEY=\"[^"]" .env && echo "✓ 至少一个 key 已填" || echo "⚠️  所有 key 为空,agent 无法调用 LLM"
```

## 3. 初始化市场数据（免费接口,约 3~6 分钟）

> 无需任何数据源 key。用 api 镜像跑（镜像已含全部 Python 依赖,免宿主环境）。

```bash
docker compose run --rm api python3 scripts/bootstrap_data.py
# 预期输出:三市场各自的行数汇总(如 "A股 SSE50 ... 50 只" / "NASDAQ100 ... 102 只" / 港股)
# 验证产物:
ls -la data/A_stock/merged.jsonl data/daily_prices_nasdaq100.json data/HK_stock/merged.jsonl 2>/dev/null
# 失败处理:网络超时 → 脚本内置重试,直接重跑一遍;腾讯/Yahoo 均不通 → 检查 curl 外网连通性
```

## 4. 构建 + 启动常驻服务

```bash
docker compose up -d --build
# 首次构建 5~15 分钟(拉 python/node 基础镜像 + npm ci + pip 依赖);之后秒级
```

```bash
# 验证:所有常驻服务 Up
docker compose ps --format "table {{.Name}}\t{{.Status}}"
# 预期:baymax-mcp-us/cn/hk、baymax-api、baymax-ui-arena、baymax-ui 全部 "Up"
# 注意:agent-* / datasync-* 默认不在列表属正常(profile 按需启动,见第 5/6 节)
```

```bash
# 验证:前端 + API 可访问
curl -s -o /dev/null -w "前端 8092: %{http_code}\n" http://localhost:8092/        # 预期 200
curl -s http://localhost:8091/api/metrics | head -c 200                           # 预期 JSON
# 失败处理:8092 非 200 → docker compose logs ui-arena(常见:htpasswd 未生成/npm 构建失败)
```

## 5. 交易 agent 冒烟测试（跑 1 天验证全链路）

```bash
docker compose --profile agents run --rm -e INIT_DATE=2026-08-28 -e END_DATE=2026-08-28 agent-cn
# 预期:日志显示 agent 加载行情 → LLM 推理 → 交易工具调用(模拟撮合) → 持仓落盘
# 验证产物:
ls data/agent_data_astock/*/position/position.jsonl 2>/dev/null || echo "⚠️  无持仓记录,看容器日志"
docker compose logs mcp-cn --tail 20   # MCP 工具服务日志,排查调用失败
```

```bash
# 打开前端确认竞技场出数据
# curl http://localhost:8092/ → 浏览器访问 <服务器IP>:8092 → 排行榜/实况页应有该 agent 记录
```

## 6. 可选功能（按需启用）

### 6.1 交易智能体面板 dsh（默认随 compose 启动,需 .env 的模型 key）

```bash
# 本机直连: http://localhost:3081 ;局域网: http://<服务器IP>:8093
# 默认凭据: admin / admin123 (entrypoint 首次启动自动生成,登录后请修改)
# 改密码:
docker compose exec ui-arena sh -c "printf 'admin:%s\n' \$(openssl passwd -apr1 '新密码') > /etc/nginx/dsh.htpasswd"
docker compose restart ui-arena
```

### 6.2 dsh 访问代理（默认启动,局域网共享可选）

dsh-proxy 默认绑 docker 网关 `172.17.0.1:3081`（供前端 8093 经 host-gateway 转发）。
局域网共享 dsh 时,`.env` 设 `DSH_BIND_IP=<本机局域网IP>`（如 192.168.31.68）后重启:

```bash
docker compose up -d dsh-proxy
```

### 6.3 A股实盘（可选,需 Windows 交易机）

通达信客户端登录 → 共享目录跑 `setup.ps1`（见 `brokers/tdx-bridge/README.md`）→
`.env` 填 `TDX_BRIDGE_URL=http://<windows-ip>:8550` + `TDX_BRIDGE_TOKEN=` → 重启 api。

### 6.4 QuantDB 数据底座（可选增强）

api 容器已预留 `/data/quantdb` 等只读挂载点；无该数据源时实盘/因子面板自动降级为空态,
模拟盘不受影响。

## 7. 故障排查表

| 症状 | 检查 | 修复 |
|------|------|------|
| `docker compose up` 报 env_file 错误 | 是否缺 `.service.env` | 正常——`required: false` 已兼容,检查 `.env` 存在即可 |
| 8092 白屏/502 | `docker compose logs ui-arena` | dist 未构建 → `docker compose build ui-arena`;htpasswd 报错 → entrypoint 自动生成,重启容器 |
| api/mcp 容器反复重启 | `docker compose logs api` 看 IsADirectoryError | runtime_env 被挂成目录(旧 compose 残留) → 检查宿主 `runtime_env*.json` 是否变成了目录,删除后 `docker compose up -d --force-recreate api` |
| agent 报 LLM 连接失败 | 容器日志 + `.env` key | 检查 `OPENAI_API_BASE` 拼写、key 是否过期;模型未启用 → `configs/*.json` 的 `models[]` 里 `enabled: true` |
| MCP 工具调用超时 | `docker compose ps` 看 mcp-* 是否 Up | 端口冲突(见第 0 步检查) → 改 `MATH_HTTP_PORT` 等并同步 `agent-*` 环境变量 |
| 实盘面板空态 | `docker compose logs api` | 无 quantmind 底座属正常(第 6.4 节);有底座仍空 → 检查挂载路径 `data/quantdb` |
| 8093 打不开 | `docker compose ps ui-arena` | DSH_UPSTREAM 指向不通 → `.env` 改 `http://host.docker.internal:3081` 或正确 dsh 地址（跨机场景用 `http://<dsh-ip>:3081`） |

## 8. 部署完成判定清单

- [ ] 第 0~4 步全部通过,`docker compose ps` 全 Up,8092 返回 200
- [ ] 第 5 步 agent 跑出 1 天成交记录,前端排行榜可见
- [ ] `.env` 中所有 key 为占位符 `<your-key>` 之外的真实值,且未被任何 agent 回显到日志
- [ ] （可选功能已启用时）8093 默认密码已修改

---

## 附:首次启动自动初始化（entrypoint 兜底,无需手工）

| 项 | 行为 |
|----|------|
| `runtime_env*.json` | 缺失时从同名 `.example` 占位模板复制（agent 运行环境配置） |
| `trade_cache.sqlite` | 缺失时创建空文件,首次查询自动建表建索引（position.jsonl 懒重建） |
| `dsh.htpasswd` | 缺失时生成默认凭据 **admin/admin123**（nginx basic auth） |
| quantmind 挂载点 | 宿主无 quantmind 仓库时 Docker 自动建空目录,功能自动降级,不阻塞启动 |
