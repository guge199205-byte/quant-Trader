# 🤖 BayMax-Trader — AI 自主交易竞技场

> AI 模拟交易平台：多个 AI 模型以独立资金池在 **美股（US）**、**A股（CN）**、**港股（HK）** 三个市场自主分析、决策、买卖，无人工干预。
> Docker 化部署，行情数据来自**本机 quantmind 数据仓库**（不再依赖免费行情 API）。

---

## ✨ 核心特性

| 能力 | 说明 |
|------|------|
| 🇺🇸🇨🇳🇭🇰 **三市场并行** | US（102 只）/ CN（上证50）/ HK 同时交易，各自独立 MCP 服务组（8100-8104 / 8200-8204 / 8300-8304）、独立状态、独立数据目录 |
| 🧠 **多模型竞争** | 每市场可配置多个模型（`configs/*.json` 的 `enabled` 开关），同池竞赛、排行榜对比 |
| 🐳 **Docker 化** | compose 编排全部服务：MCP×3、API、前端、dsh 引擎、agent 按需运行；宿主 cron 探活自愈 |
| 📊 **本地数据仓库** | 行情从本机 quantmind 仓库同步（quantdb A股后复权 / quantus 美股前复权 / NDX 基准），HK 用腾讯补齐 |
| 📝 **交易记忆系统** | 每市场独立记忆文件，agent 开盘读心得、收盘写经验，超 200 行自动归档 |
| 🛡️ **风控网关** | 单笔/持仓限额、日亏熔断（权益口径）、现金保留、黑名单，三条交易路径单点拦截 |
| 🔌 **Broker 可插拔** | sandbox（模拟盘）/ tdx（通达信桥，下单待移植）/ futu / tiger / ibkr（规划中） |
| ⚡ **双前端实时化** | AI-agent 实时看板（8080，四页：实盘/排行榜/模型/总控）+ Arena 竞技场（8092，coke-nof1 终端风：实况/排行榜/模型/总控/关于），同接 FastAPI 8091，交易结果即时可见 |
| 🤖 **双模型对决** | DeepSeek V4 Flash · V4 Pro 零样本竞技：同一数据、同一工具集、同一起点资金公平对决 |

---

## 🏗️ 架构总览（Docker）

```
┌─────────────┐   api_base    ┌──────────────┐   /api/data   ┌──────────┐
│  前端 8080   │ ────────────▶ │  FastAPI     │ ────────────▶ │  data/   │
│  ui-nof0    │               │  API (8091)  │               │ 实时数据  │
└─────────────┘               └──────┬───────┘               └──────────┘
                                     │
        ┌────────────────────────────┼───────────────────────────┐
        │ MCP 服务组（每市场 5 个：math/search/trade/price/memory）│
        │ mcp-us 8100-8104   mcp-cn 8200-8204   mcp-hk 8300-8304  │
        └──────────────┬─────────────────────┬───────────────────┘
                       │ MCP_HOST            │ MCP_HOST
        ┌──────────────▼─────┐   ┌───────────▼─────────┐
        │ agent-us / -cn / -hk │   │  dsh (3081)        │
        │ (--profile agents)   │   │ DeepSeek Harness   │
        └─────────────────────┘   └─────────────────────┘
```

**交易路径**（main.py agent / dsh agent / broker API）→ 全部经 `tool_trade.buy/sell` 落盘
→ **风控网关单点拦截** → position.jsonl 写入 → 前端实时读取。

**数据链路**：本机 `/home/zbox/projects/quantmind/data/`（Hive 分区 parquet）
→ `scripts/sync_from_quantmind.py` → `data/` 下的 AlphaVantage 格式价格文件 → agent 与前端共用。

---

## 🚀 快速开始（Docker）

### 环境准备

```bash
# 1. .env（密钥，不入镜像，仅 env_file 注入）
OPENAI_API_BASE="https://api.deepseek.com/v1"
OPENAI_API_KEY="你的Key"
JINA_API_KEY="你的Key"
RUNTIME_ENV_PATH="/home/zbox/BayMax-Trader/runtime_env.json"
```

### 启动

```bash
# 启动全部常驻服务（MCP×3 + API + 前端×2 + dsh）
docker compose up -d

# 跑交易 agent（按市场，LLM 逐日执行）
docker compose --profile agents run --rm agent-us   # 美股
docker compose --profile agents run --rm agent-cn   # A股
docker compose --profile agents run --rm agent-hk   # 港股
# 指定日期范围（环境变量覆盖 config 的 date_range）
docker compose --profile agents run --rm -e INIT_DATE=2026-08-25 -e END_DATE=2026-08-25 agent-us

# 查看状态 / 日志
docker compose ps
docker compose logs -f mcp-cn
```

### 访问

