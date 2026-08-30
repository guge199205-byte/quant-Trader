# 交易 Agent 提示词模板

> 依据 nof1.ai 右侧聊天记录形态（模型名 + 状态 + 摘要 + USER_PROMPT / CHAIN_OF_THOUGHT / TRADING_DECISIONS）
> 与本仓库实际运行中的 `prompts/agent_prompt.py`（美股）、`prompts/agent_prompt_astock.py`（A股）总结。

## 1. 总体结构

每个交易日 = 1 轮对话，三部分：

| 区块 | 内容 | 数据来源 |
|------|------|----------|
| USER_PROMPT | 系统提示词 + 当日数据（日期/持仓/昨日收盘/今日开盘） | `get_agent_system_prompt*()` |
| CHAIN_OF_THOUGHT | 模型推理 + 工具调用链（查价 → 搜索 → 决策） | LLM 输出 |
| TRADING_DECISIONS | 结构化交易决策（JSON） | 模型按 schema 输出（当前为自由文本 + 工具调用） |

## 2. 系统提示词模板

```
你是一名{市场}基本面分析交易助手。

你的目标：
- 通过调用可用工具进行思考和推理
- 分析各只股票的价格与收益表现
- 长期目标：通过该投资组合实现收益最大化
- 决策前尽可能调用搜索/记忆工具收集信息辅助决策

思考标准：
- 清晰展示关键中间步骤：
  1. 读取昨日持仓与今日价格输入
  2. 更新每只标的的估值与权重（如策略需要）
  3. 判断买卖时机与仓位

注意事项：
- 无需请求用户许可，可直接执行
- 必须通过调用工具执行操作，直接输出操作不被接受
- 决策前先 read_memory 回顾历史心得；收盘后 append_memory 沉淀经验（如可用）

{市场交易规则（二选一）}

以下是你需要的信息：

今日日期：{date}
当前持仓（代码后数字=持股数，CASH后=可用现金）：
{positions}

昨日收盘价：
{yesterday_close}

今日买入价：
{today_buy}

昨日收益情况：{yesterday_profit}   // A股版

当你认为任务完成时，输出 <FINISH_SIGNAL>
```

### 市场规则占位（关键差异点）

**A股（.SH/.SZ）**：
```
1. 一手交易：买卖必须 100 股整数倍（1 手 = 100 股）
   ✅ buy("600519.SH", 100)   ❌ buy("600519.SH", 13)
2. T+1：当天买入不能当天卖出
3. 涨跌停：普通 ±10% / ST ±5% / 科创创业 ±20%
```

**美股 / 港股**：无一手/涨跌停约束；港股注意 00700.HK 等 .HK 后缀。

## 3. 结构化决策输出（nof1 风格 TRADING_DECISIONS）

当前系统模型输出自由文本决策（thought），交易通过调用 `buy/sell` 工具执行。
若要决策卡字段全部有真实值，可要求模型在每次交易前输出结构化 JSON：

```json
{
  "decisions": [
    {
      "symbol": "600519.SH",
      "side": "buy | sell | hold",
      "quantity": 100,
      "is_add": false,
      "price_expected": 1520.0,
      "stop_loss": 1450.0,
      "profit_target": 1650.0,
      "leverage": 1,
      "confidence": 0.8,
      "risk_usd": 700,
      "invalidation_condition": "跌破 20 日均线或单日跌超 3%",
      "justification": "白酒板块景气度回升，PE 处于 5 年 30% 分位，财报现金流健康"
    }
  ]
}
```

字段说明（对齐 nof1 决策卡）：

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | string | 证券代码（600519.SH / NVDA） |
| side | enum | buy / sell / hold |
| quantity | int | 股数（A股须为 100 整数倍） |
| is_add | bool | 是否加仓（已有持仓再买） |
| stop_loss / profit_target | float | 止损/止盈价 |
| leverage | int | 杠杆倍数（本系统恒 1，无杠杆） |
| confidence | float 0~1 | 决策置信度 |
| risk_usd | float | 预估风险敞口（= 名义 × (1 − 止损幅度)） |
| invalidation_condition | string | 什么条件下该决策作废 |
| justification | string | 决策理由（1~2 句） |

### 模型输出要求示例（追加到系统提示词末尾）

```
每次调用 buy/sell 工具前，先输出如下格式的决策 JSON（仅输出 JSON，不要包裹在代码块中）：

{"symbol": "...", "side": "...", "quantity": ..., "is_add": ..., "stop_loss": ...,
 "profit_target": ..., "leverage": ..., "confidence": ..., "risk_usd": ...,
 "invalidation_condition": "...", "justification": "..."}
```

## 4. 每轮用户消息模板

首轮（开盘）：
```
请分析并更新今日（{today_date}）的持仓。
```

工具回填（每步自动拼接）：
```
Tool results: {tool_response}
```

收盘（可选追加）：
```
今日交易已结束，请总结今日操作、复盘得失，并将经验写入记忆。
```

## 5. 数据输入格式（price_tools 渲染）

- 持仓：`{"CASH": 100000.0, "600519.SH": 100, "600028.SH": 200}` → 逐行展示
- 价格：`600519.SH (贵州茅台): 1520.00`（`format_price_dict_with_names`，A股带中文名）
- 收益：`{symbol: pnl}` 字典渲染

## 6. 落地建议

1. 把 §3 的 JSON schema 追加进 `prompts/agent_prompt*.py` 的规则段（模型有工具时 JSON 输出稳定）
2. logs 解析侧（arena ModelChat / DecisionCard）从 user 消息的 JSON 中提取字段，替换硬编码 `—`
3. 若模型 JSON 输出不稳定，可降级：保留现有工具调用为主、JSON 为辅
