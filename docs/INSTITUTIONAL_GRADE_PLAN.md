# 机构级补齐规划（2026-09-18）

> 本文件回答一个问题：以机构级标准衡量，BayMax 的智能体交易链路还缺什么、
> 先做什么、怎么验证。**机构级 ≠ 更多功能**——是每一层的确定性、可观测与可
> 验证：信号可信、约束硬编码在代码里、失效姿态明确（fail-safe 方向正确）、
> 每个动作有对账、每个故障有暴露面。
>
> 配套：docs/AGENT_ROADMAP.md（能力演进）、docs/NEXT_WORK_PLAN.md（工程待办）。
> 本文件是**风控与执行层**的机构化差距清单与实施纪律。

---

## 0 · 落地记录与验证（2026-09-18 夜）

四次实施均走 测试先行（RED→GREEN）+ 事故重放 + 全量回归；落地后经**双评审
（通用 + Python 专项）**，全部 findings（H×1 / M×4 / L×10 余）已修复并复验：

| 项 | 验证方式 | 结果 |
|---|---|---|
| W1 新闻新鲜度重构 + 主编游标修复 | 用**今天真实 state.json** 重放事故时刻：把游标还原为事故时的值（09-17 19:05）、now=09-18 10:45 | ✅ 报出"新闻管线停更 940 分钟（最近一次完整成刊 09-17 19:05）"——正是今早漏掉的那条；健康态（19:05 成刊后）判为新鲜不报 |
| W2 风险预算 fail-safe | 单测：文件缺失/损坏/过期/结构异常/缺键各形态；真实档位文件端到端重跑定档 | ✅ 过期=买入侧回退+**杠杆键取文件原值**（评审 H-1：初版让杠杆上限回落 1.5 是"故障放宽"，已修）；当日文件已重生成含 5 键 |
| W3 单票集中度闸 | 纯函数边界/NaN/Inf/溢出的 fail-open 测试；**两条执行路径各自的集成测试**（mock 桥：超限拦截且人可见、限内放行、同轮重复决策不能叠加越限）| ✅；当前实盘最大单票 12.1% → 25% 上限**零扰动** |
| W4 熔断告警 | 单测 + alert.sh 新片段隔离演练（去重/解析/北京时间文件名） | ✅；`bash -n` 语法通过 |
| 全量回归 | `pytest tests/` | ✅ 1574 passed / 6 failed——6 个失败与仓库已知数据态基线**逐一对应**（非回归）；顺带修复一个在 HEAD 上已挂的"定时炸弹测试"（test_trade_recap_intent_note，测试数据钉死日期）|

评审轮要点（详见代码注释）：M-1 主编停更归因（读 last_chief_fail，文案指向主编段
而非主机）；M-2 延期单去重键去小时数（原每 6 分钟重复告警）；M-3 同轮重复决策经
`round_added` 累计防绕闸；M-4 补 09:35 路径集成测试（tests/test_llm_trade_cap_gate.py）；
L-5 修账户协议"虚拟现金恰好归零被读成 10 万"的潜在资金洞（US 金标按修复后重算，
live/hk 金标未动=非退化路径逐字节兼容）。

---

## 0 · 机构参照系（五层）与现状对照

机构交易栈的每一层都有"错了会怎样"的兜底。逐层对照（证据来自 2026-09-18
四路代码调查 + 当日线上事故实录）：

| 层 | 机构标准 | BayMax 现状 | 差距 |
|---|---|---|---|
| **信号** | 因子/模型分有版本、有门限、过期即失效 | 模型分（fusion_score）仅经每日 picks.json 进候选池；L2 signal_score 只展示；无阈值闸门 | 分数无门限、盘中不更新 |
| **组合构建** | 单票/行业/总仓位上限，风险预算可解释可审计 | 单笔 ≤ 剩余额度 15-20%、新开仓 ≤2-3 只、杠杆 ≤1.2×；**无单票累计上限、无行业上限** | 加仓可跨轮累积不封顶；`risk_amount` 字段解析了无消费方 |
| **交易前风控** | 所有限制 fail-safe；数据故障 → 收紧而非放松 | 16 道闸门多在，但**风险预算文件故障 → 按更松默认跑**（fail-open） | 失效姿态方向错误 |
| **执行** | 限价口径唯一、未成交有撤/追纪律、成交回报对账 | 买 ×1.01/卖 ×0.99/哨兵跌停保护价；wait_fill→pending→reconcile；**无撤单/追价**；**美/港无回报链** | 卖单口径两条不一致；未成交干等到日终 |
| **交易后/治理** | 熔断必上告警面；归因与记分常态运行 | 记分卡/回合台账/复盘已有；**熔断只落盘不推送** | 熔断无声 |

---

## 1 · 本回合落地（2026-09-18 夜，均已带测试）

### P1 · 09:35 主入口上下文对齐 —— **阶段 1（影子 A/B）已上线运行**

**证据**：09:35 是每天第一笔真实调仓的入口，却是信息最贫瘠的一条链（无新闻分子/
盘面状态/L2/复盘回喂）；整点轮对同一批 agent 有完整上下文。上下文不对称此前只是
历史偶然，不是设计。

**机构级实施纪律**（本批 2026-09-18 落地）：

1. **实验设计含对照臂**：temperature=0.3 下同一提示词重跑本就有差异——影子每天
   同一输入快照跑三臂：A1/A2（现状提示词 × 2 = **采样噪声基线**）、B（现状 +
   盘面状态 + 新闻分子）。判据 = effect(ΔA1B) 是否稳定高于 noise(ΔA1A2)，
   **而不是"A/B 有差异就上线"**。