| 服务 | 地址 | 说明 |
|------|------|------|
| AI-agent 实时看板 | http://192.168.31.68:8080 | 实盘（净值/持仓/成交/分析）/ 排行榜 / 模型 / 总控，三市场切换 |
| Arena 竞技场 | http://192.168.31.68:8092 | coke 终端风：实况 / 排行榜 / 模型 / 总控 / 关于（nginx 同源反代 8091，token 自动注入） |
| API | http://192.168.31.68:8091 | `/api/overview` `/api/metrics` `/api/agents?market=` `/api/data/*` |
| dsh Web | http://localhost:3081 | agent 会话/工具调用可视化（绑宿主 127.0.0.1） |

---

## 📊 数据源：本地 quantmind 仓库

价格数据**全部来自本机 quantmind 数据仓库**（`/home/zbox/projects/quantmind/data/`），
覆盖前自动备份到 `/tmp/baymax_quantmind_backup_<ts>/`：

```bash
python scripts/sync_from_quantmind.py
```

| 市场 | 本地来源 | 输出 | 覆盖 |
|------|---------|------|------|
| A股 | quantdb `daily_backward`（后复权）+ `index_daily` 000016.SH | `data/A_stock/daily_prices_sse_50.csv`、`merged.jsonl`、`index_daily_sse_50.json` | 50/50 只 |
| 美股 | quantus `daily_forward`（前复权）+ `index_daily` NDX.US | `data/daily_prices_*.json`、`Adaily_prices_QQQ.json`（内容为纳指100 指数，作 QQQ 基准） | 89/102 只，缺口不持仓无影响 |
| 港股 | quantHK 覆盖不足（仅 3/29） | — | 保留腾讯数据（`HK_stock/merged.jsonl`、`hsi_daily.json`） |

> 行情/持仓/日志数据（`data/daily_prices_*.json`、`merged.jsonl`、`agent_data*`、`*.sqlite`）**不进 git**，已在 .gitignore。

---

## 🧠 交易记忆（越用越好用）

每市场独立记忆文件（`market_memory.md`），agent 通过 MCP 工具读写：

| 工具 | 时机 | 内容 |
|------|------|------|
| `read_memory()` | 开盘决策前 | 回顾策略心得/成功案例/失败教训/市场观察 |
| `append_memory(section, content)` | 收盘后 | 沉淀今日经验（分区白名单：策略心得/成功案例/失败教训/市场观察/待改进） |

- 记忆文件：US `data/agent_data/market_memory.md` / CN `data/agent_data_astock/market_memory.md`
- 超 200 行自动归档 `market_memory.archive.md`，重建骨架防膨胀

---

## 🛡️ 风控规则（`config/backend.yaml` → `risk`）

| 规则 | 默认 | 说明 |
|------|------|------|
| 单笔限额 | ≤ 权益 20% | 金额 = 价格 × 数量 |
| 持仓限额 | 单标 ≤ 权益 20% | 买入后该标市值占比 |
| 日亏熔断 | 5% | 权益口径（含持仓市值），卖出永远放行可止损 |
| 现金保留 | $100 | 交易后最低现金 |
| 黑名单 | 空 | 禁用标的 |
| 审批位 | false | 实盘 broker 上线时置 true |

---

## 🔌 扩展：Broker 与数据源

```python
# broker（下单源）：实现 agent_tools/brokers/base.py 的 Broker 接口
class MyBroker(Broker):
    name = "mybroker"
    def buy(self, signature, today_date, symbol, amount, price=None): ...
    # 注册后 backend.yaml broker.default 切换

# 数据源（行情源）：实现 agent_tools/datasources/base.py 的 DataSource 接口
class MySource(DataSource):
    name = "mysource"
    def get_quote(self, symbol, date, market): ...
```

已内置：`sandbox`（模拟盘）/ `tdx`（通达信 8550 桥）；规划：futu（OpenD）/ tiger / ibkr。

---

## 🔌 API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/overview` | 三市场 agent 聚合（含 summary 扩展指标、最新日期） |
| `GET /api/metrics` | 服务健康（api/mcp_us/mcp_cn/mcp_hk/dsh）+ 各市场统计 + 最近交易时间 |
| `GET /api/status` | 服务健康 + 当前运行状态 |
| `GET /api/config` | 后端配置（脱敏） |
| `GET /api/agents?market=` | agent 列表（按市场过滤） |
| `GET /api/agents/{a}/positions` | 持仓序列（每日快照） |
| `GET /api/agents/{a}/trades` | 交易流水（顶层 `{date, action, symbol, amount, cash_after}`） |
| `GET /api/agents/{a}/logs` | 决策日志（`{signature, new_messages[]}`） |
| `GET /api/agents/{a}/performance?market=` | 净值/收益/回撤（排行榜按市场切换） |
| `GET /api/data/{path}` | 实时代理（根目录 data/ 优先） |

---

## 🛠️ 运维

### 生产级持久化（宿主 cron，防交易中断）

