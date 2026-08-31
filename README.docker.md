# Quant-Agent-Trader Docker 部署

容器化编排，替代原 systemd user 服务 + 手动启动 agent 的方式。

## 架构

```
                        ┌─────────────┐
                        │  dsh (3081) │  host 网络，绑宿主 127.0.0.1
                        └──────┬──────┘
                               │ localhost:8100-8104（宿主已发布的 mcp-us 端口）
┌─────────────┐   ┌──────────────┴───────────────┐
│ mcp-us      │   │  8100-8104  math/search/     │
│ (8100-8104) │   │  trade/price/memory          │
├─────────────┤   └──────────────────────────────┘
│ mcp-cn      │   8200-8204（同上 5 个服务）
├─────────────┤
│ mcp-hk      │   8300-8304
├─────────────┤
│ api (8091)  │   后端 API（数据/鉴权，前端共用）
├─────────────┤
│ ui-arena    │   Arena 竞技场（唯一前端,8092，nginx 反代 8091 + token 注入）
├─────────────┤
│ ui (8887)   │   docs 静态快照（8888 被 1Panel、8889 被 jupyter 占用）
└─────────────┘

agent-us / agent-cn / agent-hk：按市场独立容器，profile: agents
  agent → MCP 通过 MCP_HOST 环境变量指向对应 mcp 容器
```

## 常用命令

```bash
cd /path/to/quant-agent-trader

# 首次先构建（ui-arena 多阶段编译前端,无需宿主 Node）
docker compose up -d --build
# 之后日常启动
docker compose up -d

# 查看状态 / 日志
docker compose ps
docker compose logs -f mcp-cn

# 跑交易 agent（按市场）
docker compose --profile agents up -d agent-cn    # A股
docker compose --profile agents up -d agent-us    # 美股
docker compose --profile agents up -d agent-hk    # 港股
docker compose --profile agents logs -f agent-cn  # 看 agent 日志

# 数据更新（一次性任务）
docker compose --profile datasync run --rm datasync-us
docker compose --profile datasync run --rm datasync-cn

# 全部停止
docker compose down
```

## 端口

| 服务 | 宿主端口 | 说明 |
|------|---------|------|
| mcp-us | 8100-8104 | math/search/trade/price/memory |
| mcp-cn | 8200-8204 | 同上 |
| mcp-hk | 8300-8304 | 同上 |
| api | 8091 | FastAPI 后端 API（数据/鉴权，前端共用） |
| ui-arena | 8092 | Arena 竞技场（唯一前端，nginx 反代 8091 + envsubst token 注入） |
| dsh | 3081 | DeepSeek Harness（绑定宿主 127.0.0.1，本机直连） |
| dsh-proxy | 3081 (DSH_BIND_IP) | dsh 访问代理（默认启动,绑 docker 网关 172.17.0.1:3081 供 8093 转发;局域网共享设 `DSH_BIND_IP=<LAN IP>`;basic auth 密码 admin/admin123 容器内自动生成） |
| ui | 8887 | docs 静态快照（8888/8889 被占） |

与 `scripts/alert.sh` 的探活端口一致（api:8091 mcp_us:8100 mcp_cn:8200 mcp_hk:8300 dsh:3081），宿主 cron 告警无需改动。

## 数据与持久化

运行时数据 bind-mount 到宿主（宿主 cron 的 backup.sh / alert.sh 照常工作）：

- `data/` `logs/` `configs/` — 全挂载
- `config/backend.yaml` — api 配置
- `docs/` — 静态快照
- `dsh/proxy/` — dsh 局域网代理配置（nginx.conf；htpasswd 由 ui-arena entrypoint 自动生成，容器内）
- `.env` / `.service.env` — 密钥不打包进镜像，仅以 env_file 注入（`.service.env` 缺失也能启动）

**不在宿主挂载、由 entrypoint 容器内初始化**（Docker 对缺失的文件源会挂成"目录"导致服务崩溃）：

- `trade_cache.sqlite` — 交易索引，缺失自动创建空文件（position.jsonl 懒重建）
- `runtime_env.json / _cn / _hk` — 各市场运行时环境，缺失自动从 `.example` 占位模板复制（改 TODAY_DATE 等需进容器改,`docker compose exec api sh`）

## 相对原 systemd 的改动

1. **代码（3 处）**：
   - `agent/base_agent/base_agent.py` + `agent/base_agent_astock/base_agent_astock.py`：MCP URL 的 `localhost` → `os.getenv('MCP_HOST', 'localhost')`（跨容器连接）
   - `agent_tools/tool_*.py`（5 个）：`mcp.run()` 加 `host="0.0.0.0"`（容器内默认只绑 127.0.0.1）
2. **依赖锁**：`requirements.lock.txt` 由 `.venv` pip freeze 生成；`langchain-mcp-adapters` 从 0.3.0 降到 **0.2.2**——0.3.0 的 `sessions.py` 还在 import 已被移除的 `streamable_http_client`（旧名），任何兼容版本都会炸；0.2.2 用新名，与 mcp 1.16.0 / fastmcp 2.12.5 兼容
3. **systemd 单元已停**（未删除）：`systemctl --user stop baymax-*`。想回退：
   ```bash
   docker compose down && systemctl --user start baymax-api baymax-dsh baymax-mcp-cn baymax-mcp-hk baymax-mcp-us
   ```

## 生产级持久化（宿主 cron，防交易中断）

```cron
# 每分钟：宿主侧探活（写 logs/service_status.json，api /api/metrics 读它）
* * * * * bash /path/to/quant-agent-trader/scripts/status-probe.sh
# 每分钟：常驻容器掉线自动 docker compose up -d 拉起（agent 是按需任务不自动拉起）
* * * * * bash /path/to/quant-agent-trader/scripts/auto-heal.sh
# 每 5 分钟：告警（含 status-probe 联动，掉线立即知道）
*/5 * * * * bash /path/to/quant-agent-trader/scripts/alert.sh
```

- `status-probe.sh`：宿主侧 socket 探活 api/mcp×3/dsh（容器内探不到宿主回环上的 dsh），结果 JSON 写 `logs/service_status.json`（bind-mount 进 api 容器，360s 新鲜度）
- `auto-heal.sh`：检查 baymax-mcp-us/cn/hk、baymax-api、baymax-dsh、baymax-ui-arena 是否运行，掉线即拉起，日志 `logs/auto-heal.log`
- 模拟成交数据：`python scripts/simulate_demo_trades.py`（A股/港股追加 buy/sell 演示记录，价格取真实数据，运行前自动备份）

## 前端交易面板数据链路

Arena(8092) 经 nginx 反代 `/api` 到 FastAPI(8091)，token 由 envsubst 注入，浏览器无需持 key。
改前端代码后 `docker compose build ui-arena && docker compose up -d ui-arena`（多阶段构建；8093 交易智能体凭据 admin/admin123，entrypoint 首次自动生成）。

## 已知注意事项

- **dsh 拒绝绑 0.0.0.0**（防远程代码执行），因此 dsh 容器用 `network_mode: host`，只监听宿主 127.0.0.1:3081
- 宿主 venv（`.venv`）同样有 adapters 0.3.0 的 bug，若还要在宿主直接跑 agent：
  `.venv/bin/pip install langchain-mcp-adapters==0.2.2`
- 镜像内时区为 UTC，runtime_env 的日期按数据流程管理，与容器时区无关
- 容器以 root 运行（bind-mount 文件宿主属主 uid 1000，root 可写；不需要改属主）