2. **零实盘面**：影子只记录（`logs/shadow_context/{date}.jsonl`），不下单、
   不写执行状态/对话流/熔断；独立 flock（不影响主入口、也不被主入口挡住）。
3. **提示词唯一出处**：主入口与影子共用 `_agent_context()`（本批把 _run 内联
   拼装抽出，防两处漂移；重构逐行比对确认行为等价，空 extra_context 的输出有
   HEAD 逐字节金标钉住）。
4. **调度与读out**：cron 北京 10:02-10:57 **每 5 分钟重试档**——影子启动先探
   主入口锁（09-18 实录主入口持锁到 10:21，固定时刻会与真实下单轮抢
   LLM/AiData 配额），主入口没跑完就跳过等下一档；当日已采过则幂等跳过。
   周日 20:00 周报（`shadow_context_report.py --days 7 --out … --push`，
   先落盘再推一行 QQ；落盘失败 rc≠0）。
5. **灰度判据**：effect 与 noise 对照，**失败臂不参与判定**（臂 fail≠"" 的样本
   被排除——否则会把纯 API 失败折算成"决策差异"、方向恰是支持上线；B 臂
   提示词更长、失败率天然更高）；块缺失日单独计数；n<10 个判定日只观察。
   effect 稳定高于 noise 且方向合理 → 把 `extra_context` 接进主入口（一行改动）。

**双评审（2026-09-18）**：无 CRITICAL；H×2（失败臂假 effect / 09:40 并发抢配额）、
M×4（meta 死字段→上报告面 / 同日多行灌水→采样幂等+报告去重 / pct 三态丢失 /
落盘失败 rc）+ L 若干，全部修复并加测试（23 项）。

**产物**：`live_llm_trade.py --shadow-context`（新模式）/ `scripts/shadow_context_report.py`
（周报）/ `tests/test_shadow_context_ab.py` + `tests/test_shadow_context_report.py`
（14 项）/ crontab 两行（备份 /tmp/crontab_backup_20260918.txt）。

---

### P2 · 模型分数门限 —— **已度量：结论是不建闸**（2026-09-18）

P2 的第一步是"先证伪"（度量低分买入的远期超额，有正差再设闸）。度量做完，
**前提不成立，故不建闸**；本轮改为补观测面，让这个判断在将来能被证实或证伪。

四条证据（可复现）：

1. **没有可比口径**。三种池文件的 score 量纲互不相同：quantmind 盘后池
   0.66~0.78、晚间研究池 24~33、个别日 48~1050（同一文件内 CV 仅 0.02~0.09）。
   写进 `risk_budget.json` 的单一阈值不可能对三者同时正确。
2. **门槛字段多数日子不存在**。`fusion` 只在 quantmind 盘后池有；近期实际消费的
   是晚间研究池（09-14/16/17/18），提示词那一列直接是"—"——按字面实现的
   "fusion_score 门限"在这些日子是 no-op（不报错，也不生效）。
3. **fusion 本身没有区分度**。全池 0.005~0.017、同日 CV≈0.03；两个模型在实盘
   输出里各自独立写道"融合分都在 0.006 左右，基本上没有明显差异"、
   "融合分普遍仅 0.014"。门限切的是噪声。
4. **"低分买入"多半是资金约束的产物**。09-17 实录：20 只池剔除 11 只买不起的，
   含 rank 1/2/3/4/6/8——高分票因贵先出局，模型只能从池尾与池外选。
   把这条读成"模型无视分数"，方向正好相反。
5. **样本根本不存在**。`decision_pool` 此前不记"决策时刻该票在池中的排名/分数"，
   且 **09:35 主入口整条路径不进记分卡**（每天第一笔真实调仓）。69 条 bullish
   决策里能对上可用分数的只有 09-14 一天，t20/t60 覆盖为 0。

**本轮落地的是观测面（零行为变更，只写日志）**：

| 改动 | 内容 |
|---|---|
| `decision_track.pool_stamp()` | 纯函数：候选池 → 位置戳，**态与名次分开记**——`state=shown`（模型看到）／`state=dropped`（池内但被资金闸剔除）／`state=off`（池外）；`rank/score/fusion` 另存。只按 rank 反推会在池行缺 rank（md 兜底分支）时把"被剔除"读成"池外"，方向正好相反（09-18 审查 MEDIUM） |
| `decision_track.ingest_decisions(pool_view=…)` | 记录增 `pool_ctx` 字段；**整轮不传（含空 dict）= 不写该键**（旧记录与回填路径不受影响）；戳表内容坏 → 按"池外"降级，`pool_stamp` 抛异常 → 整批不写戳（计"未插桩"，不谎报覆盖率）。两条降级路径都不带走整批入库 |
| `live_llm_trade`（09:35） | **新增接入记分卡**——此前每天第一笔调仓完全不在样本里；轮内时间基准取一次传到底 |
| `live_hourly_analysis` | 原有调用点补 `pool_view` |
| `scripts/pool_score_report.py` | 只读读数面：按「分组 × rank 档位」看 T+1/T+5 超额，档位按排名序渲染，样本不足时明确拒绝结论；NaN/脏值/池文件缺失各自单列 |
| 接线漂移防线 | `tests/test_pool_ctx_ingest.py::test_every_ingest_call_site_passes_pool_view`——ast 扫全仓出货代码，新增调用点漏传 `pool_view` 直接红灯（整点轮全路径无测试脚手架，故用静态检查兜底） |
| **生产数据隔离（事故修复）** | 09-18 审查 CRITICAL：`decision_track.POOL` 不在 `tests/conftest.py` 隔离清单里 → 接入记分卡后**跑一次 pytest 就往真实 `logs/decision_pool.jsonl` 追加 fixture 决策**（实测 138→141 行，两位审查独立复现）。伪造行会带真收益回填进记分卡与 P2 报告，且 `make_id` 幂等（重跑不再加行）——越跑越难察觉。修法：conftest 加 `decision_track: (POOL, SCORECARD)`，清理已污染 3 行，`test_production_isolation` 增动态回归钉（跑一轮 ingest，生产池字节不变） |

