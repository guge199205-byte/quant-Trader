# docs 索引

> 本目录的全部设计与规划文档。**新增内容优先看下面第 0 节**；
> 其余按主题归类，便于按需查阅。

---

## 0 · 2026-09-18 机构级补齐批（带状态）

| 文档 | 内容 | 状态 |
|---|---|---|
| [INSTITUTIONAL_GRADE_PLAN.md](INSTITUTIONAL_GRADE_PLAN.md) | 机构级五层差距清单 + 本批落地（新闻新鲜度重构/风险预算 fail-safe/单票集中度闸/熔断告警）+ 后续规划 P1-P10 | ✅ 本批落地四项，余为规划 |

## 0b · 2026-09-13 本轮新增（带状态）

| 文档 | 内容 | 状态 |
|---|---|---|
| [AGENT_WORK_OBSERVABILITY.md](AGENT_WORK_OBSERVABILITY.md) | dsh 升级全记录 + agent 干活监控四阶段方案 | §4 已完成；四阶段待做 |
| [STOCK_TERMINAL_EMBED_PLAN.md](STOCK_TERMINAL_EMBED_PLAN.md) | 个股终端内嵌进 Arena | ✅ **已完成**（8094 + 导航项） |
| [ROUNDTRIP_FEATURES_PLAN.md](ROUNDTRIP_FEATURES_PLAN.md) | 回合台账价格类特征附件（离线可重跑） | ✅ **已实现**（待回合积累） |
| [ACCOUNT_PROTOCOL_PLAN.md](ACCOUNT_PROTOCOL_PLAN.md) | QIFI 式账户协议抽取（三套台账去重） | ✅ **已完成**（三市场全切） |
| [LEDGER_FOLLOWUP_PLAN.md](LEDGER_FOLLOWUP_PLAN.md) | 台账收尾三件事 | 事项一 ✅；二/三 待做 |

### 本轮代码产物

| 文件 | 作用 |
|---|---|
| `scripts/account_protocol.py` | **三市场账户协议层**（唯一算法出处；QIFI 式解耦） |
| `scripts/roundtrip_features.py` | 回合价格特征离线附件（点位时间纪律 + 幂等） |
| `scripts/live_performance.py` | **实盘绩效指标**（净值曲线 + 回合台账 → 最大回撤/胜率/盈亏比） |
| `scripts/dsh_supervised.sh` | dsh 守护启动器（借鉴 dsh_desktop 的"守护瀑布"） |
| `scripts/dsh_auth_cookie.py` | dsh web 鉴权 cookie 预签（代理层自动注入） |
| `scripts/dsh_web_url.sh` | 打印当前可用的带 token 入口 |
| `tests/test_account_protocol.py` | **golden 基线 + 三市场一致性**（防漂移） |
| `tests/test_roundtrip_features.py` | 点位时间纪律回归测试 |
| `tests/test_live_performance.py` | 绩效指标口径（含"记账跳变"回归） |
| `tests/test_live_roundtrips.py` | 回合台账 |

### 实盘绩效补上后的第一个发现（2026-09-13）

`live_performance.py` 一跑就暴露了日报看不见的东西：

```
deepseek-v4-flash: 10 天  收益 -14.95%  最大回撤 -17.56%
deepseek-v4-pro:   10 天  收益  +1.27%  最大回撤  -0.83%
glm-5.3-flash:     10 天  收益  -5.11%  最大回撤  -5.11%
⚠️ flash 2026-09-01 净值 107,979 → 91,325（-15.4%）—— 疑似记账口径变更（非交易损益）
```

**那个 -15.4% 是分账口径调整**（`live_ledger`："2026-08-31 已买的 5 只不入分账"），
不是交易亏损。若不标注就按 -14.95% 决策，会严重误判 flash 的真实表现——
所以脚本内置了跳变检测（`DISCONTINUITY_PCT`）。

> 其余两个 agent 的曲线连续、数值可信：**pro +1.27%（回撤 0.83%）明显优于另两个**。

### 本轮的三条方法论教训（都在对应文档里有现场记录）

1. **先验证前提，再下结论** —— 本轮三次误判同源：拿一张表的新旧推断整条链路。
   `stock_daily_latest` 停在 09-08 并非故障（它不在 A 股主路径上）；
   dshmarket 1.35.0 坏而 1.45.1 好（我一开始按版本号笼统告警）。
2. **"该在什么时点记录"先验证可否重算** —— 我初判价格特征"事后再也补不了"，
   实际可回溯；按错判断做会往实盘路径加无谓的数据依赖。
   见 [ROUNDTRIP_FEATURES_PLAN.md](ROUNDTRIP_FEATURES_PLAN.md) §0。
3. **同一规则复制多份必然漂移** —— 本轮在这套系统里抓到三处：
   限额口径公式（3 份）、补丁 config 覆盖（多处）、三套台账（HK/US 互为副本）。
   对策都是"收敛到唯一出处 + 加一致性测试"。

