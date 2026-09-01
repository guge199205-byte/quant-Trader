# BayMax Agent 升级规划（2026-09-01）

## 目标
盘中分析从"单次提示词"全面升级为 dsh agent（工具 + 写代码 + 记忆 + 多模型），
执行层闸门/记账/哨兵不变。分层：调度（cron）→ 决策（dsh agent）→ 执行（闸门+桥）。

## 阶段 A：明天开盘前必须完成（2026-09-01 晚）

| # | 项 | 状态 |
|---|----|------|
| A1 | v4-flash 切 dsh 试点（per-agent） | ✅ 已上线（agent_mode.json） |
| A2 | 数据层：大盘/板块/分钟K/五档/L2 自采 | ✅ 已上线 |
| A3 | baymax_quantdb 只读工具（1077万行金库） | ✅ 已上线（8105） |
| A4 | **多模型接入**：dsh LLM 适配器 baseURL/apiKeyEnv 可配 → GLM/ChatGPT 端点 | ✅ 已上线（baymax.glm.cordis.yml，per-agent 可选） |
| A5 | **agent 思考过程落盘**：persona 要求附工具调用清单 | ✅ 已上线 |
| A6 | **跨 agent 参考注入**（共识轻量版）：其他 agent 上轮建议进提示词 | ✅ 已上线 |

## 阶段 B：本周（验证试点后）

| # | 项 | 说明 |
|---|----|------|
| B1 | 共识执行闸门：同一决策 ≥2 agent 一致才执行，分歧留观 | P2-7 欠账 |
| B2 | watch 规则 buy 侧：候选池回落买点自动触发 | 哨兵扩展 |
| B3 | 滑点保护：成交价劣于决策价 X% → 告警/重试策略 | 风控纵深 |
| B4 | dsh 会话预热：09:25 预跑 agent 会话，09:30 直接出决策 | 消解延迟 |

## 阶段 C：下周+

| # | 项 | 说明 |
|---|----|------|
| C1 | v4-pro/glm 全量切 dsh，各自配不同模型（DeepSeek/GLM/ChatGPT 三角色） | 多模型对比 |
| C2 | 港股执行：富途 SIM 分账归属规则 | 三市场打通 |
| C3 | 回测对比：历史回放 dsh vs llm 决策质量 | 量化收益 |
| C4 | 前端：agent 工具调用过程流展示、watch 可视化 | 过程透明 |

## 关键决策记录
- dsh LLM 层：provider 是 settings 字符串；dsh-llm-deepseek 适配器支持
  config.baseURL + config.apiKeyEnv → OpenAI 兼容端点（GLM/ChatGPT）可复用适配器，
  无需写新插件（2026-09-01 查证）
- 记忆：headless 每次新会话；跨日记忆靠 baymax_memory 文件工具（read/append）
- 执行安全网（闸门）不因 agent 化放松：虚拟现金红线/杠杆硬约束/T+1/涨跌停/在途单
