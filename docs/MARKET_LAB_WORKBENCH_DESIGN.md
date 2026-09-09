# 行情回测工作台：合并页 + 策略备注 + Agent 对话

> 2026-09-09 设计。目标：把「单标的回测」与「策略库」合成一个同屏联动的工作台，
> 并为每条策略加上备注与 agent 对话（问逻辑 / 改策略）。
> 落地分四期，P0 纯前端，P1–P3 逐层加后端。

## 进度

| 期 | 状态 | 落点 |
|---|---|---|
| P0 | ✅ 2026-09-09 已上线（commit `04eb38a`，已 deploy --ui） | `Workbench.tsx` / `StrategyList.tsx` / `ResultView.tsx` / `StrategyPanel.tsx` / `useWorkbench.ts` / `labResult.ts`(+test) |
| P1 | ✅ 2026-09-09 已上线（commit `611cffb`，已 deploy --api） | `pine_library.py` 备注段 + 3 端点 + `NotePanel.tsx` + 左栏 💬 角标（11 单测） |
| P2 | ✅ 2026-09-09 已上线（`ask` 问答；`edit` 留 P3） | `pine_chat_worker.py` + `AgentChatPanel.tsx` + 对话端点 + cron（24 单测） |
| P3 | ✅ 2026-09-09 已上线（`edit` 全链路已实跑验证，见下） | `pine_to_pyne.py` 的 `--source/--out` + `versions/` + `apply`/`revert`/`versions` 三端点 + 候选对比卡 |

**P3 验收实跑**（2026-09-09，`0001` Fib Confluence Scanner，标的 600309.SH 后复权）：
发「lenA/lenB/lenC 20/50/100 → 40/100/200」→ 17.9s 出候选，`chat/0001/<job_id>/`
里落 `candidate.pine` + 沙箱报告；对比卡显示净收益 127.8%→19.1%、最大回撤 44.4%→54.0%、
成交 239→281 笔；点采用 → `edited/0001.pine` 变成 40/100/200 并自动重排转写；
回滚 → 源码回到 20/50/100，且**被回滚掉的那版仍留快照**（可再切回去）。

P3 三个实现决定（与设计稿原文不同，均为实跑后改的）：

- 不做 `--scratch`，改成 `--source <pine> --out <dir>`：候选复用**同一条** CLI + 静态闸 +
  bwrap 沙箱，只把「读哪份 Pine、产物落哪」改道，避免出现第二条能绕过静态闸的链路。
  `--out` 只改产物目录，**不改 item_id**（否则 chat 目录名 job_id 会被当成策略 id，
  标题/分类全丢），标题等元信息仍从正式库里取。
- 任务状态多一个**非终态** `staged`：候选跑完但 worker 还没落对比时，界面继续轮询，
  否则前端会在对比卡出现前就停止刷新。
- 回滚做成**交换**而非单向消耗：先把当前生效版快照一份，再写回目标版，最后删掉被用的
  快照——所以「采用 → 回滚 → 再回滚」能来回切，不是一次性撤销。还原成与语料一字不差时
  顺手删掉 `edited/` 覆盖文件（否则界面永远挂着「已编辑」而内容等于原始语料）。
- 对比卡的「改前」列标出**基线生成时间**；基线比当前源码旧（手改过源码 / 闸门收紧后
  还没重跑）时打黄条提醒——实跑就踩到过：`0001` 的旧报告是收紧前 10000 本金跑的，
  拿它当基线会把「口径变了」误读成「策略变差了」。

P0 落地时顺带发现的静态闸漏洞（已修）：`pine_to_pyne.py` 原来只检查
`initial_capital` / `default_qty_value` / `default_qty_type` **存在**，不校验**取值**，
于是 `0001` 的候选写了 `initial_capital=10000` + `default_qty_value=100` 也能过闸——
同一策略与模板回测不同口径，跨策略比较会失真。现在 `_sizing_problems()` 校验取值
（100000 / 95 / percent_of_equity），189 份候选里偏离的 62 份已排批重跑。
（界面仍按每条链路真实本金显示，见 `labResult.ts` 的 `capital` / `unit`。）

