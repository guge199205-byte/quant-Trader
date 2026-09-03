# 盘后复盘 Agent 设计（阶段 1：自我进化闭环）

> 依据 docs/AGENT_ROADMAP.md 阶段 1。目标：收盘后每个分账 agent 自动复盘
> 当日盈亏归因 → 经验沉淀（market_memory）→ 明日预案，无人干预。
> 阶段 2（假设库）以此为数据入口。

## 1. 范围

**做**：盘后逐 agent 复盘（真实成交归因）、memory 沉淀、明日预案、复盘产物落盘与次日引用。
**不做**（v1）：盘前自动挂 watch、假设库回测、前端复盘页——留接口不实现。

## 2. 触发与调度

- cron（北京 15:35，周一到周五）：`python scripts/post_review.py`
  （复盘只读+写 memory，不交易，无时段限制；盘中误触发也无副作用）
- 手动：`python scripts/post_review.py --agent glm-5.3-flash --date 2026-09-03`

## 3. 输入（全部来自现有产物，零新采集）

| 输入 | 来源 |
|---|---|
| 当日该 agent 成交流水（买/卖/价/量/时点） | logs/live_trade_*.jsonl |
| 当日该 agent 每轮决策（四段式+JSON） | data/agent_data_astock/{agent}/log/{date}/log.jsonl |
| 当日净值轨迹（首/末/极值/盘中起伏） | logs/live_equity.jsonl |
| 当日盘面状态（大盘/情绪温度/新鲜度） | market_state.build_market_state() |
| 当前持仓与成本 | logs/live_ledger.json |

## 4. 复盘任务（prompts/review_workbook.py，复用 dsh run_agent + baymax_memory 工具）

复盘 agent 按此工作：
1. **逐笔归因**：每笔成交对照“当时决策理由”复盘——理由兑现/证伪/未兑现？
   赚亏归因到 决策质量 / 时机 / 运气 / 数据失真（四选一）。
2. **行为审计**：当日有无 追高/杀跌/犹豫错过/仓位失衡 四类问题；有则写明证据。
3. **沉淀**：调用 `baymax_memory append_memory` 写入 ≤3 条经验教训
   （日期+事件+教训+下次如何识别）。
4. **明日预案**：输出结构化 JSON `plan`（每只持仓：动作/触发条件/目标价/止损），
   以及 ≤2 条 `watch` 建议（代码/触发/动作）。
5. **假设候选**（阶段 2 预留）：若复盘发现可验证规律，输出 `hypothesis_candidates`。

## 5. 输出

- `logs/review/{agent}/{date}.md`：人读复盘（四段：一句话总评/逐笔归因表/行为审计/明日预案）
- `logs/review/{agent}/{date}.json`：机器读（lessons/plan/watch/hypothesis_candidates）
- memory：经 append_memory 写入（market_memory 体系，现有读取路径自然可见）

## 6. 次日引用（v1.5，本阶段尾接入）

live_hourly_analysis 首轮分析注入「昨日复盘要点」段（读前一日 review JSON 的
lessons+plan 摘要）；当日该 agent 上下文自带“我昨天的教训与预案”。

## 7. 质量与降级

- 只复盘真实数据；当日无成交/无分析 → 输出“无交易复盘”（仍写 memory 观察市场）。
- 桥/LLM 失败 → 降级为数据摘要复盘（同现有分析降级模式），不静默。
- 复盘不调用下单工具；只读 + memory 写。

## 8. 验收（DONE 标准）

1. 连续 5 个交易日 cron 自动产出每 agent 复盘 md+json；
2. market_memory 随复盘增长（含日期标注）；
3. v1.5 后，次日首轮分析提示词可见“昨日复盘要点”。
