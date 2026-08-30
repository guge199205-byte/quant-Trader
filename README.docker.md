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
│ api (8091)  │   后端 API（数据/鉴权，双前端共用）
├─────────────┤
│ ui-nof0     │   Quant-Agent-Trader 实时看板（8080，静态服务）
├─────────────┤
│ ui-arena    │   Arena 竞技场（8092，nginx 反代 8091 + token 注入）
├─────────────┤
│ ui (8887)   │   docs 静态快照（8888 被 1Panel、8889 被 jupyter 占用）
└─────────────┘

agent-us / agent-cn / agent-hk：按市场独立容器，profile: agents
  agent → MCP 通过 MCP_HOST 环境变量指向对应 mcp 容器
```

## 常用命令

```bash
cd /path/to/quant-agent-trader

# 启动全部常驻服务（mcp×3 + api + dsh + ui）
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
| api | 8091 | FastAPI 后端 API（数据/鉴权，双前端共用） |
| ui-nof0 | 8080 | Quant-Agent-Trader 实时看板（原 start_nof0.sh 端口） |
| ui-arena | 8092 | Arena 竞技场（nginx 反代 8091 + envsubst token 注入） |
| dsh | 3081 | DeepSeek Harness（绑定宿主 127.0.0.1，本机直连） |
| dsh-proxy | 3081 (LAN IP) | dsh 局域网代理（nginx basic auth，密码在 `dsh/proxy/dsh.htpasswd`，改完重启容器） |
| ui | 8887 | docs 静态快照（8888/8889 被占） |

与 `scripts/alert.sh` 的探活端口一致（api:8091 mcp_us:8100 mcp_cn:8200 mcp_hk:8300 dsh:3081），宿主 cron 告警无需改动。

## 数据与持久化

运行时数据 bind-mount 到宿主（宿主 cron 的 backup.sh / alert.sh 照常工作）：

- `data/` `logs/` `configs/` `trade_cache.sqlite` — 全挂载
- `config/backend.yaml` — api 配置
- `runtime_env.json / _cn / _hk` — 各市场运行时环境（改 TODAY_DATE 等直接生效）
- `docs/` `nof0/` — 前端静态文件
- `dsh/proxy/` — dsh 局域网代理配置（nginx.conf + dsh.htpasswd，htpasswd 已 gitignore）
- `.env` / `.service.env` — 密钥不打包进镜像，仅以 env_file 注入

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
- `auto-heal.sh`：检查 baymax-mcp-us/cn/hk、baymax-api、baymax-dsh、baymax-ui、baymax-ui-nof0 是否运行，掉线即拉起，日志 `logs/auto-heal.log`
- 模拟成交数据：`python scripts/simulate_demo_trades.py`（A股/港股追加 buy/sell 演示记录，价格取真实数据，运行前自动备份）

## 前端交易面板数据链路（已修）

"成交记录/分析"面板空白根因链：
1. `nof0/data` 是指向 `../data` 的**符号链接**，`python -m http.server` 默认不跟随 → 8080 上相对路径 `data/...` 请求 404
2. `transaction-loader.js` 硬编码相对路径 `data/${agentDataDir}/...`（没走 api_base）→ 修复：改走 `configLoader.getDataPath()`（有 api_base 时 = `http://192.168.31.68:8091/api/data`）
3. ui-nof0 容器只挂 nof0/，容器内 symlink 目标 `/app/data` 不存在 → 补 `./data:/app/data` 挂载
4. 静态服务改用 `scripts/serve_nof0.py`（`follow_symlinks=True` 子类；注意它是**类属性**不是 __init__ 参数）

改 JS 后需 bump `index.html` 里的 `?v<时间戳>` 缓存版本号，否则浏览器命中旧缓存。

## 已知注意事项

- **dsh 拒绝绑 0.0.0.0**（防远程代码执行），因此 dsh 容器用 `network_mode: host`，只监听宿主 127.0.0.1:3081
- 宿主 venv（`.venv`）同样有 adapters 0.3.0 的 bug，若还要在宿主直接跑 agent：
  `.venv/bin/pip install langchain-mcp-adapters==0.2.2`
- 镜像内时区为 UTC，runtime_env 的日期按数据流程管理，与容器时区无关
- 容器以 root 运行（bind-mount 文件宿主属主 uid 1000，root 可写；不需要改属主）