P2 上线时踩到的坑（已修，`deploy.sh` 已加护栏）：容器以 root 在 bind mount 里建目录，
而 unlink/rename 只看**目录**写权限——`chat/queue` 被容器建成 `root:755` 后，宿主 worker
连自己的请求文件都摘不了牌，job 永远卡在 running。`deploy.sh` 现在会
`mkdir -p` + `chmod 777` 这几个两侧共写的目录；worker 侧 `_drop_request()` 也会把
「目录属主不对」翻成人话写进 job.error。

---

## 1. 现状（已核实）

### 1.1 页面：已经在同一页，只是分 tab

`arena/src/pages/MarketLab.tsx` 仅 60 行，4 个 tab（:16-21）共用一条路由
（`App.tsx:29`，导航 `Navbar.tsx:57`）：

| tab | 组件 | 问题 |
|---|---|---|
| `single` 单标的回测 | `components/lab/SingleBacktest.tsx` | 策略下拉只有后端 **6 个 PyneCore 模板**（:57-58、:257-263） |
| `library` 策略库 | `components/lab/StrategyLibrary.tsx` | **零 props 完全自包含**（:88），选中项 `cur`（:96）出不去 |
| `rank` / `screener` | `BatchPanel` | 与本次合并无关 |

**两个真实痛点**：

1. **库里的策略进不了单标的回测**——1001 条策略转写/回测完，只能在库右栏看一套简化卡片
   （Spark 内联 SVG + 最近 8 笔成交，`StrategyLibrary.tsx:394-457`），看不到完整的
   K线/净值/逐笔。
2. **结果两套实现**——库右栏的 Spark 与单标的页的 `EquityChart`
   （`SingleBacktest.tsx:307`）各画各的。

所以「合并」的实质是**去 tab 化**：策略库从 tab 变成常驻左栏，选中即驱动中栏回测区，
同时删掉库右栏那套重复结果。

### 1.2 两条回测链路产物不同构

| | 模板回测 | 库策略回测 |
|---|---|---|
| 入口 | `market_lab.run_backtest()`（`market_lab.py:403`） | `pine_library.enqueue_backtest()`（`pine_library.py:196`） |
| 执行 | API 进程内同步 | 宿主 `scripts/pine_transpile_worker.py`（cron 每分钟 + flock） |
| 产物 | `{stats, trades, equity, meta}` | `data/pine_transpile/<id>/report.json` |
| 明细字段 | `trades` = 明细数组 | `trades` = **数量**，明细在 `trade_rows[]` |

`stats` 两边同构：`{指标名: {value, pct}}`，28 个 TradingView 口径指标。

### 1.3 对话能力：全是只读回放，没有任何输入框

- `ChatStream.tsx:23` 是纯展示组件（props 只有 `agents: {name, lines}[]`），
  渲染四段式（总结/链路/决策/推理），Live.tsx 的「模型对话」tab 用它。
- 全站唯一的 textarea 是策略库的 Pine 代码编辑器（`StrategyLibrary.tsx:468`）。
- **没有任何自由文本投递端点**：`/api/live/analyze` 的 body 只有 `{agents}`，不能传话。
- `call_llm()`（`live_hourly_analysis.py:889`）只接受 system+user 两条消息；
  `pine_to_pyne.py:173` 的 `_chat(messages, model, think=False)` 接受任意消息数组，
  是现成的多轮入口。

### 1.4 策略库索引

`data/pine_library/index.json`：1001 条、9 分类、`total=usable=1001`。单条字段
`{id, category, title, title_en, file, url, source_kind, mtime, version, lines, bytes, indent_ok, has_strategy}`。
**没有任何备注字段**，且索引由 `scripts/pine_library_index.py:169-172` 整体原子重建
——**备注绝不能写进 index.json**，否则下次重建即丢。

源码优先级：`edited/<id>.pine` > `recrawl` > `desktop`（`pine_library.py:77-87`）。

### 1.5 安全边界（既定约束，不可绕过）

