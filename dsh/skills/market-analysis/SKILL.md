---
name: market-analysis
description: "市场分析报告（大盘快照版）— 复用市场分析页面全部数据能力：核心指数、市场广度与情绪温度、行业板块/热门概念热力图、行业多日涨跌幅对比（1/3/5日）、板块资金流（1日/5日/10日）、个股主力资金 Top20、标签体系统计。用户说「市场分析」「大盘分析」「行情分析」「今天市场怎么样」「市场报告」时使用：跑取数脚本（market_analysis.py）→ AI 基于 facts 撰写解读 → Markdown → PDF（研报风）→ 落盘股票报告目录 → 聊天回复速览。触发词：市场分析、大盘分析、行情分析、市场报告、看盘、今天市场、今日市场、市场怎么样"
---

> ## ⚙️ 运行环境契约（最高优先级，先于本文其余内容执行）
>
> 本技能可能运行在 **QuantBot（QwenPaw 容器）** 或**宿主机/本地 Claude Code**。执行前先探测环境（`which docker`、API 连通性），并遵守以下映射规则：
>
> 1. **后端 API 地址**：QwenPaw / 容器网络内一律用 `http://quantmind:8000`（`quantmind` 是 docker 网络别名）；仅宿主机调试用 `http://127.0.0.1:8000`。正文中出现的 `127.0.0.1:8000`、`localhost:800x`，在 QwenPaw 环境下自动替换为 `http://quantmind:8000`。
> 2. **取数脚本执行**：凡 import 了 `pandas / duckdb / psycopg2 / numpy / sqlalchemy` 等重依赖或 `backend` 包的脚本，**必须在 quantmind 容器内执行**（QwenPaw 本地 venv 无这些依赖）：
>    ```bash
>    docker cp <脚本路径> quantmind:/tmp/<脚本名> && docker exec -w /app quantmind python3 /tmp/<脚本名> <参数>
>    ```
>    脚本源三选一：宿主机 repo `skills/<name>/scripts/`、QwenPaw 工作区 `/app/working/workspaces/default/skills/<name>/scripts/`、挂载目录 `/quantmind/skills/<name>/scripts/`。纯标准库脚本（无重依赖）可在 QwenPaw 本地直接跑。
> 3. **报告落盘**：股票报告页可见的 MD/PDF 报告，直接写 `/data/reports/trading_agents/{市场或类别}/{股票名}/`（QwenPaw 对 `/app/db` 有写权限，**直接写文件，不要 docker cp**）；过程数据 facts 写 `/data/reports/<类别>/`（`/data` 可写）。
> 4. **MD → PDF 转换（按优先级降级）**：
>    ① `docker exec -w /app quantmind python3 backend/scripts/md_to_pdf_report.py <输入.md> <输出.pdf>`（研报级排版，首选）；
>    ② docker 不可用时，**改用 QwenPaw 内置 `pdf` 技能**把 MD 转成 PDF；
>    ③ 两者都不可用则只交付 MD，并明确告知用户 PDF 未能生成及原因。
> 5. 本文中的 `~/.claude`、`cp -r ... ~/.claude/skills` 等说明仅适用于本地 Claude Code 维护者，**QuantBot 不要执行**。

# market-analysis — 市场分析报告（大盘快照版）

把市场分析页面的数据能力（指数/广度/行业/概念/资金流/标签）变成一份结构固定的报告（Markdown + PDF），落盘到前端「股票报告」页可见目录，并在聊天里回复速览。**报告必含「八、市场解读与次日关注」——AI 必须基于 facts 数据撰写，禁止编造数字。**

与 [[daily-review]] 的区别：daily-review 是盘后复盘（含新闻情绪、模型推理信号、次日六维研判）；本 skill 是**市场快照**（行情结构 + 资金流 + 板块强弱），适合盘中/盘后随时输出当日市场状态。

## ⚠️ 单位铁律（先查 [[quantdb-fields]]，最高优先级）

| 陷阱 | 正确口径 |
|---|---|
| `l2_factors.flow_*` 是**元** | 报告统一换算**亿元**（脚本 `_yi()` 已处理） |
| `index_daily.amount` 是**万元** | 指数成交额脚本已转亿元 |
| `technical_indicators.pct_change` = **%** | 涨跌家数/涨停/行业均涨幅全用它；涨停≈≥9.8%、跌停≈≤-9.8% |
| `index_daily.preClose` **全 NULL** | 指数涨跌幅由 close 序列自算（后端已处理，勿另算） |
| 资金流依赖 `l2_factors` | 最新日期可能与行情日不同步（L2 更新滞后 1 日属正常），facts 里以 trade_date 为准，滞后时在报告中声明 |

**写报告时所有数字必须来自 `{date}_facts.json`，不得自行推算或编造。**

## 执行流程（每次 5 步）

### 第 1 步：跑取数脚本（宿主机）

```bash
cd <repo>/skills/market-analysis/scripts
python3 market_analysis.py          # 最新交易日；--out 自定义输出目录（默认 data/reports/market_analysis/）
```

脚本复用后端 `quantdb_feed` 聚合口径，产出 `{date}_facts.json`（全部原始数据）+ `{date}_report.md`（骨架）。QuantDB 不可用时脚本报错退出——不要假装出报告，直接告诉用户数据未同步。

