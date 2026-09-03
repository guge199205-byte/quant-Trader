# 系统技能包总览（dsh / Skills）

把整个系统的分析能力组织成一套 **dsh 技能包**：AI 会话（8093 交易智能体 / 宿主 dsh / 复盘任务）通过**触发词**自动命中技能，技能内部用契约脚本取数、算因子、落报告。

## 技能包在哪

| 位置 | 内容 | 生效方式 |
|---|---|---|
| `dsh/skills/<name>/` | 本仓库自带技能（下表） | bind mount → dsh 容器 `/root/.dsh/skills/`，改完 `docker compose up -d dsh` 生效 |
| `/quantmind/skills/`（只读挂载） | 外部 quantmind 仓库技能（可选增强） | dsh 容器内 `/quantmind/skills` 直读，技能契约脚本可用 `docker exec quantmind` 跑重依赖 |
| `dsh/baymax.*.cordis.yml` | 交易 agent persona/端点补丁（非技能） | dsh `--patch` 参数，盘中分析用 MCP 工具链 |

## 技能清单（12 个，A股/美股/港股）

| 技能 | 干什么 |
|---|---|
| `market-analysis` | 大盘快照研报：指数/广度/情绪/行业热力/资金流 → MD+PDF 报告 |
| `stock-research` | 个股深度研究（多 Agent 框架，借鉴 TradingAgents-CN） |
| `stock-picks` | 每日复盘后的股票推荐（多维度选股） |
| `stock-market-analysis` | 全市场信号扫描/行业强度/数据导出 |
| `daily-review` | A股每日复盘（专业版，QuantDB 本地数据） |
| `news-sentiment-research` | 新闻情绪方法论：RSS 历史库 → FinBERT 情绪因子 |
| `quantdb-sdk` | QuantDB 数据 SDK：Key 配置/数据集目录/查询 |
| `quantdb-fields` | QuantDB 字段单位速查手册（实测验证） |
| `futuapi` | 富途 OpenAPI 交易/行情助手（HK） |
| `install-futu-opend` | Futu OpenD 一键安装升级（HK 前置） |
| `ibkr-cli` | IB Gateway/TWS 安装配置与 CLI 操作（US） |
| `tigeropen` | 老虎 OpenAPI 交易与行情（US/HK 备选通道） |

新增技能规范（目录结构、SKILL.md frontmatter、环境契约写法）：见 [AGENT_GUIDE.md](AGENT_GUIDE.md) §③。

## 技能 ↔ 交易 agent 的分工（重要）

- **技能包** = 会话/复盘 AI 的能力：人类问「今天市场怎么样」「深度研究 XXX」时命中触发词执行。
- **交易 agent** = 盘中自主执行：`dsh/baymax.trading.cordis.yml` 定义 persona，调 **MCP 工具**
  （`baymax_price/search/trade/math/memory/quantdb`，由 mcp-us 容器 8100-8105 提供），输出统一 JSON 决策块，风控/闸门在系统侧。
- 两边共用同一数据底座（quantdb / 桥行情 / 记忆），技能给"人问"服务，cordis 给"机器自跑"服务。

## 部署后自检

```bash
# 技能在 dsh 会话里可见可触发
docker compose up -d dsh && docker logs baymax-dsh | tail
# 或直接看容器内技能目录
docker exec baymax-dsh ls /root/.dsh/skills | head
# 交易 agent 工具链（MCP）自检
python scripts/dsh_agent.py --status
```