- API 容器**没有 bwrap**，且**绝不执行模型产出的代码**；
  一切模型产物只在宿主 worker 的 bwrap 沙箱里跑
  （`sandbox_cmd()`，`pine_transpile_worker.py:93`：`--ro-bind / /` + `--unshare-net` +
  只 bind 该策略目录可写；无 bwrap 直接拒跑，不降级）。
- `/api/market-lab/*` 需要 `x-api-token`（`api_server.py:98-124`，生产由 nginx 注入）。

---

## 2. 目标形态：三栏工作台

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 行情回测                                                                  │
├───────────────┬──────────────────────────────────────┬───────────────────┤
│ 策略库         │ 标的 [600309.SH▾] 区间[__] 复权[▾]    │ [备注] [对话]      │
│ [分类▾][搜索…] │ ┌──────────────────────────────────┐ │                   │
│ 1001 条        │ │  K线 + 买卖点 (KLineChart)        │ │ 0001 斐波那契云   │
│ ● 0001 斐波那契│ └──────────────────────────────────┘ │ ─────────────     │
│   趋势跟踪 ✎AI▶│ ┌──────────────────────────────────┐ │ 备注 (markdown)   │
│   0002 …       │ │  净值曲线 (EquityChart)           │ │ ┌───────────────┐ │
│   …            │ └──────────────────────────────────┘ │ │               │ │
│ [加载更多]     │ ┌────────────┬─────────────────────┐ │ └───────────────┘ │
│                │ │ 逐笔成交    │ 统计卡              │ │ 标签/评分/状态     │
└───────────────┴──────────────────────────────────────┴───────────────────┘
```

- **左栏常驻**（~300px）：分类下拉 + 搜索（沿用 300ms 防抖）+ 分页 200 条
  （`StrategyLibrary.tsx:37`）。每行角标：`✎` 已编辑 · `AI` 已转写 · `▶` 已回测 · `💬` 有备注。
- **中栏**：标的 + 区间 + 复权 + K线 + 净值 + 逐笔 + 统计卡。复用 `KLineChart`、
  `EquityChart`，与现有 `SingleBacktest` 的块一致。
- **右栏**：`备注` / `对话` 两个子 tab（不是页面级 tab）。

**策略来源扩展**：中栏策略从「6 个模板」扩展成「模板 ∪ 库策略」。选中库策略时走异步
job 轮询（2.5s，沿用 `StrategyLibrary.tsx:38` 的 `POLL_MS`），跑完渲染同款图表。

> 默认按「整页一个工作台」设计：`rank`/`screener` 的批量结果收进右栏抽屉。
> 若更想保留两个批量一级 tab，P0 只需把左栏/中栏/右栏塞进现有 `single` tab 即可，不影响后续期。

### 2.1 统一结果视图模型

前端把两条链路适配成同一个 `LabResult`（新增 `arena/src/api/labTypes.ts`）：

```ts
type LabResult = {
  source: 'template' | 'pine';
  strategy: { id: string; name: string; title?: string };
  symbol: string; adj: string; start: string; end: string; bars: number;
  stats: Record<string, { value: number | null; pct: number | null }>;
  trades: TradeRow[];                        // 统一成明细数组
  equity: { date: string; value: number }[];
};
```

适配器：`fromTemplate(BtResult)` / `fromPineReport(report)`——后者要把
`report.trades`(数量) 与 `report.trade_rows[]`(明细) 对齐，别把数量当明细渲染。

### 2.2 K线是主图，回测成交点必须落在 K线上

中栏第一屏就是 K线（`KLineChart.tsx`，lightweight-charts v5），**保留不动**，并且
两条回测链路的成交点都要标上去：

- `KLineChart` 的 props 是 `{bars, trades?: BtTrade[], showMarkers}`，把
  `trades[].entry_time` 画成「买↑」、`exit_time` 画成「卖↓」（:142-165）。
- **库策略不需要适配器**：`report.json` 的 `trade_rows[]` 字段与 `BtTrade` 完全一致
  （`{entry_time, entry_price, qty, signal, exit_time, exit_price, profit, profit_pct}`），
  `entry_time` 形如 `2016-04-08 09:30:00`，正好匹配 `KLineChart` 的
  `.slice(0, 10)` 取日期。实测 `data/pine_transpile/0001/report.json`：203 笔，
  字段逐一对得上。
- **必须同步标的与复权口径**：报告自带 `symbol` / `adj`（如 `600309.SH` / `backward`）。
  中栏的 K线要用**报告自己的** `symbol`+`adj` 拉，否则标记会落在错误的K线上
  （现库面板压根没拉 K线，这是 P0 要补的）。
- **密集成交**：203 笔 = 406 个 marker，叠在 2595 根K线上会很糊。默认画，
  给一个「成交点」开关（`showMarkers`），并在缩放后只画可视区间内的标记。

### 2.3 策略用到的指标也画在 K 线上

光有买卖点看不出策略为什么进出场——策略真正算过的 EMA/MACD/RSI 必须同时画出来：
与价格同量纲的叠加线进主图，振荡指标各自开副图。实现在
`backend/services/lab_indicators.py`（捕获）+ `arena/src/components/lab/indicators.ts`（对齐/配色）。

**为什么是运行时捕获，不是解析源码**：转写产物里**没有 `plot()`**（`pine_to_pyne.py`
的 SYSTEM_PROMPT 明确禁止 plot/alert/table/label/line/box），内置模板也没有——静态解析
无从下手。改在回测时给 `pynecore.lib.ta` 打桩，按「调用点」（`文件名@行号@字节码偏移`）
记录每根 bar 的实参与返回值。

- **必须行为透明**：打桩只包一层记录后原样返回。单测断言同一策略打桩前后
  `stats` / `trades` 逐位相同（`tests/test_lab_indicators.py::TestRealBacktest`）。
- **`@overload` 分派器是个坑**：`ta.highest/lowest/pivothigh/pivotlow` 等 6 个函数走
  `__pyne_bind__`，而 `functools.wraps` 会把该方法复制到包装层上——于是锚点绑到了真分派器、
  直接绕过记录（三个周期的 `highest` 还会互相污染）。修法：包装 `__pyne_bind__`，
  让它返回「记录版」的绑定可调用对象。
- **标签取实参，不取源码**：周期可能来自 `input()`（还会被界面覆盖）或嵌套函数参数，
  源码里读不到。捕获到的实参中「每根 bar 都一样且为整数」的挑出来做后缀 →
  `EMA(20)`、`MACD DIF(12,26,9)`。
- **量纲闸**：`ta.sma(volume, 22)` 也是 `sma`，值 3e7，画进主图会把价格压成一条直线。
  中位数偏离价格中位数超过 4 倍的一律降级到副图（`PRICE_RATIO_LO/HI`）。
- **指标套指标跟输入走，不跟函数名走**：`ta.highest(rsi, 14)` 名字在叠加表里，值域却是
  RSI 的 0-100（中位数 ~50 恰在价格的 ±4 倍护栏内，量纲闸拦不住）。pynecore 传给 `ta.*`
  的是当前 bar 的浮点数（不是 Series 对象），所以只能按「谁的输出与本调用的源值逐 bar
  逐位相同」认亲，链式一路走到根 → 与 RSI 同格。`ta.sma(stoch_rsi_k, 3)` 的源值被
  pynecore 注入的状态对象（list）挡住，要取第一个数值实参。
- **副图内按量级拆格**：同一条推导链共一格；格内量级差超过 4 倍再拆开——0541 一行三个
  `ta.sma` 中位数 0.0036 / 0.031 / 2.14，挤在一格时小的两条被压成贴着 0 的直线。
- **体积闸**：`MAX_OVERLAYS=24` / `MAX_PANES=24`。一条序列 ≈ 每根 K 线一个数，实测
  183 份报告 p95 = 16、最长 32 条；旧值 8/6 曾把 4 份报告的线悄悄截掉（0975 的 RSI
  区间高低点、成交量均线就这么消失过）。
- **对齐**：指标是全历史跑的，K 线可能只取尾部 600 根 → `alignSeries` 尾部切片；
  长度对不上（换了标的/周期）宁可整条不画，也不画一条错位的线。
- **两条链路都走同一个捕获器**：模板在 API 进程内同步回测时直接捕获；Pine 产物在宿主
  bwrap 沙箱回测时捕获，结果落 `report.json` 的 `indicators`——**容器侧永远不执行模型代码**。
- **老报告补数据**：`pine_lab/.venv/bin/python scripts/pine_backfill_indicators.py
  --missing-only --workers 8`（重跑沙箱回测，`validate()` 只在成功后写盘，失败保留原报告）。

---

## 3. 策略备注

### 3.1 存储

`data/pine_library/notes/<id>.json`（per-id 文件，与 `edited/<id>.pine` 同构，
避免并发写冲突；**不写 index.json**）：

```json
{
  "id": "0001",
  "note": "多周期斐波那契，趋势市有效；震荡市假信号多，需加 ADX 过滤",
  "tags": ["趋势", "多周期"],
  "rating": 4,
  "status": "观察中",
  "by": "user",
  "updated": "2026-09-09T11:20:00+08:00"
}
```

- `status` 枚举：`待研究` / `观察中` / `已采用` / `已弃用`
- `by`：`user` | `agent`（agent 写入的备注要能区分）
- **id 校验放宽**：备注要同时覆盖 6 个内置模板（id 形如 `sma_cross`）与库 id
  （`^\d{4}$|^x\d{3}$`，`pine_library.py:23`）。备注层用独立校验
  `^[a-z0-9_]{1,32}$`，不要复用 `_check_id`。

### 3.2 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/market-lab/library/{id}/note` | 读备注（不存在返回空结构） |
| PUT | `/api/market-lab/library/{id}/note` | 写备注（原子 `tmp.replace`） |
| GET | `/api/market-lab/notes` | 全部备注 id 集合，供左栏 `💬` 角标 |

