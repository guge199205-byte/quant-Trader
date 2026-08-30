# BayMax-Trader 架构升级文档（2026Q3 实施）

> 配套路线图见 [PLAN_2026Q3.md](PLAN_2026Q3.md)。本文记录已实施/设计中的架构改动。

## 1. 后端配置系统（已实施）

**位置**：`config/backend.yaml`（加载器 `backend/config.py`）

统一管理：服务端口、市场定义、broker 注册表、API 行为、日志。密钥一律引用 `.env`（`_apply_env_overrides` 自动合并 `TDX_BRIDGE_URL`/`FUTU_OPEND_HOST`/`TIGER_API_KEY`/`IBKR_HOST` 等）。

```
config/
└── backend.yaml     # 后端总配置（server/markets/broker/api/logging）
```

## 2. API 层（已实施，端口 8091）

**位置**：`backend/api_server.py` + `backend/services/agent_data.py`

| 端点 | 说明 |
|------|------|
| `GET /api/config` | 后端配置（脱敏） |
| `GET /api/status` | 服务健康 + 当前运行状态（runtime_env） |
| `GET /api/markets` | 市场列表 |
| `GET /api/data/{path}` | **实时代理**：优先根目录 `data/`，回退 `nof0/data/` 快照 |
| `GET /api/agents?market=` | agent 列表（自动扫描） |
| `GET /api/agents/{a}/positions` | 持仓记录序列 |
| `GET /api/agents/{a}/trades` | 交易流水 |
| `GET /api/agents/{a}/performance` | 净值序列 + 收益/最大回撤摘要 |
| `GET /api/agents/{a}/logs?date=` | 决策日志 |

**前端实时化**：`nof0/config.yaml` 加 `api_base: "http://192.168.31.68:8091"`，
`config-loader.js` 的 `getDataPath()` 检测到 `api_base` 即返回 `{api_base}/api/data`——
所有 fetch 自动走实时数据，交易结果即时可见，无需手动复制快照。

> 注：8090 被 Huntly 容器占用，故用 8091。

## 3. Agent 引擎切换：DeepSeek Harness（已验证）

**目标**：将手写 langchain agent 循环替换为 DeepSeek 官方开源 harness
（`@deepseek-ai/dsh` v0.1.1-rc.2），MCP 工具链复用不变。

**验证结果**（2026-08-30）：`dsh --profile headless --patch dsh/baymax.cordis.yml`
成功经 `baymax_math` MCP（8100）调用工具完成计算。4 个 MCP 服务全部可挂载。

**结构**：
```
dsh/
└── baymax.cordis.yml      # MCP 挂载 patch（baymax_math/search/trade/price → 8100-8103）
scripts/start_dsh.sh       # 一键启动（含 .env 加载、端口检查）
```

**启动**：`bash scripts/start_dsh.sh` → http://localhost:3081（Web UI 自带会话/工具/日志可视）
**模型**：默认 `deepseek-official / deepseek-v4-flash`，key 走 `DEEPSEEK_API_KEY` 环境变量。
**提示词**：dsh 的 agent/system prompt 可在 Web UI（Settings）配置；现有中文交易提示词
（`prompts/agent_prompt.py`）可整体迁入。

### 与手写循环的对比

| 维度 | 旧（langchain 手写） | 新（dsh） |
|------|---------------------|-----------|
| 稳定性 | 依赖第三方适配层（已踩 mcp 版本坑） | DeepSeek 官方维护 |
| 会话/日志 | 自写 JSONL | 内置 session log + 事件流 |
| UI | 无 | 自带 Web UI（工具调用可视化、会话管理） |
| 插件生态 | 无 | dsh-market 插件市场 |

**推荐插件**（来自 [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)）：

| 插件 | 用途 |
|------|------|
| `863683348/dsh-plugin-finance-data` | 金融计算（万/亿换算、CAGR、风险指标）——交易分析直接受益 |
| `534119219/chicheng-cron` | 定时调度（每天收盘后跑回测/复盘） |
| `534119219/chicheng-push` | 多渠道推送（钉钉/飞书/微信/Telegram）——交易信号提醒 |
| `dsh-market` | 插件市场（Web UI 一键装） |
| `00080000/dsh-project-memory` | 项目记忆（跨会话策略知识） |

安装：`dsh plugin --profile web add <name>`（需先 `dsh plugin --profile web add dshmarket`）

## 4. Broker 抽象层（已实施：sandbox + tdx 骨架）

```
agent_tools/brokers/
├── __init__.py       # 注册表工厂: get_broker()/get_default_broker()
├── base.py           # Broker ABC: get_positions/get_cash/buy/sell/get_quote/get_klines
├── sandbox.py        # ✅ 模拟盘（复用 tool_trade.py，含初始持仓自举）
├── tdx_bridge.py     # ✅ 通达信桥：行情(8550) + 下单(quantmind 协议移植，三重防护)
├── futu_bridge.py    # ✅ 富途 OpenD（11111）：行情/下单/持仓/资金，默认模拟环境
├── tiger_bridge.py   # ✅ 老虎 SDK：行情/下单（账户未配拒绝）
└── ibkr_bridge.py    # ✅ 盈透 ib_insync：行情/下单/持仓/资金（默认纸面 7497）
```

