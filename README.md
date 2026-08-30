# 🤖 Trade Agent — AI 自主交易竞技场

> **多市场并行、每市场独立记忆、风控护航、券商/数据源可插拔**。

多个 AI 模型以独立资金池在**纳斯达克 100（美股）** 与 **上证 50（A股）** 市场上自主分析、决策、买卖，无人工干预。AI 的交易记忆随运行沉淀，**越用越好用**。

---

## ✨ 核心特性

| 能力 | 说明 |
|------|------|
| 🇺🇸🇨🇳 **多市场并行** | US + CN 同时交易，各自独立 MCP 服务组、独立状态、独立数据目录 |
| 🧠 **DeepSeek Harness 引擎** | 官方开源 agent harness（dsh）驱动，会话持久化、工具调用可视化、插件生态 |
| 📝 **交易记忆系统** | 每市场独立记忆文件，agent 开盘读心得、收盘写经验，防膨胀自动归档 |
| 🛡️ **风控网关** | 单笔/持仓限额、日亏熔断（权益口径）、现金保留、黑名单，三条交易路径单点拦截 |
| 🔌 **Broker 可插拔** | sandbox（模拟盘）/ tdx（通达信桥，下单待移植）/ futu / tiger / ibkr（规划中） |
| 📊 **数据源可插拔** | local（本地文件）/ tdx（8550 桥行情），与 broker 对称设计 |
| ⚡ **前端实时化** | FastAPI 代理层，交易结果即时可见，无需手动同步快照 |
| 🌙 **统一 UI** | 全中文、深/浅主题、手机自适应、排行榜/模型页与实盘页风格统一 |

---

## 🏗️ 架构总览

```
┌─────────────┐   api_base    ┌──────────────┐   /api/data   ┌──────────┐
│  Trade Agent │ ────────────▶ │  FastAPI     │ ────────────▶ │  data/   │
│  前端 (8080) │               │  API (8091)  │               │ 实时数据  │
└─────────────┘               └──────────────┘               └──────────┘
                                                                    ▲
┌──────────────┐   MCP streamable-http   ┌──────────────────────┐  │
│  dsh (3081)  │ ──────────────────────▶ │ US 组 8100-8104      │──┘
│ agent 引擎    │                         │  math/search/trade/  │
│ (DeepSeek)   │                         │  price/memory        │
└──────────────┘                         ├──────────────────────┤
                                         │ CN 组 8200-8204      │
                                         │  math/search/trade/  │
                                         │  price/memory        │
                                         └──────────────────────┘
```

**交易路径**（main.py agent / dsh agent / broker API）→ 全部经 `tool_trade.buy/sell` 落盘
→ **风控网关单点拦截** → position.jsonl 写入 → 前端实时读取。

---

## 🚀 快速开始

### 环境准备

```bash
# 1. Python 3.10+ 依赖（uv 快，可用 pip）
uv pip install --python .venv/bin/python -r requirements.txt

# 2. 环境变量（.env）
OPENAI_API_BASE="https://api.deepseek.com/v1"
OPENAI_API_KEY="你的DeepSeekKey"
DEEPSEEK_API_KEY="你的DeepSeekKey"      # dsh 引擎使用
MATH_HTTP_PORT=8100  SEARCH_HTTP_PORT=8101  TRADE_HTTP_PORT=8102
GETPRICE_HTTP_PORT=8103  MEMORY_HTTP_PORT=8104
RUNTIME_ENV_PATH="/绝对路径/BayMax-Trader/runtime_env.json"
```

### 一键启动

```bash
# 多市场并行（US + CN 全套：MCP 服务 + 交易 agent）
bash scripts/run_multi_market.sh

# 或分步
bash scripts/start_dsh.sh                    # dsh 引擎（3081）
.venv/bin/python agent_tools/start_mcp_services.py  # US MCP 组
.venv/bin/python main.py configs/deepseek_hour_test.json  # US agent
```

### 访问

| 服务 | 地址 | 说明 |
|------|------|------|
| Trade Agent 前端 | http://192.168.31.68:8080 | 实盘/排行榜/模型，实时数据 |
| API | http://192.168.31.68:8091 | `/api/status` `/api/agents` `/api/data/*` |
| dsh Web | http://localhost:3081 | agent 会话/工具调用/日志可视化 |