**读out**：`python scripts/pool_score_report.py --days 30`
（当前如实输出"带池位置戳 0 条 / 未插桩 69 条 / 样本不足"——这正是本轮的结论形态）。
脚本真跑起来之后才开始积累带戳样本；`state` 字段 2026-09-18 起写入，此前的戳
（数量为 0）按 rank 反推的兼容读法处理。

**什么时候再谈设闸**（缺一不可）：选择集内两组各 ≥30 样本、方向跨周稳定、
幅度大于该 agent 日常波动，**且闸门能区分池种类**（量纲问题见证据 1）。
在此之前，任何"按 fusion 设阈值"的改动都是给噪声立规矩。

---

### P3 · 影子代价账 —— **已接线，每条闸门从此记账**（2026-09-19）

问题：风控闸门只留下"拦掉了什么"的痕迹（stdout 里一句中文），没有任何数据能回答
**"拦对了吗"**。日复一日加闸 = 单向棘轮：每条规则都有存在的理由，但没有一条会因
**代价大于收益**而下线。影子账给每条规则记一本账：当时若放行，后来是涨是跌。

**本期落地（记录层 + 读数面 + 接线 + 生产回测，全部带测试）**：

| 改动 | 内容 |
|---|---|
| `scripts/gate_rules.py` | 规则的**稳定 id 词表**（21 条，`<域>.<规则>`）。此前否决原因只以中文句子存在，拿句子当键的三个必然结局：改一个字换一条规则、同规则两种说法、报告靠正则猜 |
| `configs/gate_registry.json` | 每条规则的**依据 / 失效条件 / 复核日**（`evidence`/`falsify`/`review_by`，默认一个月后到期）。规则默认会过期，不默认正确 |
| `scripts/ghost_ledger.py` | 唯一写入层。schema 与决策池一致（+`rule`/`kind`/`ref_px`），故 `decision_track.update_forward(path=GHOST)` 直接可用——**远期收益口径不存在第二套实现**；幂等键含 rule（同日同票被两条规则拦是两件事）；写入原语共用 `append_rows`；任何异常吞掉只留一行 stderr（观测层绝不反噬交易链路） |
| 两条买入路径接线 | `live_llm_trade`（09:35，13 处）与 `live_hourly_analysis`（整点轮，12 处）共 25 个否决点各接一行 `ghost_ledger.veto(...)`。**unseen**（候选被资金剔除、模型根本没看见）与 **veto**（模型想买被拦）分开记——读法相反，且 unseen 是弱反事实 |
| 演练轮标记 | `source` 带 `.dry`（`--execute` 关闭 / `dry_run`）：演练轮的钱本来就不会动，"被拦下的意图"没有机会成本，但"闸门今天动过"仍是有效观测——不藏不混 |
| `scripts/ghost_backfill_logs.py` | **历史回填**（只读日志；默认预览，`--write` 才落盘）：解析 `💸` 剔除行（两种日志格式）+ 决策池×成交流水的"意图无落地"差集（**只认委托号**；成交流水缺失的日子**不可比**，单独计数，不算未落地） |
| `scripts/ghost_report.py` | 读数面：按规则/域聚合；**事件 ≠ 样本**（同票同日多 agent = 1 个样本，独立单位是 日×标的×规则）；样本 <30 只展示不结论；参考线 = 决策池同期看多基线（**拦掉的比基线差**才算省钱）；未到期单独计数；未登记规则与复核过期显式列出；`--out` 原子写 |
| 接线漂移防线 | `tests/test_ghost_wiring.py`：ast 扫全仓——有闸门调用却没有影子账记录的函数直接红灯；`veto` 的规则参数不许写字面量；词表里的规则必须有人发得出来 |
| 生产数据隔离 | conftest 加 `ghost_ledger: (GHOST,)`；`test_production_isolation` 增动态回归钉（真记一条，生产影子账字节不变）。`REGISTRY` 刻意**不**重定向：它是 `configs/` 下只读表，重定向会让"登记表覆盖所有规则"的防线对着空表跑出假绿 |

**生产回测（首次读数，2026-09-19，数据来自 09-14/09-17 的真实日志）**：

```
事件 37 条 → 独立样本 13 个（窗口 2026-09-14 ~ 09-17）
budget.unaffordable   unseen   36 事件 / 12 样本   t1 超额 -0.86%（中位 -1.29%，胜率 33%）
unknown.no_execution  unknown   1 事件 /  1 样本   t1 超额 -7.27%
参考线（决策池同期看多基线）            16 样本   t1 超额 +0.39%
期限覆盖：t1 13/13 · t5 0/13（未到期）
```

如实读法：**样本 12 < 30，不做结论**。方向上像是"资金剔除省了钱"（被剔的高价票跑输
模型自己的看多基线），但这是两天 12 个独立样本，且 unseen 是弱反事实（"假设模型会
选它"本身没有证据）。

三条**结构性发现**（比数字更重要，决定这套设施怎么用）：
1. **历史 veto 数据几乎为零**：09-01~09-18 两条日志里没有任何一条买入否决行
   （执行段很少被走到：桥断线/让位/冷却；09:35 路径此前不进任何池）→ 影子账的价值
   在**从今天起往前累积**，回填只拿得到 unseen。