- 注册表：`available_brokers()` → `['sandbox', 'tdx', 'futu', 'tiger', 'ibkr']`
- **实盘券商三重防护**（全实测）：未配置桥/账户 → 拒绝；审批门
  （`risk.approval_required=true`）→ 拒绝；富途默认 SIMULATE 模拟环境
- 环境变量：`TDX_BRIDGE_URL/TOKEN`、`FUTU_OPEND_HOST/PORT/TRD_PWD`、
  `TIGEROPEN_TIGER_ID/PRIVATE_KEY/ACCOUNT`、`IBKR_HOST/PORT/CLIENT_ID`

## 4.1 dsh 插件（已装）

```
dshmarket           插件市场（Web UI 一键装/升级）
chicheng-cron       定时调度（每日收盘复盘、定时交易任务）
chicheng-push       多渠道推送（钉钉/飞书/微信/Telegram 交易提醒）
dsh-plugin-finance-data  金融计算（万/亿换算、CAGR、风险指标）
```

安装方式：`cd ~/.dsh/profiles/web && pnpm add <pkg>`（npm registry 或 git+https）

## 4.2 风控模块（已实施，broker 前置网关）

**位置**：`agent_tools/risk.py`；配置 `config/backend.yaml` → `risk` 段。

规则（全部单点校验，三条交易路径——main.py agent / dsh agent / broker API——都经
`tool_trade.buy/sell` 落盘，网关在此拦截）：

| 规则 | 默认 | 说明 |
|------|------|------|
| 单笔限额 | ≤ 权益 20% | 金额 = 价格 × 数量 |
| 持仓限额 | 单标 ≤ 权益 20% | 买入后该标市值占比 |
| 日亏熔断 | 5% | **权益口径**（CASH + 持仓市值估值），买入支出不误判为亏损；卖出永远放行（可止损） |
| 现金保留 | $100 | 交易后最低现金 |
| 黑名单 | 空 | 禁用标的 |
| 审批位 | false | 实盘 broker 上线时置 true |

`RiskGateway` 包装类可包装任意 broker；`pre_trade_check()` 供 tool_trade 直接调用。
初版用 CASH 变化算日亏会误判买入支出为亏损，已改为权益口径。

## 4.3 数据源抽象层（已实施）

**位置**：`agent_tools/datasources/`（与 broker 对称可插拔）；配置 `backend.yaml` → `datasource` 段。

```
datasources/
├── base.py    # DataSource ABC: get_quote/get_klines/is_trading_day/get_trading_days
├── local.py   # 本地 merged.jsonl（包装 price_tools，日级+小时级兼容）
├── tdx.py     # 通达信 8550 桥（行情只读，复用 TdxBridgeBroker 逻辑）
└── __init__.py# 注册表: get_datasource()/get_default_datasource()
```

现状：**下单源（broker）与行情源（datasource）完全解耦可插拔**。

## 4.4 前端风格统一（排行榜/模型页）

`nof0/assets/css/theme-unify.css`（最后加载覆盖）：
- 统计卡（best/worst/total-equity）去掉彩色渐变/发光/上浮，统一玻璃拟态
- 表格表头统一玻璃背景
- models 页覆盖浅色硬编码（#f8f9fa/#fff）为深色主题变量

## 4.5 多市场并行交易（已实施）

**脚本**：`scripts/run_multi_market.sh`（US + CN 并行）

隔离方案（每市场独立全套）：
```
US: MCP 8100-8104 + runtime_env.json          + data/agent_data/
CN: MCP 8200-8204 + runtime_env_cn.json       + data/agent_data_astock/
HK: MCP 8300-8304 + runtime_env_hk.json       + data/agent_data_hk/
```

**港股数据源**（腾讯行情接口，免费后复权日K）：`data/HK_stock/get_daily_price_hk.py`
→ 30 只恒指权重股，生成 `data/HK_stock/merged.jsonl`（与美股/A股同格式）。
港股 agent 为日级模式（BaseAgent + market: hk），股票池 `all_hk_symbols`（prompts）。

- 端口隔离：CN 组以 `MATH_HTTP_PORT=8200 ...` 环境变量启动（load_dotenv 不覆盖已设环境变量）
- 状态隔离：RUNTIME_ENV_PATH 指向独立文件（MCP 子进程继承环境变量）
- 实测：两套服务并行响应，CN agent 用 deepseek-v4-flash 跑 A 股（SSE 50）推进正常

用法：
```
bash scripts/run_multi_market.sh                 # 全量：服务 + 双市场 agent
bash scripts/run_multi_market.sh --services-only # 只起服务
bash scripts/run_multi_market.sh --agents-only   # 只起 agent
```