---

## 1 · 实盘与交易链

| 文档 | 内容 |
|---|---|
| [LIVE_TRADING.md](LIVE_TRADING.md) | 实盘下单流程（通达信桥） |
| [QMT_BRIDGE.md](QMT_BRIDGE.md) | QMT 桥（迅投大 QMT）接入手册 |
| [BROKER_ONBOARDING.md](BROKER_ONBOARDING.md) | 券商接入新手指南 |
| [us_live_execution_plan.md](us_live_execution_plan.md) | 美股实盘执行链规划（IBKR） |
| [PIPELINE_UPGRADE.md](PIPELINE_UPGRADE.md) | 全天候循环 · 全链路优化审计 |

## 2 · Agent 与智能体

| 文档 | 内容 |
|---|---|
| [AGENT_GUIDE.md](AGENT_GUIDE.md) | 新增智能体指南（Agent / 技能包） |
| [AGENT_ROADMAP.md](AGENT_ROADMAP.md) | 交易智能体演进路线图（A股 → 多市场） |
| [AGENT_PHASE34_DESIGN.md](AGENT_PHASE34_DESIGN.md) | 阶段 3/4 设计（多空辩论 · 动态风险预算） |
| [REVIEW_AGENT_DESIGN.md](REVIEW_AGENT_DESIGN.md) | 盘后复盘 Agent 设计 |
| [prompt_template.md](prompt_template.md) | 交易 Agent 提示词模板 |
| [SKILLPACK.md](SKILLPACK.md) | 系统技能包总览（dsh / Skills） |
| [agent_upgrade_plan.md](agent_upgrade_plan.md) | Agent 升级规划 |

## 3 · 数据与账户

| 文档 | 内容 |
|---|---|
| [ACCOUNT_PROTOCOL_PLAN.md](ACCOUNT_PROTOCOL_PLAN.md) | **账户协议抽取**（三套台账 → 一份协议） |
| [ROUNDTRIP_FEATURES_PLAN.md](ROUNDTRIP_FEATURES_PLAN.md) | 回合台账与价格特征 |
| [quantmind-trading-migration.md](quantmind-trading-migration.md) | QuantMind 交易框架移植清单 |

## 4 · 平台与部署

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE_UPGRADE.md](ARCHITECTURE_UPGRADE.md) | 架构升级（2026Q3 实施） |
| [DEPLOYMENT.md](DEPLOYMENT.md) | AI 可执行部署指南 |
| [COMPOSE_OPS.md](COMPOSE_OPS.md) | 容器编排运维手册（自愈与事实源） |
| [CONFIG_GUIDE.md](CONFIG_GUIDE.md) | 配置系统文档 |
| [MARKET_LAB_WORKBENCH_DESIGN.md](MARKET_LAB_WORKBENCH_DESIGN.md) | 行情回测工作台设计 |

## 5 · 规划与路线图

| 文档 | 内容 |
|---|---|
| [PLAN_2026Q3.md](PLAN_2026Q3.md) | Quant-Trader 升级规划（2026Q3） |
| [PLAN_PRODUCTION.md](PLAN_PRODUCTION.md) | 生产化改造 ✅ 已完成 |
| [MAINTENANCE_PLAN.md](MAINTENANCE_PLAN.md) | 系统维护与优化计划 |
| [upgrade_arena_plan.md](upgrade_arena_plan.md) | 竞技场升级总体规划 |

---

## 6 · 待办总览（跨文档汇总）

| 事项 | 出处 | 前置 |
|---|---|---|
| **把 `performance.json` 接进日报 / 面板** | 本节 | 无（数据已就绪，只差消费方） |
| **事项二 · 延期单重放多市场化** | LEDGER_FOLLOWUP | 无（HK/US 恢复交易**前**必须做） |
| **事项三 · 行为归因**（错过信号 / 过度交易） | LEDGER_FOLLOWUP | **必须等事项二**——否则会把"还没重放"误算成永久损失 |
| **A 股路径的限额口径抽取** | 本轮对话 | 三条路径同规则复制，建议抽共享函数 |
| **容器接守护启动器** | AGENT_WORK_OBSERVABILITY §4.9④ | 无（防坏插件致容器无限崩溃重启） |
| **agent 干活监控四阶段** | AGENT_WORK_OBSERVABILITY §2 | 阶段 1 需先做输出 A/B 回归 |
| **影子账户模式抽取** | ROUNDTRIP_FEATURES §5 | 等回合台账积累（当前 0 笔） |
| **容器 dsh 升级 + `Dockerfile.dsh` 钉版本** | AGENT_WORK_OBSERVABILITY §4 | 非交易日窗口 |

> **注意**：`stock-terminal/`（内嵌的个股终端）与 `/home/zbox/stock-terminal-local/`
> 的原件**已不一致**——所有版式改动都在仓库副本里，原件保持初始状态。
> 需要同步时再定（是否回写 / 是否提交进 git）。