2. **整点轮 09-09~09-18 的 95 条决策里没有一条 buy**（86 watch / 9 sell）——不是
   "被闸门拦了"，是那段时间模型确实没提买入。两条读法差一个世界。
3. **改文案 = 断历史样本**：这正是 rule id 与中文 reason 分离的原因。

**读数命令**：`python scripts/ghost_report.py --out logs/ghost_scorecard.json`。
判定一条规则是否该退：样本 ≥30 且正超额稳定 → 这条闸在花钱；但**先回到问题本身**
（09-17 剔除名单含 rank 1/2/3/4/6/8 的高分票，"买不起"是资金规模问题，不是规则写错了）。

**夜间滚动（待启用，需人工确认后 `crontab -e` 加一行；已按 cron 最小环境实测 rc=0）**：

```
12 1 * * 2-6 /home/zbox/baymax/.venv/bin/python /home/zbox/baymax/scripts/ghost_ledger.py --update >> /home/zbox/baymax/logs/ghost_ledger.log 2>&1 && /home/zbox/baymax/.venv/bin/python /home/zbox/baymax/scripts/ghost_report.py --out /home/zbox/baymax/logs/ghost_scorecard.json >> /home/zbox/baymax/logs/ghost_ledger.log 2>&1 && /home/zbox/baymax/.venv/bin/python /home/zbox/baymax/scripts/ghost_ledger.py --check >> /home/zbox/baymax/logs/ghost_ledger.log 2>&1  # P3 影子代价账 北京00:12（decision_track 00:10 之后；远期回填→记分卡→登记表健康检查）
```

不加这行影子账也不会坏：它只是被动记（写入在两条买入路径里）；这行负责**把记下的东西
变成每天更新的读数**（远期收益到期回填 + 记分卡落盘 + 复核过期检查）。
`--check` 不在 cron 里也建议每周手跑一次——复核过期的规则没人看见就等于没有复核日。

---

### TCA · 执行损耗账 —— **已接线，下单这件事从此有账**（2026-09-19）

问题：仓库此前只能回答"模型**选得对不对**"（`decision_track` 明确写着不衡量执行
滑点），回答不了**"下单这件事花了多少钱"**。同一段行情，报单方式、缓冲宽度、
轮询时机的差别全是真金白银，但没有一本账。

**口径（唯一出处 `scripts/exec_cost.py`）**：一笔委托读三个价——
`ref_px`（决策基准：下单时取价行情的 close）→ `limit_px`（报出去的限价：
买 = 基准×1.01/港 1.005，卖 = 基准×0.99）→ `fill_px`（桥回报成交价）。
**符号约定：两侧的 `slip_bps` 正号都表示"比基准差"**；`cushion_used_bps`
0 = 按基准成交、100 = 成交在限价上（把缓冲吃满）、负 = 优于基准。缺项/脏值一律
`None`（"不可定价"），**绝不吐 0 bps 冒充"执行完美"**——fail-open 观测层纪律。

**本期落地**：

| 改动 | 内容 |
|---|---|
| `scripts/exec_cost.py` | 口径唯一出处：`slip_bps` / `cushion_used_bps` / `tape_fields`（写进水里的字段集）/ `normalize_row`（行→样本，含**提交回执排除**）/ `summarize`（成交额加权 + 稳健分位 + 成交率分母含零成交终态） |
| 4 个执行写入点接线 | 09:35 与整点轮 × 买卖两侧（`live_llm_trade` 2 处 + `live_hourly_analysis` 2 处）每行成交带三段价 + 三个时刻（`decided_ts` 轮级 / `submit_ts` 报单 / `fill_ts` 观察到成交） |
| 对账补记接线 | `reconcile` 的 `fill_confirm` 行同样带全字段；三段价里 ref/limit/decided 由**下单时的 pending 条目**带过跨进程窗口（`add_pending` 新增 `ref_px`/`decided_ts`，老条目缺项 → 如实不可定价） |
| `settle_place_fill` 语义修正 | `price` 参数 = **委托限价（两侧同义）**。历史漂移：买入侧传的是基准价，而该参数进（a）pending 在途价→reconcile 回退记账价、（b）「剩余未成交（限价 ¥X）」告警文案、（c）`fill_abort` 流水——三条全错且无症状 |
| `live_fills.sell_limit` | 卖价缓冲 0.99 此前在两个文件各写一遍 → 收口（对照 2026-09-13 买入缓冲那次漂移） |
| `scripts/tca_report.py` | 读数面：按 路径×方向 分组、成交额加权、成交率、决策→提交延迟；**样本 <30 只展示不结论**；四类已知边界原样印出。`--infer-ref`（默认关）从历史买入限价反推基准（±1~2bp），推理样本单列 `.inf` 组 |

**修改过程中由生产数据挖出的 4 个隐藏问题**（都不是本次引入的）：
1. `settle_place_fill` 的限价语义在买入侧是错的（见上表第 4 行）。
2. **提交回执行会被读成成交**：执行路径提交时刻写一行 `{mode: execute, price: 限价,
   volume: 委托量, result: {...}}`，与成交行长得几乎一样——读成成交就是"按我们自己
   报的限价成交"的幽灵样本（滑点恒等于 +1% 缓冲）。已按行的固有字段排除并单列计数
   （历史 17 行）。
3. `add_pending` 的 `reason`（2026-09-18 通知口径统一）**从未被 `settle_place_fill`
   转发** → 四条无人值守路径的对账迟补成交推送一直没有理由。已补。
4. 卖价缓冲两处内联（见上表第 5 行）。

**生产回测（首读数，2026-09-19；09-19 接线前的全部历史，`--infer-ref`）**：