服务函数放 `backend/services/pine_library.py`（`get_note` / `save_note` / `list_notes`），
路由放 `api_server.py:2056-2088` 一带。

---

## 4. Agent 对话

### 4.1 两种模式

| 模式 | 输入 | 产出 | 用途 |
|---|---|---|---|
| `ask` | 用户问题 | 文本回答 | 问逻辑/风险/适合行情；可一键「写入备注」 |
| `edit` | 用户改法 | 新 Pine + 沙箱回测对比 | 「止损改成 3%」「加成交量过滤」 |

模型统一用 **`deepseek-v4-flash` + `thinking={"type":"disabled"}`**（与 Pine 转写同源，
实测 4.6s/792 token；理由：改策略产出的代码要能被同一条转写链路读懂）。

### 4.2 链路（沿用「队列 + 宿主 worker + 沙箱」）

```
前端 POST /api/market-lab/library/{id}/chat  {message, mode}
   ↓ 原子写 data/pine_library/chat/queue/<job_id>.json
     {job_id, id, message, mode, symbol, requested}
宿主 scripts/pine_chat_worker.py（cron 每分钟 + flock，独立锁文件）
   1. 读上下文：策略源码 + 现有备注 + 最近 report.json + thread.jsonl 近 N 轮
   2. 调 _chat(messages, model, think=False)（多轮）
   3. mode=ask  → 回复文本
      mode=edit → 新 Pine 写入 chat/<id>/<job_id>/candidate.pine
                  → 静态闸 → bwrap 沙箱回测 → 记前后指标对比
   4. 写 data/pine_library/chat/jobs/<job_id>.json {status, stage, reply, ...}
   ↓
前端轮询 GET /api/market-lab/library/{id}/chat/{job_id}（2.5s）
```