```cron
* * * * * bash /home/zbox/BayMax-Trader/scripts/status-probe.sh   # 探活写 logs/service_status.json
* * * * * bash /home/zbox/BayMax-Trader/scripts/auto-heal.sh      # 常驻容器掉线自动拉起（agent 除外）
*/5 * * * * bash /home/zbox/BayMax-Trader/scripts/alert.sh        # 告警（联动 status-probe）
```

### 常用脚本

| 脚本 | 用途 |
|------|------|
| `scripts/sync_from_quantmind.py` | 从本地 quantmind 仓库同步三市场价格 |
| `scripts/backfill_us_agent.sh` | 补跑 US agent 缺失交易日（自动备份/恢复持仓行，失败 trap 兜底） |
| `scripts/simulate_demo_trades.py` | 生成演示成交记录（价格取真实数据，运行前自动备份） |
| `scripts/serve_nof0.py` | 前端静态服务（跟随 symlink） |

### 回退 systemd

原 systemd user 服务已停（未删）：`docker compose down && systemctl --user start baymax-*`

---

## 📁 目录结构

```
agent_tools/
├── brokers/          # Broker 抽象层（sandbox/tdx 注册表）
├── datasources/      # 数据源抽象层（local/tdx 注册表）
├── risk.py           # 风控网关（单点校验 + RiskGateway 包装）
├── tool_trade.py     # 交易执行（风控接入）
├── tool_memory.py    # 交易记忆（每市场独立）
└── start_mcp_services.py  # MCP 服务编排（US 8100-8104 / CN 8200-8204 / HK 8300-8304）
backend/              # FastAPI 层（api_server.py + services/agent_data.py）
config/backend.yaml   # 后端总配置（server/markets/broker/datasource/risk）
configs/              # agent 配置（default_config.json US / astock_config.json CN / deepseek_*_test.json）
scripts/              # sync_from_quantmind.py / backfill_us_agent.sh / serve_nof0.py / 探活自愈
dsh/                  # DeepSeek Harness 集成（persona + MCP 挂载）
nof0/                 # AI-agent 实时看板前端（8080，中文化，响应式，浏览器带 token）
arena/                # Arena 竞技场前端（8092，coke-nof1 终端风复刻，React+Vite，nginx 注入 token）
data/                 # 交易数据（gitignore；价格文件 + agent_data/ US + agent_data_astock/ CN）
docs/                 # 规划与架构文档
```

---

## ⚠️ 常见问题

| 问题 | 解决 |
|------|------|
| 前端不实时 | 确认 `nof0/config.yaml` 的 `api_base` 指向 API 地址；浏览器强刷（Ctrl+Shift+R） |
| 前端改了 JS 不生效 | bump `index.html` 里的 `?v<时间戳>` 缓存版本号 |
| A股/港股曲线空白 | 跑 `python scripts/sync_from_quantmind.py`（A股）；HK 确认 `data/HK_stock/merged.jsonl` 非空 |
| 排行榜市场切换不更新 | 已修复：`loadPerformance` 带 `?market=` 参数 |
| agent 报 EBUSY / runtime_env.json 删不掉 | 容器 bind-mount 不可 unlink，main.py 已降级为截断（无需处理） |
| agent 跑错模型（目录变成 gpt-5 等） | 确认 `configs/*.json` 的 `enabled` 模型；补跑历史曲线用 `deepseek_us_test.json` |
| 重建环境后 MCP client 报错 | `pip install langchain-mcp-adapters==0.2.2`（0.3.0 是坏发布，见 README.docker.md） |
| 端口冲突 | 8888=1Panel、8889=jupyter-lab，勿占用；MCP 端口经 .env 环境变量可改 |

---

## 📈 路线图

- [x] 三市场并行交易（US + CN + HK 独立 MCP 组）
- [x] Docker 化部署 + 宿主 cron 探活自愈
- [x] 本地 quantmind 数据仓库（不再依赖免费行情 API）
- [x] 交易记忆系统（每市场独立，自动归档）
- [x] 风控网关（单笔/持仓/熔断/黑名单）
- [x] Broker + 数据源抽象层
- [x] 双模型对决（DeepSeek V4 Flash / V4 Pro 零样本竞技）
- [x] 双前端（AI-agent 看板 8080 + Arena 竞技场 8092）
- [x] summary 扩展指标（夏普/胜率/盈亏比/费用/平均持仓）
- [ ] TDX 实盘下单移植（风控已就位）
- [ ] 富途 OpenD / 老虎 / IBKR adapter
- [ ] dsh 定时收盘复盘 + 多渠道推送
- [ ] 每日自动调度（daily_trade.sh + cron）

---

## 📄 License

MIT License（继承自 BayMax-Trader / AI-Trader）

> ⚠️ 免责声明：本项目仅供研究学习，所有交易均为模拟盘，不构成任何投资建议。接入实盘前请充分了解风险，并确保风控配置到位。