```
窗口 2026-08-31 ~ 09-18 · 成交行 49 · 合并后委托 43 笔 · 可定价 10 · 不可定价 33
非成交行(回执/在途) 17 · 限价从日志补回 10 笔 · 零成交终态 2 · 成交率 95.6%（43/45）

  llm_trade.inf/buy     n=9   加权 -4.33 bps   中位 +0.64   p10 -22.23   p90 +4.93
  reconcile.inf/buy     n=1   加权 +6.43 bps
  合计                  加权 -3.13 bps · 中位 +1.19 bps · 成交额 ¥86,543
```

如实读法：**n=9 < 30，不做结论**；且 10 笔全是**买侧**（卖侧限价历史未落盘，卖单
滑点不可重建——这正是接线要解决的），基准价是**推理值**。量级信息两条：加权为负
= 限价买单总体成交在基准（K 线收盘）下方；但 p10 ≈ −22 bps 说明有一批单吃到了明显
更差的价，而 p90 ≈ +5 bps 说明也有贴着限价成交的。"限价单在回落中成交"是候选解释，
**真读数要等接线后的实测样本**。

**已知边界（报告里原样印出，不藏进文档）**：①手续费/印花税**不在账**（桥只回报价量，
账本无费用字段）；②`fill_ts` 是轮询**观察到**成交的时刻（3s 粒度），不是交易所成交
时刻；③历史卖单限价未落盘；④`ref_px` 是取价时刻行情，`decided_ts` 是轮级时刻——
"模型决策→取价"那一段延迟不在本读数内。

**防线三层**：
- 静态：`tests/test_tca_wiring.py`（AST——有 `"fill"` 键的成交行必须展开
  `tape_fields` 且参数齐全；`fill_confirm` 同理；`settle_place_fill` 的价格参数
  买入必须传限价；0.99 不许再内联）——漏接一个写入点运行时**没有任何症状**，
  只能靠静态钉；
- 单元：`tests/test_exec_cost.py`（符号约定为核心契约）+ `tests/test_tca_report.py`
  （去重/合并/推理基准单列/提交回执排除）；
- 生产形态：`tests/test_hourly_exec_settle.py` 用假桥真跑 `execute_intraday_decision`，
  断言写出的行值与口径一致；`tests/test_fill_remainder.py` 走完
  下单→挂队→对账补记→报告可定价的完整生命周期。

**读数命令**：
```bash
.venv/bin/python scripts/tca_report.py --days 30 --out logs/tca_scorecard.json
.venv/bin/python scripts/tca_report.py --infer-ref      # 历史重建（推理值，.inf 组）
.venv/bin/python scripts/tca_report.py --push           # 摘要推 QQ
```

**夜间滚动（待启用，需人工 `crontab -e`；行已按 cron 最小环境 `env -i` 实测 rc=0，scorecard/日志落盘正常，见 2026-09-19 记录）**：
```
20 1 * * 2-6 /home/zbox/baymax/.venv/bin/python /home/zbox/baymax/scripts/tca_report.py --days 90 --out /home/zbox/baymax/logs/tca_scorecard.json >> /home/zbox/baymax/logs/tca_report.log 2>&1  # TCA 记分卡 北京00:20（ghost 00:12 之后，只读不写状态）
```
不加也不影响交易：报告是纯只读（与影子账同姿态），接线本身被动记录。

---

### W1 · 情报链可用性（对应：今日事故）

**证据（2026-09-18 实录）**：主机冻结打掉 09:25/09:55 两轮新闻；10:21 空窗轮
假称"无新增"；白天交易 agent 引用前一日 19:05 分子（>14h）；**上午告警窗口内
零告警**，下午才报（且无去重，45 分钟刷了 11 条）。根因三处：
① 判定源用 `latest.json` 的 mtime（任何写动作可刷新鲜，且空窗轮的行为不受控）；
② `pgrep` 到进程在跑就豁免——卡死的进程反而挡住告警；
③ 该检查无节假日闸、无去重。

**设计**（三处修复，全部收敛到 `alert_checks.py` 纯函数 + `alert.sh` 接线）：

1. **新鲜度判定改用成功游标**：`state.json` 的 `last_end` 只在"跑满五段且无桶
   失败"时推进（2026-09-18 已加固的语义），比 mtime 可靠。判据二：分子内容
   新鲜度 = `max(last_chief_ok, last_empty)`——主编持续截断失败会停更该标记
   （空窗是合法形态、计入）。
2. **进程豁免带过期**：alert.sh 只把"存活 ≤30 分钟"的 news_brief 进程视为
   在跑；超时未退 = 卡死，不再豁免（正常单轮 5-15 分钟）。
3. **去重 + 节假日闸**：告警按 （类型, 小时） 桶去重（此前 45 分钟 11 条）；
   判定走 `is_trading_day`（此前法定假日会误报）。

**连带修复**：`news_brief.state_after_run` 的游标推进此前**不检查主编是否产出**
——主编截断失败轮也会推进游标 → 该窗口新闻永久丢失且 `last_end` 假装健康。
改为：主编失败（chief_ok=False）不推进（与其他失败同方向：宁可重复处理，不可
静默丢失；窗口上限 400 条已封顶重复代价）。

**验收**：单测重放今日时序（10:45 读到昨日 19:05 游标 → 必须报，含"停更 N
分钟 + 最近成刊时间"）；主编失败轮游标不动（test_news_brief 新增用例）。

### W2 · 风控失效姿态：风险预算 fail-safe

**证据**：`load_limits` 在文件缺失/损坏/过期时返回 `{}`，调用方保持各自默认
（1.5×/20%，**比预算档松**）——"风控读不到就放宽"方向错误（与 `decide_level`
2026-09-08 已修的 fail-safe 不一致）。今日 09:00 即实录一次"档位日期早于应定档
日，照常 fail-open"。

