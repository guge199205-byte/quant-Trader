# 新增智能体指南（Agent / 技能包）

本仓库有**三种「智能体」**，各自扩展方式不同。动手前先分清你要加哪一种：

| 类型 | 用途 | 入口 |
|---|---|---|
| ① 竞技/回放 agent | 历史回放竞技场（模拟盘）参赛模型 | `configs/*_config.json` + `agent/` 策略类 |
| ② 实盘分账 agent | A股实盘分析执行（分账 ¥10 万/agent） | `configs/astock_config.json` + `prompts/analysis_modes.py` + dsh 端点 |
| ③ dsh 技能包技能 | 给 AI 会话/复盘用的可复用能力包 | `dsh/skills/<name>/SKILL.md` |

---

## ① 加一个「竞技/回放」agent（模拟盘参赛）

沿用 CLAUDE.md 的开发指南，3 步：

1. `configs/default_config.json`（A股用 `astock_config.json` 同构）的 `models` 数组加一项：
   ```json
   { "name": "my-agent", "basemodel": "your/model-name", "signature": "my-agent", "enabled": true }
   ```
2. `.env` 配好该模型的 API Key / Base URL（OpenAI 兼容格式即可）。
3. 用默认策略类即可参赛；自定义策略继承 `BaseAgent`（美股）或 `BaseAgentAStock`（A股）覆写 `run()`，并在 `main.py` 的 `AGENT_REGISTRY` 注册，再把配置里 agent_type 指向它。

> 竞技场的排行榜/模型卡/对话全部自动出现该模型，无需改前端。

---

## ② 加一个「实盘分账」agent（A股生产链路）

实盘链路 = **分账名单**（谁参赛）+ **执行通道**（dsh agent 模型端点）+ **分析模式**（每轮怎么想）+ **记忆/账本**（自动）。共 4 步：

### 2.1 分账名单（核心开关）

编辑 `configs/astock_config.json` → `models` 数组：

```json
{ "name": "my-agent", "basemodel": "my/model", "signature": "my-agent", "enabled": true }
```

- 名单来源 = `scripts/live_trade_picks.py::enabled_agents()`（读 `models[].enabled`）。
- 生效时机：下一轮 09:35 调仓 / 整点分析自动带上；系统自动给它开 ¥10 万虚拟子账户（首次买入记入 `logs/live_ledger.json`），不抢别人的额度。

### 2.2 模型执行端点（dsh 怎么调你的模型）

实盘分析默认走 `scripts/dsh_agent.py::run_agent(model)`：

- 内置两种端点：`deepseek`（默认，读 `DEEPSEEK_API_KEY`）与 `glm`（读 `GLM_API_KEY/GLM_API_BASE`，额外挂 `dsh/baymax.glm.cordis.yml` 补丁）。
- 新模型两步：
  1. `scripts/dsh_agent.py` 顶部 `MODEL_ENV_KEYS` 加入你的 Key 名（如 `MY_API_KEY`），`build_env()` 照现有模式透传。
  2. 新建 `dsh/baymax.<mymodel>.cordis.yml`（参考 glm 版：只覆写 provider/base_url/model，其余不动的补丁在 dsh 里是增量合并的），并把该模型名 → patch 的映射加进 `run_agent()` 的 `patches` 分支。
- 兜底：主模型配额耗尽自动降级备用端点（`FALLBACK_LLM_MODEL`），无需干预。

### 2.3 分析模式（每轮分析怎么想）

`prompts/analysis_modes.py` 的 `MODES` 数组定义模式（基线/苦行/情境感知/极限杠杆…），前端「比赛配置」页可对每个 agent 多选，选择存 `configs/comp-config.json`。

新增模式 = 在 `MODES` 加一项（`id` 唯一 + 提示词）。要注入额外上下文（如排行榜）参考 `awareness` 在 `live_hourly_analysis.py` 里的特判注入。

### 2.4 纪律与风控（新 agent 自动继承）

闸门全部在系统侧，与模型无关：T+1 可卖量复核、杠杆 >1.5× 独立守护强平、跌停不接、额度不透支、单笔 ≤20% 权益、拒单登记延期单自动重放、行情停更禁价。新 agent 不需要自己实现风控。

---

## ③ 加一个 dsh「技能包」技能

技能包位置：`dsh/skills/<技能名>/`（运行容器里挂载到 `/root/.dsh/skills/`）。

### 3.1 目录结构

```
dsh/skills/my-skill/
├── SKILL.md        # 技能说明（必填，frontmatter 带 name + description）
├── scripts/        # 取数/执行脚本（bash/python，契约见下）
└── references/     # 可选：参考文档
```

### 3.2 SKILL.md 规范（复制现有技能最稳）

frontmatter 两字段决定触发：

```yaml
---
name: my-skill
description: "一句话说明做什么 + 触发词（用户说「XX」「YY」时使用）。含重依赖脚本说明放正文"
---
```

正文首行放 **运行环境契约**（参考 `dsh/skills/market-analysis/SKILL.md` 的写法）——技能可能在容器或宿主机跑，先探测环境再执行：重依赖脚本 `docker exec` 进 quantmind 容器跑、报告落盘目录、PDF 降级链。技能内容 = 给 AI 的指令，写清楚「什么时候用、跑什么、输出什么、禁止编造」。

### 3.3 生效与验证

```bash
# 改完技能重新起 dsh 容器即生效（技能目录是 bind mount）
docker compose up -d dsh
# 在 8093 会话里试触发词确认命中
```

> ⚠️ 技能与「交易 agent persona」是两个层面：交易 agent 每轮分析调的是 MCP 工具
> （`baymax_price/quantdb/math/...`，见 `dsh/baymax.trading.cordis.yml` persona），
> 技能包是给会话型 AI（dsh 交互/复盘）用的能力。给交易 agent 加工具走 cordis
> persona / MCP 白名单，给会话 AI 加能力走技能包。

---

## 最小验证路径（加完跑一圈）

```bash
# ① 竞技 agent：回放 1 天
docker compose --profile agents run --rm -e INIT_DATE=2026-08-28 -e END_DATE=2026-08-28 agent-cn
# ② 实盘分账 agent：等整点分析日志出现该 agent 名（logs/live_hourly_analysis.log），
#    或手动跑一轮分析（不真下单）
python scripts/live_hourly_analysis.py --dry-run
# ③ 技能包：8093 会话里触发词试聊
```

系统级技能包总览（全部技能、与外部技能目录关系、开发规范索引）：见 [SKILLPACK.md](SKILLPACK.md)。
智能体能力演进路线（自我进化/假设库/辩论/风险预算/多市场复刻）：见 [AGENT_ROADMAP.md](AGENT_ROADMAP.md)。
