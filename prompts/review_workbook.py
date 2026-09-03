"""盘后复盘工作法（阶段1：自我进化闭环）——作为 dsh 复盘任务的前置引导。

复盘只读+沉淀，不下单；输出 md 结构 + JSON（lessons/plan/watch/假设候选）。
"""

REVIEW_WORKBOOK = """【盘后复盘工作法（只读+沉淀，禁止调用任何下单工具）】

你正在为 {agent}（{display}）做 {date} 的收盘复盘。先通读下方当日事实，
再按步骤输出。所有归因必须对照事实数据，禁止编造成交或价格。

步骤：
一、逐笔归因：对【当日成交】每一笔——写出该笔当初的决策理由
（若日志缺失理由则标"无决策记录"），判定结果：赚/亏，并把结果归因到
{{决策质量|时机|运气|数据失真}} 之一，一句话说明依据。

二、行为审计：对照四类问题自查当日表现，命中才写，未命中不写：
追高（涨超+5%后买入/不追高纪律违反）、杀跌（恐慌低点卖出）、
犹豫错过（watch 后未执行且事后证明该动）、仓位失衡（单票>50%权益未处理）。

三、沉淀教训：调用 baymax_memory 的 append_memory，写入 ≤3 条经验教训，
每条格式：【{date}】事件一句话 → 教训 → 下次如何提前识别/应对。
沉淀完在输出中列出已写入的 memory 条数。

四、明日预案：对每只持仓给出 plan（JSON），含 action(hold/sell/buy/watch)、
code、name、触发条件（明确价位/事件）、目标与止损；无新想法的给 hold+条件。
最多给 2 条 watch 建议（明日盘中哨兵候选：code/触发价/动作/理由）。

五、假设候选（可选）：若今日复盘发现可回测验证的规律，输出
hypothesis_candidates（描述/触发条件/方向/为什么），没有就空数组。

输出格式：
- 正文用 md（四段：一句话总评/逐笔归因表/行为审计/明日预案），
- 最后附一个 JSON 块（与正文同一次回复）：
{{"lessons":["..."],"plan":[{{"action":"","code":"","name":"","trigger":"","stop_loss":null,"reason":""}}],
  "watch":[{{"code":"","price":null,"action":"","reason":""}}],
  "hypothesis_candidates":[]}}

时间盒 2-3 分钟；当日无成交时仍输出：无交易复盘 + 市场观察 1 条 + memory 1 条。
"""


def build_review_prompt(agent: str, display: str, date: str, facts: str) -> str:
    return (REVIEW_WORKBOOK.format(agent=agent, display=display, date=date)
            + "\n\n===== 当日事实（系统采集）=====\n" + facts)