**设计**：新增 `FALLBACK_LIMITS`（买入侧防守：单票 10% / 新开仓 1 只 / 单票
持仓上限 15%）。文件不可信（缺失/损坏/过期/结构异常）→ 返回该回退 + 告警
（事件文案说明回退内容）。**有意不含 `leverage_max` / `leverage_trim_to`**：
强减是风险动作，数据故障不应触发强平——fail-safe 的方向是"限制新增风险"，
不是"制造新动作"。

**验收**：原"fail-open 返回 {}"系列测试改写为新契约；断言回退**不含**杠杆键
（防将来有人顺手加回去）。

### W3 · 组合层约束第一件：单票集中度上限

**证据**：`per_stock_pct` 是"单笔占**剩余额度**"比例，同一标的跨轮加仓不封顶
（只有杠杆闸兜底，且杠杆闸在 `virtual_cash≥0` 时几乎不 binding）。当前实盘
单票最大 12.1%（无紧近压力，上线零扰动）。

**设计**：新档位键 `per_stock_pos_pct`（平静 30% / 谨慎 25% / 防守 15%），
判定 = 同票已有敞口 + 本单成本 ≤ 上限 × 权益：
- 纯函数 `buy_gate.position_cap_reason`（口径由调用方保证同基）；
- 两个执行入口（09:35 `live_llm_trade` 成本口径、整点轮 `live_hourly_analysis`
  市值口径，各自与自己路径的权益口径一致）在资金三连闸处调用；
- 旧档位文件无该键 → 调用方默认 25%（档位 schema 向前兼容，次日 09:10 定档
  自动写入新键）。

**验收**：纯函数单测（边界/脏输入/equity≤0 放行）；整点轮执行路径集成测试
（超限加仓被拦、同额新开仓不受影响）；09:35 路径同式接线经代码评审确认。

### W4 · 熔断上告警面

**证据**：`live_breaker` 触发只落 `logs/breaker/{day}.json`，alert.sh 无接线
——当日该 agent 禁止新开仓，而人不知道。

**设计**：`alert_checks.breaker_lines` 读当日熔断文件 → alert.sh 按 agent 去重
推送（熔断是当日状态，只打扰一次），文案给原因与时刻。

**验收**：单测（正常/坏文件/CLI）；alert.sh 接线评审。

### W5 · 告警面的时区与生命周期（2026-09-20）

**证据（周末实录）**：周五（09-18）两条止损卖单被柜台判废、一条条件位重复卡死，
三个事件在**周六/周日北京 23:00 各重播一次**（`/tmp/.baymax_*_seen` 三个日期文件
与 alerts.log 3 个块头为证）。两个根因：
① alert.sh 的按日去重键是裸 `date +%F`——本机 JST，北京 23:00（JST 午夜）就翻日，
未解决事件被当成"新的一天"重新公告；
② 事件一旦写上没有任何"已解决"出口：写侧 24h 惰性清理只在**下一次写入**时生效，
周末没有写入 → 事件永远停在告警面上。

**设计**：

1. **时间口径**：alert.sh 顶部定义 `BJ_DATE/BJ_HOUR`（`TZ=Asia/Shanghai`），
   11 处按日去重键 + 新闻按小时键 + alpha 滞后星期闸 + 块头时间戳全部北京；
   breaker 块本来就是这个口径，改为复用 `BJ_DATE`。
2. **读侧唯一口径 = `scripts/order_events.py`**（纯标准库，alert.sh 的系统 python3
   可直接跑）：保留窗口与写侧**同一常量**（`EVENT_KEEP_H`，写侧 `prune_doc` 也
   从这里 import）；一条事件不再告警的四种理由——人工 ack（resolved_* 落盘 +
   `live_order_events_ack.jsonl` 只增不删审计尾巴）、留痕（alert=False）、
   **卖域自动消解**（unfilled(side=sell)/sell_failed/dup_unresolved/refire 且账本
   任一 agent 都不再持有该 code；持仓还在就继续告警）、过期（24h）。
   `no_order_id/outcome_unknown` **不**自动消解——那两条的前提就是"账本不可信
   （成交未记账）"，不能反过来拿账本推断已解决；账本缺档一律禁用自动消解。
3. **只读红线**：告警路径对数据文件只读；唯一写入口是 ack CLI，与
   `record_event` 共用同一把 flock（`data/live_reconcile.lock`）。
4. **三处时间戳标注修复**：`live_account_cache.ts_cn` / `tdx_bridge` 内联快照 /
   `agent_commands.executed_ts` 原本是 `time.strftime(...+08:00)`——取机器本地
   时间（JST）却标 +08:00，比真实北京时间快 1 小时；改为显式北京时间。

**验收**：`tests/test_order_events.py`（24 例：窗口边界/不可解析 ts 保守保留/
ack 幂等与审计/自动消解各分支/账本不可用保守全报/锁文件与 live_fills 同源/
纯读不写文件/CLI）；`tests/test_alert_checks.py` 增补（过期-签收-自动消解排除、
CLI 传账本 argv）；`tests/test_alert_time_wiring.py`（alert.sh 无裸本地时间键的
静态钉 + 两个写入点的行为测试：回拨 1 小时会被抓住）。生产复验：
系统 python3 跑 `order_events` 检查与 `list` 审计视图，三个 09-18 事件如实显示
"已过期"、不再告警；alert.sh 整跑 rc=0 无新告警。

**已知边界（如实记录）**：事件记录本身随 24h 窗口被清理（审计靠 ack.jsonl）；
"持仓还在但事件已过期"的裸奔场景由持仓/条件位自身承载，不在本告警面。

---

### W6 · 磁盘满事故与告警面加固（2026-09-21）

