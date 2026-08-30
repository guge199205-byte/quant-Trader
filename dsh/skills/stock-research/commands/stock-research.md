---
description: 个股深度研究多 Agent 编排 — 输入股票代码，5 分析师并行 → 多空辩论 → 研究经理汇总，输出深度研究报告并落盘
---

# Stock Research 执行手册

触发词：深度研究/个股研究/研究 XXXX/多角度分析。执行前先读 `skills/stock-research/SKILL.md`（数据铁律 + 单位口径）。

## 模式一：全流程研究（默认）

### Step 1：归一化代码 + 跑数据脚本

```bash
# 代码归一化：600036 / SH600036 / 600036.SH / 招商银行 均可，脚本自动转后缀格式
docker cp <repo>/skills/stock-research/scripts/research_data.py quantmind:/tmp/
docker exec quantmind python3 /tmp/research_data.py --symbol <输入>
# 记下输出 symbol（后缀格式）与 JSON 路径 /data/reports/stock_research/{symbol}_{date}.json
docker cp quantmind:/data/reports/stock_research/{symbol}_{date}.json /tmp/stock-research-{symbol}.json
```

脚本失败（输出不含 `"out"` 字段）时：检查 QuantDB 是否同步（`data/quantdb` 分区）、股票代码是否正确；不硬出报告。

### Step 2：准备目录

```bash
mkdir -p /tmp/stock-research/{symbol}/reports
```

### Step 3：Phase 1 — 并行 5 分析师（一次消息发 5 个 Agent）

每个 Agent prompt 构造：

```
你是{角色}。读取数据包 /tmp/stock-research-{symbol}.json 与角色规则。
角色规则（完整内容）: <prompts/{role}.md 全文>
按规则输出报告并保存到 /tmp/stock-research/{symbol}/reports/{role}.md
```

并行 5 个：technical / news / sentiment / fundamentals / market。

### Step 4：Phase 2 — 并行多空辩论（一次消息发 2 个 Agent）

```
你是多头/空头研究员。读取 /tmp/stock-research/{symbol}/reports/ 下全部 5 份报告 + 数据包。
角色规则: <prompts/bull.md 或 bear.md 全文>
```

### Step 5：Phase 3 — 研究经理汇总（串行）

```
你是研究经理。读取全部 7 份报告 + 数据包。
角色规则: <prompts/research_manager.md 全文>
最终报告保存到 /tmp/stock-research/{symbol}/reports/final.md
```

### Step 6：md → PDF → 落盘深度分析目录

```bash
# ① 取回 final.md 并转 PDF（容器内研报管线）
docker cp /tmp/stock-research/{symbol}/reports/final.md quantmind:/tmp/ma_report.md
docker exec quantmind bash -lc "cd /app && python3 backend/scripts/md_to_pdf_report.py /tmp/ma_report.md /tmp/ma_report.pdf"
# ② 落盘：A股市场/{股票名}/（与深度学习分析报告同列表；容器 root 目录，须容器内操作）
docker exec quantmind bash -c "mkdir -p '/data/reports/trading_agents/A股市场/{股票名}' && cp /tmp/ma_report.md '/data/reports/trading_agents/A股市场/{股票名}/{股票名}{代码}_{date}_深度研究分析报告.md' && cp /tmp/ma_report.pdf '/data/reports/trading_agents/A股市场/{股票名}/{股票名}{代码}_{date}_深度研究分析报告.pdf'"
```

股票名从数据包 `sector` 或行情结果获取（如无中文名则用代码）。

### Step 7：聊天速览

评级 + 现价/PE/主力资金/新闻情绪各一个关键数字 + 核心风险 1 条 + 报告路径。

## 模式二：指定分析师

用户说"技术面分析 600519"时：Step 1 + 只跑对应分析师（news 分析师默认走双通道），输出该角色报告 + 聊天速览，不进入辩论/汇总。

## 数据包字段速查（各分析师取数）

| 切片 | 内容 | 主要用户 |
|---|---|---|
| quote | 120 日 K 线（前复权）、60 日高低、20/60 日涨幅 | 技术/情绪 |
| indicators | ma/rsi/kdj/macd/vol 全列 + close_20d | 技术 |
| valuation | pe_ttm/pb/ps/市值/ttm 利润营收 | 基本面 |
| financials | 三表最新报告期核心科目 | 基本面 |
| l2_flow | 近 10 日主力净流入（亿）+ 5/10 日累计 | 资金情绪 |
| sector | CSRC 一级行业 | 基本面/市场 |
| market_context | 5 指数、行业涨幅榜、板块资金流 1/5/10 日 | 市场 |
| news | 近 60 天 FinBERT 事件 + 七维统计 | 新闻 |