- **对话历史**：`data/pine_library/chat/<id>/thread.jsonl`，行格式与
  `log.jsonl` 兼容（`{timestamp, role, content, job_id, mode}`），前端可直接喂
  `ChatStream`。
  > 备选是复用 `/api/agents/{sig}/logs`（无签名白名单，零后端改动），但会往
  > `data/agent_data_astock/` 塞最多 1001 个目录并污染模型列表，故不采用。
- **多轮**：`_chat()` 接受消息数组，把 thread 最近 N 轮 + system prompt 拼进去即可。
- **新增 CLI 标志**：`pine_to_pyne.py` 目前只有 `--id`，读的是 `data/pine_library` 的
  生效源码。验证候选**不能覆盖正式产物**，需加 `--source <path>` 或 `--scratch <dir>`，
  让候选在 `chat/<id>/<job_id>/` 里转写+回测。

### 4.3 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/market-lab/library/{id}/chat` | 投递一条消息，返回 `{job_id}` |
| GET | `/api/market-lab/library/{id}/chat` | 读 thread（给 ChatStream） |
| GET | `/api/market-lab/library/{id}/chat/{job_id}` | 轮询任务状态/回复/对比 |
| POST | `…/chat/{job_id}/apply` | 采用候选：版本快照 → 写 `edited/<id>.pine` |
| POST | `…/chat/{job_id}/revert` | 回滚到上一版 |