**证据（盘满实录）**：北京约 14:39 起心跳写入开始失败，约 15:43-17:44 journal 里
rsyslogd 刷出 **266 万行** `No space left on device`（窗口内 journal 共 380 万行），
17:44 后恢复。下游症状全在**告警面自身**炸开：

1. **误报主机离线**：心跳（原子 tmp+replace）写不进去 → gap 虚涨，磁盘腾出后
   17:45 推「交易主机曾离线约 **177 分钟**」——journal 显示主机全程活着，是心跳
   自己没写下去；14:45 那条「离线约 6 分钟」同源。
2. **死轮无限重报**：14:45 尾盘决策轮撞 ENOSPC 中断——`record_equity` 写不进去，
   finally 里的 failed 心跳（tmp+replace）同样失败 → 状态原样停在 `running=true`
   + 死 pid 1613420。`round_stuck` 只按心跳新鲜度判定：alerts.log 每 5 分钟一条、
   QQ 每 2 小时重推（17:45/19:50/21:55…），且文案「后续 cron 轮会被锁跳过」对死
   进程是**假陈述**（flock 随进程终止由内核释放）。
3. **观测面截断**：alerts.log 缺 16:05-17:40 全部块 + 半行粘连；
   `premarket_probe_state.json` 被非原子 `write_text`（先截断后写）清成 0 字节；
   15:00-15:10 收盘净值定格采样丢失（今日净值曲线缺收盘点）。
4. **源头零告警**：以上全是下游症状——没有任何一条告警指向「磁盘将满」本身。
5. **放大器**：qm-portable-test7 测试实例的 backend.log 24G 未轮转（~100% 是
   TdxPush「TDX_BRIDGE_URL/TOKEN 未配置」无限重试刷屏，早上 3.7 小时涨 20G）。

**设计（四处加固，全部带测试）**：

1. **`scripts/round_reconcile.py`（新）+ alert.sh §2h-1**：round_stuck 判定**之前**
   运行的对账——死轮（/proc cmdline 定特征串，防 pid 复用）打印一次性告警并
   显式收尾成 failed（保留 pid/start_ts）；活进程不动（真卡死仍由 round_stuck 报，
   此时「锁被占用」才是事实）；状态文件坏/0 字节 → 隔离 `.corrupt.<ts>` + 重置。
   幂等：收尾后下一轮自然静默。fork→exec 窗口 cmdline 为空 → 判 unknown 保守不动。
2. **`alert_checks.disk_water`（新）+ alert.sh §2h-2b**：根分区**可写空间**
   `used/(used+free)` ≥88% 告警（剩余 GB 入文案），95% 升级措辞；usage 注入可测。
   （2026-09-22 改口径：原用 `used/total`，本机两种口径差 4.68pt=47.7G 的 ext4
   保留块；旧口径同样的 88% 阈值下真实可写余量只剩 ~65G，预警带被压缩，换分母后 ~107G。）
3. **`host_watchdog` 写失败留痕**：写失败时在**定长模板**上同长原地覆写
   `logs/host_heartbeat.writefail.json`（盘满 100% 时新建/扩展都失败，覆盖已分配
   的旧块可以成功）；gap 恢复时若留痕落在窗口内 → 口径改为「心跳写入失败」
   （标题/文案/`host_gaps.mode=writefail`），不再谎报离线；boot_id 变化优先。
4. **状态文件原子写**：`bridge_watch._save_state` / `premarket_win_probe._save_state`
   改 tmp+replace——写失败保留旧文件，绝不把好状态截成 0 字节。

**验收**：`tests/test_round_reconcile.py`（10 例：死/活/unknown/未跑/缺档/坏文件
参数化隔离/幂等/真 /proc 语义）；`tests/test_alert_checks.py` 磁盘段 8 例（阈值边界/
文案自报口径/保留块跃迁点(`_U(reserved_gb=)`)/`writable<=0` 退化/inf·nan 无判定/
CLI 无 path 参数）；`tests/test_host_watchdog.py` +5（留痕写入与重试、
窗口内降级归因、窗口外旧痕不降级、重启优先、模板定长）；`tests/test_bridge_watch.py`
+1（写失败不截断旧状态）；conftest 增补 round_reconcile 生产状态隔离。

**事实性遗留（未做/待用户决定）**：test7 实例处置（停 / 配 TDX_BRIDGE_URL / 加轮转）
——该 log 现为稀疏文件（表观 24G、实际 12K，写入从 24G 偏移续写），全量内容已
压缩留档 `backups/baymax/test7_backend_log_20260921.log.gz`；docker 42G 可回收镜像；
今日 15:00 收盘净值采样缺失（日报名义收盘价会缺一格，如实标注）。

---

## 2 · 规划中（未做，按序）

