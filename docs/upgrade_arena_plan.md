# BayMax 竞技场升级总体规划（NOF1 思路 + coke-nof1 前端）

> 2026-08-30 编制。目标：把 BayMax-Trader 从"单模型三市场手动回放"升级为
> "多模型同池竞技 + 现代竞技场前端"，对标 NOF1.AI Alpha Arena。

---

## 一、现状事实（规划依据，已实测）

### 后端 / 模型
- `.env` 只有 **1 个 LLM key**：`OPENAI_API_KEY`（base = `https://api.deepseek.com/v1`）
- deepseek 平台实测可用模型 **3 个**：`deepseek-v4-flash`、`deepseek-v4-pro`、`deepseek-v4-flash-vision-exp`
- `configs/` 里已有 7 个模型定义（claude-3.7-sonnet / deepseek-chat-v3.1 / qwen3-max /
  gemini-2.5-flash / gpt-5 / deepseek-v4-flash / MiniMax-M2），但**除 deepseek-v4-flash 外全部
  disabled，且名字是 OpenRouter 风格**——deepseek 平台不可达，属于"配了跑不了"的占位
- 三市场 config：`deepseek_us_test.json`(US) / `astock_config.json`(CN) / `deepseek_hk_test.json`(HK)，
  当前都只启用 deepseek-v4-flash
- 手续费/滑点模型已有（万3 + 0.05%）；风控五重已有；交易记忆已有；本地 quantmind 数据仓库已有

### 前端
- 现状：`nof0/` 原生 JS + Chart.js 静态页（index 实盘 / portfolio 排行榜 / models 模型 / monitor 总控），
  serve_nof0.py 静态服务（8080），api 托管版 8091
- 参照物 coke-nof1（已 clone 到 /tmp/coke-nof1）：
  - React 18 + TypeScript + Vite + Redux Toolkit（4 slices）+ **@visx 图表** + **antd 5** + axios + dayjs
  - 页面：Home / Live / Leaderboard / ModelDetail / Blog / About
  - 组件：Navbar / PriceCard / ModelCard / AccountValueChart
  - 有 mockData.ts 可直接替换数据层；useWebSocket hook 可对 WS/SSE
  - 信息架构正是竞技场形态：品牌页 → 实盘观战 → 排行榜 → 模型详情

---

## 二、目标形态

```
┌──────────────────── 竞技场前端（React 新应用）────────────────────┐
│  Home 品牌/规则   Live 实时观战   Leaderboard 排行榜   ModelDetail │
└───────────────┬──────────────────────────────────────────────────┘
                │ axios (REST) / 未来 SSE
                ▼
          FastAPI 8091（现有端点复用 + summary 指标扩展）
                │
   ┌────────────┼────────────┐
   ▼            ▼            ▼
 agent-us     agent-cn     agent-hk
 双模型同池    双模型同池    双模型同池     ← P0-3 多模型激活
 （v4-flash + v4-pro 起步，OpenRouter 可扩）
                │
                ▼
      本地 quantmind 数据仓库（现有）
```

---

## 三、阶段规划

### 阶段一：P0-3 多模型激活（后端/配置，约 1 天）

1. **实测 `deepseek-v4-pro` 可用性**（发 1-token 请求验证计费/延迟/输出格式）
2. **三市场 config 各启用 2 模型**：`deepseek-v4-flash` + `deepseek-v4-pro` 同池竞争
   - `configs/deepseek_us_test.json` / `astock_config.json` / `deepseek_hk_test.json`
   - 注意：不改 `default_config.json`（gpt-5 占位，跑了会建空目录）
3. **双模型补跑历史**（08-24~08-28 × 3 市场 × 2 模型）：用 `backfill_us_agent.sh` 模式扩展
   → 排行榜立刻有竞争（6 个 agent）
4. **summary 指标扩展**（多模型后排行榜才有可比性，NOF1 洞察 2）：
   Sharpe、胜率、盈亏比、费用占比、平均持仓时长、多空一致性
   → `/api/agents/{a}/performance` summary + `/api/overview` 加字段
5. **可选：OpenRouter 接入**（用户提供 key，验证国内可达性）→ gpt-5 / claude /
   gemini / qwen 全开，凑齐 7 模型竞技场

### 阶段二：前端复刻（React 竞技场，约 1 周）

- **新目录 `arena/`**（React + Vite + TS + Redux Toolkit + visx + antd），不动现有 nof0
  （渐进替换，老前端保底可回退）
- 部署：vite build → 静态产物进 nginx/新容器，或直接替换 serve_nof0 的根目录
- 页面映射（coke-nof1 → 我们）：

| coke-nof1 页面 | 我们的对应 | 数据源（8091 现有端点） |
|---------------|-----------|------------------------|
| Home | 品牌页：三市场 × 双模型竞技介绍、赛季规则 | 静态 + /api/status |
| Live | 实盘观战：账户价值曲线（visx 多线+基准虚线）、持仓/成交面板、三市场切换 | /api/data/*、/performance |
| Leaderboard | 排行榜：排名表（收益/回撤/Sharpe/胜率/费用）+ 归一化净值对比图 | /api/agents、/performance |
| ModelDetail | 模型详情：指标卡、决策记录、交易明细、记忆摘要 | /performance、/positions、/trades |

- 视觉方向：深色终端竞技场美学（参照 nof1.ai 现场感），遵守 design-quality 规则
  （层级/节奏/动效/语义色，避免模板感）
- 保留：主题切换、三市场切换、30s 自动刷新（过渡期先轮询，未来 WS/SSE）

### 阶段三：竞技场闭环（P1，约 1 周）

1. **每日自动调度**：`scripts/daily_trade.sh` + cron（sync 数据 → 三市场双模型跑最新交易日）
2. **持仓 exit plan**：每笔买入登记止损/止盈价，风控网关监控、触发提醒
3. **置信度输出**：agent 决策带 0-100 confidence，前端展示
4. 赛季概念：周/月榜、累积排名（P2 再细化）

### 阶段四（P2，后续）
归因分析、回测对比系统、模型相关性矩阵、赛季结算、用户跟单

---

## 四、依赖与风险

| 风险 | 影响 | 对策 |
|------|------|------|
| deepseek-v4-pro 不可用/太贵 | 阶段一卡住 | 实测先行；不可用则换 vision-exp 或单模型先跑 |
| OpenRouter 国内不可达 | 7 模型凑不齐 | deepseek 双模型先跑竞技场，OpenRouter 后续验证 |
| 前端换技术栈字段对齐 | 返工 | 先列 8091 端点→前端字段映射表再动手 |
| docker 加前端容器 | 端口/挂载冲突 | 复用现有 8080/8091 模式，nginx 静态 |
| 多模型补跑耗时 | 一天跑不完 | 沿用 backfill 脚本并行化（三市场并行） |

## 五、决策点（待用户拍板）

1. **多模型起步**：先 deepseek 双模型（零成本立即跑）？还是等 OpenRouter key？
2. **前端目录**：新 `arena/` 渐进替换（推荐）？还是直接重写 nof0？
3. **部署方式**：vite build 静态产物 + nginx 容器（推荐）？
4. 阶段一是否需要连 P0-2（指标扩展）一起做？（推荐：是，排行榜可比性依赖它）