## 4.6 交易记忆系统（已实施）

**位置**：`agent_tools/tool_memory.py`（MCP memory 服务，US 8104 / CN 8204）

**每市场独立记忆文件**（agent 越用越好用的核心）：
- US: `data/agent_data/market_memory.md`
- CN: `data/agent_data_astock/market_memory.md`

**工具**（baymax_memory）：
- `read_memory()` — 决策前回顾历史（persona 指示开盘前调用）
- `append_memory(section, content)` — 收盘后沉淀经验（分区白名单：策略心得/成功案例/失败教训/市场观察/待改进）
- `list_memory_sections()` — 查看记忆规模

**机制**：
- 市场隔离：工具按 runtime_env 的 MARKET 定位记忆文件（dsh 读的即当前市场）
- 防膨胀：超过 200 行自动归档到 `market_memory.archive.md` 并重建骨架
- 注入：dsh persona + main.py 提示词均已加入"开盘读记忆、收盘写记忆"指令

**验证**：MCP 层读写/隔离/分区校验全通过；dsh headless 实测经 baymax_memory 读取 CN 市场记忆成功。

## 4.7 dsh 中文交易 persona（已配置）

`dsh/baymax.cordis.yml` 中 `system-prompt` 行 `config.persona`：中文交易助手身份 +
工具使用规则（baymax_trade/price/math/search）+ 交易纪律（单标的 ≤20%、止损 -8%/
止盈 +15%、每日总结）。patch 行覆盖语法：`- id: <row-id>` + 覆盖字段（Cordis
applyEntryPatches 直接按 id 覆盖，无 set 包装）。

## 5. 已修复的 Bug

| # | 问题 | 修复 |
|---|------|------|
| 1 | 日级/小时级数据不匹配（is_trading_day 只认 Daily） | `tools/price_tools.py`：新增 `_iter_time_series_keys`/`_ts_key_matches_date`，兼容任意 Time Series 前缀 + 小时级 key |
| 2 | mcp>=1.0 API 改名导致 adapters 崩溃 | `scripts/patch_mcp_adapters.py` 幂等固化（import 兼容 + 新函数签名） |
| 3 | 前端 CDN 白屏（jsdelivr/Google Fonts 不可达） | 3 个 JS 自托管 `nof0/assets/js/vendor/`，字体链接移除 |
| 4 | 端口 8000-8003 / 8090 冲突 | MCP → 8100-8103；API → 8091 |
| 5 | 前端静态快照不实时 | API 层 /api/data 代理（见 §2） |
| 6 | 页面英文 + 无手机自适应 | 前端中文化 + 响应式补强（768/480 断点、字号基准） |
| 7 | `tool_trade.buy` 价格是字符串时 `str*int` 抛 TypeError 被吞 → `cash_left` 未定义 UnboundLocalError（A股路径必现） | 价格/现金显式 `float()`，异常改为返回错误 dict |
| 8 | `get_open_prices` 只精确匹配 key，纯日期查小时级数据返回空 | 日期前缀匹配（同 bug 1 家族） |
| 9 | 前端实时化失效链（config-loader.js 全面修复）：① `split(':')` 截断 URL 值 ② value 未剥引号 ③ `markets:` 分组键覆盖 `config.markets` 对象 ④ 缩进在 trim 之后计算（永远为 0，整棵配置树分层失效）⑤ 缩进回退时 `currentMarket` 未重置（api_base 落进 cn 市场）⑥ `agents:` 行覆盖已初始化的数组 ⑦ `parseAgentEntry` 无停止条件吞掉整个列表块 ⑧ agent 缺失 `folder` 标识 ⑨ `getEnabledAgents` 过滤掉无 `enabled` 字段的 agent（配置里根本没有该字段 → 永远空） | 重写 parseYAML 缩进/分组/列表解析；`getEnabledAgents` 默认启用 |
| 10 | date-fns@2.29.3 的 jsDelivr 产物是 CJS，浏览器 `exports is not defined` 崩溃；且 `chartjs-adapter-date-fns.bundle.min.js`（50KB）是异常文件，真 bundle 应为 220KB | 换用 220KB `chartjs-adapter-date-fns.bundle.js`（内嵌 date-fns），删除独立 date-fns 引用 |
| 11 | 页面跳转主题黑闪 | 4 页面 head 加防闪内联脚本（CSS 加载前同步 `data-theme` + 背景色） |
| 12 | 右侧面板（持仓/成交/分析/README）超高不对齐 | `.side-panel` 限高 + 内部滚动，与图表区等高 |

## 6. 运行拓扑（当前）

```
nof0 前端 (8080)  ──api_base──▶  FastAPI (8091)  ──/api/data──▶  data/ (实时)
     │                                    ▲
     └── 静态资源 ────────────────────────┘
dsh Web (3081)  ──MCP streamable-http──▶  math(8100)/search(8101)/trade(8102)/price(8103)
交易主程序 (main.py)  ──MCP──▶  同上 8100-8103
```
