<!-- TOC: 分析框架与输出模板 -->
# 分析框架与输出模板

本文件教 AI **拿到数据后怎么分析、输出什么结构**——不是教怎么调接口（调接口见各 `references/*.md`），而是给「分析→输出」的模板。每个框架含：数据来源 → 输出结构 → 关键校验点。填充模板时严格按输出结构组织，不要堆 raw 数据。

## 目录

- 财报点评卡片（earnings review）
- 持仓诊断清单（position diagnostic）
- 选股结果排序逻辑（screener ranking）

---

## 一、财报点评卡片（earnings review）

**触发**：用户说「分析 XX 最新财报」「点评 XX 业绩」「XX 财报怎么样」时。

**数据来源**（优先用 `collect.py` 一键抓取；或手动逐个调）：
- `python skills/futuapi/scripts/quote/collect.py US.AAPL --json`（位置参数 code；含 snapshot + finances + rating + valuation，推荐；可加 `--with-options` 取期权摘要）
- 或手动：`get_financials_statements.py`（利润表+关键指标）+ `get_research_analyst_consensus.py`（评级目标价）+ `get_valuation_detail.py`（PE/PB 分位）+ `get_financials_earnings_price_move.py`（财报日价格反应）

**输出结构**（按此组织，每块 1-3 行）：

```
【XX（CODE）最新财报点评】报告期：YYYY-Qx

1. 核心指标（YoY）
   - 营收：X 亿，YoY ±x%
   - 归母净利润：X 亿，YoY ±x%
   - 毛利率：x%（较上年 ±xpct）
   - 净利率：x%（较上年 ±xpct）

2. 盈利质量
   - ROE：x%（趋势：上升/下降/平稳）
   - 经营性现金流 vs 净利润：X 亿 vs X 亿（勾稽是否匹配，经营现金流/净利润 < 1 时提示应收/存货风险）

3. 估值锚
   - PE_TTM：x（历史分位 x%）
   - PB：x（历史分位 x%）
   - 结论：估值处于历史高位/中位/低位

4. 财报日价格反应
   - 财报日涨跌幅：±x%，次日：±x%
   - IV 变化（若有期权）：财报前 IV x% → 财报后 x%

5. 一句话结论 + 风险点
   - 结论：…
   - 风险：…（如营收增速放缓、现金流弱于利润、估值偏高等）
```

**关键校验**：营收 YoY 与净利润 YoY 方向应一致，否则提示利润率波动原因；经营性现金流为负而净利润为正时**必须提示**。

---

## 二、持仓诊断清单（position diagnostic）

**触发**：用户说「诊断我的持仓」「持仓怎么样」「帮我看看仓位」时。

**数据来源**：`scripts/trade/get_portfolio.py --json`（返回 `{"funds":..., "positions":[...]}`）。

**⚠️ 强制字段口径**（见 `docs/FIELD_MAPPING.md`，与 APP 一致）：
- ✅ 用 `unrealized_pl`（未实现盈亏，均价口径）、`pl_ratio_avg_cost`（盈亏比，如 5.23=5.23%）、`average_cost`（均价）、`nominal_price`（现价）、`market_val`（市值）
- ❌ **禁止**用 `cost_price`/`diluted_cost`（摊薄成本）、`pl_val`/`pl_ratio`（摊薄口径，可能与 APP 不符）
- 多币种持仓：**不要直接累加**不同币种的 `market_val`/`unrealized_pl`；用 `accinfo_query(currency=目标币种)` 取账户级汇总，或按实时汇率换算（勿用硬编码汇率）

**输出结构**：

```
【持仓诊断】账户：XXX  总资产：X（币种）

1. 盈亏 Top3 / 亏 Top3
   - 浮盈：① CODE 名称 +x%（盈亏金额 X） ② … ③ …
   - 浮亏：① CODE 名称 -x%（盈亏金额 X） ② … ③ …

2. 集中度（⚠️ 单票占比 >30% 标红）
   - CODE1：占比 x%（市值 X / 总资产）
   - CODE2：占比 x%
   - 前三大合计占比：x%

3. 行业暴露
   - 行业A：x%  行业B：x%  行业C：x%（按 positions 的行业聚合）

4. 账户风险
   - risk_status：LEVEL3(安全)/LEVEL2(警告)/LEVEL1(危险)
   - 可用资金：X（available_funds）
   - 杠杆/保证金：power X、initial_margin X

5. 口径校验 + 建议
   - 未实现盈亏合计 vs 已实现盈亏： unrealized X + realized X = 总 X
   - 建议：…（如「集中度过高，建议减仓 CODE1」「浮亏标的 CODE 基本面恶化，建议止损」）
```

**关键校验**：单票占比 >30% **必须标红提示**；`risk_status` 为 LEVEL1/LEVEL2 **必须提示**风险。

---

## 三、选股结果排序逻辑（screener ranking）

**触发**：用户说「帮我选 XX 的股票」「筛选 XX 条件的股」并要求排序/精选时。

**数据来源**：`scripts/quote/get_stock_screen.py --config config.json --json`（返回 `{"last_page":..., "all_count":..., "data":[...]}`）。枚举名/单位/Term 见 `docs/STOCK_SCREEN_FIELDS.md`。

**两阶段处理**：

### 阶段 1：服务端筛选+排序（构造 config.json）
- `filters`：用 `simple_property`/`financial_property`/`cumulative_property` 等，传**原始值**（ROE 15.0 非 0.15；市值 1e10=100 亿）
- `sorts`（多键排序）：`[{"direction":"DESC","property_type":"simple","property_params":{"name":"MARKET_CAP"}}]`
- `retrieves`：**必须显式声明**（每项单 name），否则只返回 `stock_id`；至少取 `CODE`/`NAME`/`PRICE`/`MARKET_CAP` + 排序字段
- 分页：`--page-count 200`，需要续拉传 `--page-from`

### 阶段 2：客户端二次精选（AI 在返回的 items 上做）
1. **剔除**：ST/*ST/退市标的（按名称或代码规则）、流动性极差（成交额过小）的标的
2. **按流动性过滤**：剔除日均成交额低于阈值的（避免买入后卖不出）
3. **按估值分位分层**：如 PE_TTM 历史分位 <30% 标「低估」、30-70%「合理」、>70%「偏高」
4. **输出 Top10 表**：

```
| 序号 | 代码 | 名称 | 价格 | 市值 | 关键因子值 | 估值分位 | 一句话理由 |
|---|---|---|---|---|---|---|---|
| 1 | US.XXX | 名称 | X | X 亿 | ROE x% | x% | … |
| 2 | … | | | | | | |
```

每只附**一句话理由**（为什么入选：如「ROE 18% 行业领先 + 估值处历史低位」）。

**关键校验**：返回 `all_count` 过大（如 >1000）时提示用户收紧条件；`data` 为空时检查枚举名是否拼错（大小写敏感）或市场是否支持（如港股 BMP 权限、HK 仅 Q1+ANNUAL）。

---

## 相关技能路由

- 财报/估值数据脚本 → `references/fundamentals.md`
- 持仓/资金脚本 + 字段口径 → `references/trade-commands.md` + `docs/FIELD_MAPPING.md`
- 选股脚本 + 枚举映射 → `references/quote-commands.md` + `docs/STOCK_SCREEN_FIELDS.md`
- 一键全景数据 → `scripts/quote/collect.py`（见 `references/quote-commands.md`）