---

## 🧠 交易记忆（越用越好用）

每市场独立记忆文件（`market_memory.md`），agent 通过 MCP 工具读写：

| 工具 | 时机 | 内容 |
|------|------|------|
| `read_memory()` | 开盘决策前 | 回顾策略心得/成功案例/失败教训/市场观察 |
| `append_memory(section, content)` | 收盘后 | 沉淀今日经验（分区白名单：策略心得/成功案例/失败教训/市场观察/待改进） |

- 记忆文件：US `data/agent_data/market_memory.md` / CN `data/agent_data_astock/market_memory.md`
- 超 200 行自动归档 `market_memory.archive.md`，重建骨架防膨胀
- dsh persona 与 main.py 提示词均已内置"开盘必读、收盘必写"指令

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
| `GET /api/status` | 服务健康 + 当前运行状态 |
| `GET /api/config` | 后端配置（脱敏） |
| `GET /api/agents?market=` | agent 列表 |
| `GET /api/agents/{a}/positions` | 持仓序列 |
| `GET /api/agents/{a}/trades` | 交易流水 |
| `GET /api/agents/{a}/performance` | 净值/收益/回撤 |
| `GET /api/data/{path}` | 实时代理（根目录 data/ 优先） |

---

## 📁 目录结构

```
agent_tools/
├── brokers/          # Broker 抽象层（sandbox/tdx 注册表）
├── datasources/      # 数据源抽象层（local/tdx 注册表）
├── risk.py           # 风控网关（单点校验 + RiskGateway 包装）
├── tool_trade.py     # 交易执行（风控接入）
├── tool_memory.py    # 交易记忆（每市场独立）
└── start_mcp_services.py  # MCP 服务编排（US 8100-8104 / CN 8200-8204）
backend/              # FastAPI 层（api_server.py + services/agent_data.py）
config/backend.yaml   # 后端总配置（server/markets/broker/datasource/risk）
dsh/baymax.cordis.yml # dsh 集成（persona + MCP 挂载）
scripts/              # run_multi_market.sh / start_dsh.sh / patch_mcp_adapters.py
docs/                 # PLAN_2026Q3.md / ARCHITECTURE_UPGRADE.md
nof0/                 # 前端（实时看板，中文化，响应式）
data/                 # 交易数据（agent_data/ US、agent_data_astock/ CN、market_memory.md）
```

---

## ⚠️ 常见问题

| 问题 | 解决 |
|------|------|
| 端口冲突（8000-8003 被其他服务占用） | MCP 端口通过 .env 环境变量改（本项目用 8100-8104 / 8200-8204） |
| 重建环境后 MCP client 报错 | 运行 `python scripts/patch_mcp_adapters.py`（mcp>=1.0 API 兼容补丁） |
| 前端不实时 | 确认 `nof0/config.yaml` 的 `api_base` 指向 API 地址；浏览器强刷（Ctrl+Shift+R） |
| A股 agent 不交易 | 检查 `data/A_stock/merged.jsonl` 数据覆盖日期范围 |
| 记忆不生效 | 确认 memory MCP 服务在线（8104/8204），persona 已含记忆指令 |

---

## 📈 路线图

- [x] 多市场并行交易（US + CN 独立 MCP 组）
- [x] DeepSeek Harness 引擎接入 + 插件（dshmarket/cron/push/finance-data）
- [x] 交易记忆系统（每市场独立，自动归档）
- [x] 风控网关（单笔/持仓/熔断/黑名单）
- [x] Broker + 数据源抽象层
- [ ] TDX 实盘下单移植（风控已就位）
- [ ] 富途 OpenD / 老虎 / IBKR adapter
- [ ] dsh 定时收盘复盘 + 多渠道推送
- [ ] SQLite 元数据 + 事件流

---

## 📄 License

MIT License（继承自 BayMax-Trader / AI-Trader）

> ⚠️ 免责声明：本项目仅供研究学习，所有交易均为模拟盘。接入实盘前请充分了解风险，并确保风控配置到位。