| # | 事项 | 为什么/证据 | 第一步（先证伪再建设） | 失效姿态 |
|---|---|---|---|---|
| P1 | **09:35 主入口上下文对齐**：注入新闻分子 + 盘面状态（现只有整点轮有） | 主入口是每天第一笔真实调仓，上下文却最贫瘠（无新闻/盘面/L2/复盘回喂，且无工具） | 先做 A/B：dry-run 对照一周日志（带/不带新闻分子的决策差异），再灰度 | 变更仅"多给信息"，不放松任何闸门 |
| ~~P2~~ | ~~**模型分数门限**：candidate 池 fusion_score 低于阈值禁止新开仓~~ | 当前分数只影响进池排序，LLM 可买池内任意票 | ✅ **已度量（2026-09-18）：前提不成立，不建闸**——量纲不可比/字段多数日子不存在/离散度≈噪声/"低分买入"实为资金约束；已落观测面（`pool_ctx` + 读数报告），未来可再判 | 不适用（未建） |
| P3 | **`risk_amount` 消费**：按模型自报风险额约束单笔 | 字段已解析（schema/parse 都在），零消费方——模型表达的风险意图被丢弃 | 统计该字段的填写率与分布；填写率低先修提示词 | 有值才收紧 |
| P4 | **撤单/追价纪律**：限价挂队超时未成交 → 有限次改价，而非干等日终 | `cancel_order` 已实现无人调用；今日 3 笔 rejected 全是挂队未成 | 先量化挂队单的成交等待分布（需先补记委托价进 live_order_events） | 只对卖出（止损）启用追价 |
| P5 | **卖单限价口径统一**：调仓 -1% vs 哨兵跌停价 | 快速下跌 -1% 可能挂着不成交（今日 600176/300408 实录） | 量化两类卖单的成交率差 | — |
| P6 | **美/港成交回报链**：wait_fill→pending→reconcile 未移植 | 美按现价、港按成本价记账（账实漂移风险） | HK/US 恢复交易前必须完成（依赖 docs/LEDGER_FOLLOWUP P3 重放多市场化） | — |
| P7 | **行业上限 / 总仓位上限** | 行业劣化只有提示词软提示（`industry_caution_block`），无硬约束 | 先统计当前行业分布与"行业上限 N%"的历史约束效果（回测候选池） | 与 W3 同式入档位 |
| P8 | **dsh `baymax_trade` 工具语义修正** | 该工具写模拟盘 position.jsonl，与实盘脱钩；persona 却称"下单执行"——agent 可能误以为已下单 | A/B：改 persona 措辞（明确"决策 JSON 才是下单通道"）对照决策行为 | 只改文本，不动执行 |
| P9 | **假设库来源分层**：review 来源（LLM 自述、无量化验证）与 factor_screen（有 IC/WF）在注入侧已有 source 区分，进一步在**存储**侧分离文件 | 40 条未验证假设与 58 条已验证混排一个文件 | 纯搬家，无行为变化 | — |
| P10 | **两条杠杆闸口径统一**（成本 vs 市值） | 09:35 用成本、整点轮用市值——同一约束两个口径，极端行情会分叉 | 先量化当前两口径的差（持仓浮盈亏分布） | — |
| P11 | **手工路径补闸**：`live_trade_picks.py` 的 buy/sell（--execute 手工驱动）未过 buy_gate 全链与集中度闸，只有"单票 ≤20% 资金"老逻辑 | 无人值守链路已全覆盖；手工路径由人驱动、风险低，但口径与自动链不一致（三次漂移教训的同一形态） | 把 check_buy/资金三连闸抽进该路径，或将其降级为"只允许 dry-run + 走 live_llm_trade 执行" | — |

## 3 · 明确不做（及理由）

| 项 | 理由 |
|---|---|
| 撮合/排队模型 | 成交数据里没有排队信息可标定（docs/NEXT_WORK_PLAN.md P1 结论），先补数据 |
| LLM 合成执行权（辩论 v2 的 arbiter 直接下单） | 盘中把执行权交给模型 = 把闸门搬到模型手里，风险不可控（AGENT_PHASE34 既定结论，维持） |
| ML 模型进本仓库 | 因子/模型在 quantmind，本仓库保持"消费产物"定位（架构边界） |

---

## 4 · 实施纪律（每个落地方案必须满足）

1. **测试先行**：先写/改测试到失败（RED），再实现（GREEN）；改动契约的测试
   必须写明"为什么改契约"。
2. **失效姿态显式**：每个判定函数在 docstring 写清 fail-open 还是 fail-safe
   及理由；风控类一律 fail-safe（收紧方向）。
3. **唯一出处**：同一规则不得在两个文件里各写一份（本仓库三次漂移教训）；
   跨入口共用纯函数。
4. **告警先行于自动化**：新约束上线前，其"被触发"必须有可观测面（日志 +
   事件 + 必要推送），否则无法验证是"没触发"还是"没生效"。
5. **验证三层**：单测（含事故重放）→ 集成/演练 → 线上早报对照。

## 5 · 验证命令

```bash
cd /home/zbox/baymax
.venv/bin/python -m pytest tests/test_alert_checks.py tests/test_risk_budget_freshness.py \
  tests/test_buy_gate.py tests/test_news_brief.py tests/test_hourly_exec_settle.py \
  tests/test_live_breaker.py tests/test_account_protocol.py -q
```

事故重放（今日时序）：单测 `test_news_freshness_round_dead_reports_with_last_success_time`
以 09-18 10:45 + 09-17 19:05 游标为输入，必须报停更。

P1/P2 观测面：

```bash
cd /home/zbox/baymax
.venv/bin/python -m pytest tests/test_shadow_context_ab.py tests/test_shadow_context_report.py \
  tests/test_pool_ctx_ingest.py tests/test_pool_score_report.py \
  tests/test_production_isolation.py -q
.venv/bin/python scripts/shadow_context_report.py --days 7      # P1 周报（effect vs noise）
.venv/bin/python scripts/pool_score_report.py --days 30         # P2 读数（样本不足时明确拒绝结论）
```

TCA / 影子账（P3 + 执行损耗）：

```bash
cd /home/zbox/baymax
.venv/bin/python -m pytest tests/test_exec_cost.py tests/test_tca_report.py \
  tests/test_tca_wiring.py tests/test_fill_remainder.py tests/test_hourly_exec_settle.py -q
.venv/bin/python scripts/tca_report.py --infer-ref     # 执行损耗（历史段为推理基准）
.venv/bin/python scripts/ghost_report.py               # 影子代价账（每条闸门"拦对了吗"）
```