### 4.4 「随时调整」的落地

- **版本快照**：每次「采用」先把当前生效源码存到
  `data/pine_library/versions/<id>/<ts>.pine`，再写 `edited/<id>.pine`
  （沿用现有优先级：edited > recrawl > desktop，不动桌面语料）。
- **采用后自动入队**转写+回测（复用现有队列），页面继续轮询。
- 编辑器里手改源码的路径（`PUT /api/market-lab/library/{id}`）保持不变，
  agent 只是多了一条产改动的入口。

### 4.5 安全边界（照抄现有约束，不新开旁路）

1. 模型产出的 Pine 永远不直接执行，只在 `sandbox_cmd()` 里跑。
2. 候选写进 `edited/` 必须用户显式点「采用」；未采用前只存在于 `chat/<id>/<job_id>/`。
3. 每次采用留版本快照，可回滚。
4. 静态闸复用 `pine_to_pyne.py`，不另开校验旁路。
5. 对话 worker 与转写 worker **各自独立锁**，互不阻塞；两者都只跑单实例。

---

## 5. 分期与验收

| 期 | 内容 | 落点 | 验收 |
|---|---|---|---|
| **P0** | 去 tab 化三栏布局；库策略接入中栏回测；成交点标到 K线 | `MarketLab.tsx`、拆 `StrategyLibrary.tsx`、`SingleBacktest.tsx` 加 props、`MarketLab.css` | 左栏点一条已转写策略 → 中栏看到完整 K线（带买卖点）/净值/逐笔，与模板回测同款 |
| **P1** | 备注：存储 + 3 个端点 + 右栏编辑 + 左栏角标 | `pine_library.py`、`api_server.py`、新增 `NotePanel.tsx` | 写完备注刷新仍在；索引重建后备注不丢 |
| **P2** | 对话 `ask` + 一键写入备注 | 新增 `pine_chat_worker.py`、`AgentChatPanel.tsx`、cron 一行 | 页面发问 → ≤1 分钟出答 → 可写入备注 |
| **P3** | 对话 `edit`：候选 → 沙箱回测 → 前后对比 → 采用/回滚 | `pine_to_pyne.py` 加 `--source/--out`、`versions/` | 让 agent 改一个参数 → 看到前后指标对比 → 采用后编辑器源码已变 → 可回滚 |

**分期顺序的理由**：P0 是纯前端、无新后端依赖，先把「看到」打通；P1 是 P2/P3 的
上下文基础（agent 回答要能沉淀）；P2 先做只读问答，把队列+worker+轮询这套链路跑顺，
P3 才引入「产代码」这个高风险动作。

---

## 6. 风险与待定

1. **`--source/--scratch` 是新开关**，要确保它不会让候选绕过静态闸——沙箱调用点
   必须与正式链路同一个 `sandbox_cmd()`。
2. **报告字段陷阱**：`report.trades` 是数量不是明细，适配器写错会让逐笔表渲染成
   一个数字。P0 加单测。
3. **对话 worker 的模型调用**要走 `_chat()` 而非 `call_llm()`（后者只吃两条消息，
   无法多轮）。
4. **待定**：
   - 右栏默认显示「备注」还是「对话」？
   - 改策略的对比粒度：只给指标对比，还是净值曲线叠加？
   - `rank`/`screener` 是否收进抽屉（默认收）。