### 第 2 步：AI 撰写解读（核心增值环节）

基于 facts 在骨架的「八、市场解读与次日关注」补写四小节（每节 2-4 句，数字引用 facts）：
- **8.1 市场总览**：指数涨跌结构（几涨几跌、深/创弱于上证？）、两市量能（亿，与昨日对比如 facts 有）、情绪温度（赚钱效应 %、涨跌停家数）
- **8.2 结构性机会**：涨幅前列行业/概念 2-3 个 + 对应板块资金流方向（净流入板块）；10 日资金持续流入的方向
- **8.3 风险提示**：净流出前列行业、跌停/炸板、指数与广度背离（如指数涨但下跌家数多）
- **8.4 次日关注**：2-3 条可跟踪信号（具体板块/个股/资金/情绪阈值）

写完后通读全文：数据表与解读数字一致、无矛盾、单位统一为亿元。

### 第 3 步：Markdown → PDF（研报风，复用 md_to_pdf_report 管线）

```bash
# ① 复制 md 进容器（.claude 目录未挂载）
docker cp <repo>/data/reports/market_analysis/{date}_report.md quantmind:/tmp/ma_report.md
# ② 容器内转换（reportlab，封面/红涨绿跌/斑马纹表格自动生效）
docker exec quantmind bash -lc "cd /app && python3 backend/scripts/md_to_pdf_report.py /tmp/ma_report.md /tmp/ma_report.pdf"
# ③ 取回宿主机
docker cp quantmind:/tmp/ma_report.pdf <repo>/data/reports/market_analysis/{date}_report.pdf
```

### 第 4 步：落盘股票报告目录（前端「股票报告」页可见）

```bash
mkdir -p <repo>/db/trading_agents_results/市场分析
cp <repo>/data/reports/market_analysis/{date}_report.md <repo>/db/trading_agents_results/市场分析/市场分析_{date}.md
docker cp <repo>/data/reports/market_analysis/{date}_report.pdf quantmind:/data/reports/trading_agents/市场分析/市场分析_{date}.pdf
```

文件名固定：`市场分析_{YYYY-MM-DD}.md` / `.pdf`。

**用户要求「放深度分析那里」时**：额外落一份到 A股市场 分组（与个股深度分析/投研报告同列表展示，报告管理页 → A股市场 → 市场分析）：

```bash
docker exec quantmind bash -c "mkdir -p '/data/reports/trading_agents/A股市场/市场分析' && cp /tmp/ma_report.md '/data/reports/trading_agents/A股市场/市场分析/市场分析_{date}.md' && cp /tmp/ma_report.pdf '/data/reports/trading_agents/A股市场/市场分析/市场分析_{date}.pdf'"
```

> 注：`A股市场/` 目录为容器 root 创建，宿主机无写权限，须在容器内操作（docker exec 为 root）。

### 第 5 步：聊天回复速览

回复用户一段 3-5 行的速览（指数、情绪、最强板块、资金动向、明日关注 1 条）+ 报告文件路径（md + PDF）。

## 数据能力清单（对应市场分析页面功能）

| 报告章节 | 后端数据源 | 说明 |
|---|---|---|
| 一、核心指数 | `index_daily` | 上证/深成/创业板/沪深300/科创50：价格/涨跌/成交额/5日趋势 |
| 二、市场广度 | `daily_forward`+`technical_indicators` | 涨跌家数/涨停跌停/两市成交额/赚钱效应/炸板率估算 |
| 三、行业板块 | `sector_members` 申万一级 | 31 行业均涨幅/成交额/领涨股，Top10+Bottom10 |
| 四、行业多日涨跌幅 | `daily_unadjusted`+`sector_members` | 行业 1/3/5 日累计涨幅对比（成分股中位数），看启动时点与主线持续性 |
| 五、热门概念 | `sector_members` 概念板块 | 概念均涨幅 Top/Bottom |
| 六、板块资金流 | `l2_factors.flow_*` | 1日/5日/10日 行业净流入（亿元）+主力占比 |
| 七、个股资金流 | `l2_factors.flow_*` | 主力净流入 Top20 / 净流出 Top10 |
| 八、标签体系 | `sector_members` 聚合 | 标签总数/覆盖股票/热门标签 |

**多日对比解读口径**（四、行业多日涨跌幅，中位数）：
- `1日% ≫ 3日% ≫ 5日%`：行业**刚启动**（如种植业 +14.1/+8.4/+2.6），次日接力或退潮均有可能
- `5日% 持续领先且 1日% 续强`：**主线行业**（趋势延续，如林业 +10.75/+7.27/+3.11）
- `5日% 强但 1日% 转负`：**退潮信号**（高位滞涨）
- 与「六、板块资金流 10 日净流入」交叉验证：多日强势 + 资金持续流入 = 可信主线；强势但资金流出 = 纯情绪炒作

## 已知边界

- 仅支持**最新交易日**（与市场分析页面一致），不支持历史日期回放
- 行业/概念为静态分类（`sector_members`），非实时申万调整
- 资金流依赖 L2 数据日频更新；L2 未更新时对应章节为空——报告里如实标注，不填 0
- 若用户需要含新闻情绪/模型推理信号的盘后深度复盘，改用 [[daily-review]]
