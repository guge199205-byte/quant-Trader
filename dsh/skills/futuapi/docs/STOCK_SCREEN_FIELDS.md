# 选股器字段映射（V2 `get_stock_screen`，协议号 3252）

> **数据来源**：枚举取自 SDK 模块 `futu.quote.stock_screen_const`（源码 `futu/quote/stock_screen_const.py`）。脚本 `scripts/quote/get_stock_screen.py` 的 `_resolve()` 已支持传**枚举名字符串或数字**（如 `"PRICE"` 与 `2201` 等价）。**所有 `lower`/`upper` 传原始值，OpenD 负责倍率换算**——下表「倍率」列只用于理解**返回值**，不要用于缩放输入。本表列高频常用字段，完整枚举见 SDK 源码上述路径。

## 数值单位约定

| 类型 | 约定 | 示例 |
|---|---|---|
| 价格（PRICE/OPEN_PRICE/HIGH/LOW/BID_PRICE...） | 原始货币值 | `10.0` = 10 元/港元/美元 |
| 市值（MARKET_CAP/FLOAT_MARKET_CAP） | 原始值 | `1e10` = 100 亿 |
| 金额（NET_PROFIT/REVENUE/EBITDA/TURNOVER...） | 原始值 | `1e8` = 1 亿 |
| 百分比/比率（所有 `_PCT`/`_RATE`/`_RATIO`/增长率/换手率/振幅） | 原始百分数，**不是小数** | `5.0` = 5%，不是 `0.05` |
| 股数（TOTAL_SHARE/FLOAT_SHARE/VOLUME） | 原始股数 | `1000` = 1000 股 |
| 时间戳（LISTED_DATE/RELEASED_DATE/SURPRISE_*_DATE） | unix 秒 | 传/返回 unix 时间戳 |

## 通用枚举

| 枚举类 | 成员 |
|---|---|
| `ScrMarket`（`simple_field` MARKET 的 values） | `HK`=1, `US`=2, `CN`=3, `SG`=4, `CA`=5, `AU`=6, `JP`=7, `MY`=8 |
| `ScrSortDir`（排序方向） | `ASC`=1, `DESC`=2, `ABS_ASC`=3, `ABS_DESC`=4 |
| `Period`（指标/K线形态周期） | `MINUTE_1`=1, `MINUTE_3`=2, `MINUTE_5`=3, `MINUTE_15`=4, `HOUR_1`=5, `MINUTE_30`=6, `DAY`=11, `WEEK`=21, `MONTH`=31 |
| `Position`（指标位置关系） | `OVER`=1, `BELOW`=2, `CROSS_UP`=3, `CROSS_DOWN`=4 |
| `OptionHVPeriod`（期权 HV 周期） | `HV_30D`=0, `HV_60D`=1, `HV_90D`=2, `HV_120D`=3, `HV_365D`=4 |

## Term 财报期（`financial_property` 必传 `term`）

| 枚举 | 值 | 含义 |
|---|---|---|
| `Q1` | 1 | Q1 报 |
| `Q2` | 2 | Q2 报 |
| `Q3` | 3 | Q3 报 |
| `Q4` | 4 | Q4 报 |
| `Q6` | 6 | 中报（累积报） |
| `Q9` | 9 | 三季报（累积报） |
| `LATEST` | 10 | 最新单季报 |
| `ANNUAL` | 100 | 年报 FY |
| `SURPRISE_LATEST` | 200 | 最近一期 |
| `SURPRISE_LATEST_QUARTER` | 201 | 最近一期季报 |
| `SURPRISE_LATEST_HALF` | 202 | 最近一期半年报 |
| `SURPRISE_LATEST_ANNUAL` | 203 | 最近一期年报 |
| `SURPRISE_LATEST_ALL` | 204 | 全部 |

> **硬规则**：港股仅 `Q1`+`ANNUAL` 有数据，`Q2`/`Q3`/`Q4` 通常缺失；`SURPRISE_LATEST`(200-204) 在 HK/US 当前返回数据 ≈ `ANNUAL`，**慎用**。
>
> **⚠️ 美股财务因子筛选覆盖稀疏**：用 `financial_property`（如 `ROE`/`NET_PROFIT`/`REVENUE`）作为 US 市场的 **filter** 时，命中的标的**非常少**（实测 `ROE` term=`ANNUAL` 全美仅 ~3 只、`LATEST` 更少）——这是服务端数据覆盖的天然限制，不是 term 选错或参数写错。若 PE（`simple_property`）∩ ROE（`financial_property`）交集为 0，**不要怀疑枚举名/单位**：先用 `financial_property` 单独筛 `ROE` 看 `all_count` 确认覆盖量，再决定是否放宽阈值或改用 `featured_property` 的 `ANALYST_RATING`/`HIST_PERCENTILE_PE` 等替代因子。港股财务筛选覆盖正常，此问题主要影响 US。
>
> **⚠️ financial retrieve 可能不返回值**：实测 `financial_property` 作为 **retrieve**（取值字段）时，即使标的通过了同字段的 filter 筛选，返回的 `dval` 仍可能为 `None`（如 US ROE>15 筛出的 3 只标的，retrieve ROE 全为 None）。这是服务端限制，filter 生效但 retrieve 不一定回填。需取 ROE 等财务值时，建议改用 `get_financials_statements.py` 单标的查询。

## 筛选类型（filter `type`）

每条 filter 是一个 dict，`type` 决定形状。`property_type`（用于 sort 的 `property_params`）取值：`simple`/`cumulative`/`financial`/`basic`/`featured`/`broker`/`klineShape`（**camelCase，非 `kline_shape`**）/`option`。

| `type` | `name` 对应枚举 | 关键字段 | 示例 |
|---|---|---|---|
| `simple_field` | `SimpleField`（用 `field` 非 `name`） | `field`, `values` | `{"type":"simple_field","field":"MARKET","values":["HK"]}` |
| `plate` | — | `plate_ids`, `parent_plate_id` | `{"type":"plate","plate_ids":["BK1001"]}` |
| `simple_property` | `SimpleProperty` | `name`, `lower`, `upper`, `*_included`(默认 true) | `{"type":"simple_property","name":"PRICE","lower":10.0,"upper":100.0}` |
| `cumulative_property` | `CumulativeProperty` | `name`, `days`(默认 1), `lower`, `upper` | `{"type":"cumulative_property","name":"PRICE_CHANGE_PCT","days":5,"lower":5.0}` |
| `financial_property` | `FinancialProperty` | `name`, `term`(Term), `lower`, `upper` | `{"type":"financial_property","name":"ROE","term":"ANNUAL","lower":15.0}` |
| `indicator_positional` | `Indicator`(`first_indicator_name`) | `first_indicator_name`, `period_type`(Period), `position`(Position), `second_indicator` | `{"type":"indicator_positional","first_indicator_name":"MA5","period_type":"DAY","position":"CROSS_UP","second_indicator":"MA20"}` |
| `indicator_pattern` | `Pattern`(`name`) | `name`, `period_type`(Period) | `{"type":"indicator_pattern","name":"MACD_GOLD_CROSS","period_type":"DAY"}` |
| `featured_property` | `FeaturedProperty` | `name`, `intervals` | `{"type":"featured_property","name":"CHIPS_PROFIT_RATIO","intervals":[{"filterMin":{"value":50.0,"includes":true}}]}` |
| `broker_holdings` | `BrokerProperty` | `name`, `days`, `param`, `intervals` | `{"type":"broker_holdings","name":"CONCENTRATED_DISTRIBUTION","days":30,"intervals":[...]}` |
| `kline_shape` | `KlineShapeProperty`(`name`) + `KlineShapeType`(`value_set`) | `name`, `period`(Period，**必传**), `value_set` | `{"type":"kline_shape","name":"SHAPE_TYPE","period":"DAY","value_set":["DOUBLE_BOTTOMS"]}` |
| `option` | `OptionProperty`(`name`) | `name`, `intervals`, `period`(OptionHVPeriod) | `{"type":"option","name":"STOCK_IV","period":"HV_30D","intervals":[{"filterMin":{"value":20.0,"includes":true}}]}` |

`intervals` 形状（featured/broker/option/indicator_positional 用）：`[{"filterMin":{"value":N,"includes":bool}, "filterMax":{"value":N,"includes":bool}}]`。

## 属性枚举（常用子集）

> 每类仅列高频字段。**完整枚举见 SDK 源码 `futu/quote/stock_screen_const.py`**（对应类名）。

### SimpleProperty（行情属性，`simple_property`）

| 含义 | 枚举名 | 倍率/单位 |
|---|---|---|
| 最新价格 | `PRICE` | 1000 |
| 今开 / 昨收 / 最高 / 最低 | `OPEN_PRICE` / `LAST_CLOSE` / `HIGH` / `LOW` | 1000 |
| 涨跌幅 | `PRICE_CHANGE_RATE` | 1e5（5.0=5%） |
| 买入价 / 卖出价 | `BID_PRICE` / `ASK_PRICE` | 1000 |
| 量比 / 委比 | `VOLUME_RATIO` / `BID_ASK_RATIO` | 1e5 / 1e7 |
| 市值 | `MARKET_CAP` | 1000（`1e10`=100 亿） |
| TTM 市盈率 / 年化市盈率 | `PE_TTM` / `PE_ANNUAL` | 1e5 |
| 市净率 | `PB` | 1e5 |
| 股息率 | `DIVIDEND_RATIO` | 1e5 |
| 上市时间 / 上市天数 | `LISTED_DATE` / `LISTED_DAYS` | 时间戳 / 1 |
| 盘前价格 / 盘前涨跌幅 | `BEFORE_PRICE` / `BEFORE_CHANGE_PCT` | 1000 / 1e5 |
| 盘后价格 / 盘后涨跌幅 | `AFTER_PRICE` / `AFTER_CHANGE_PCT` | 1000 / 1e5 |
| 夜盘价格 / 夜盘涨跌幅 | `OVERNIGHT_PRICE` / `OVERNIGHT_CHANGE_PCT` | 1e9 / 1e5 |
| 每手价格 | `LOT_PRICE` | 1000 |

> 完整列表含高精度 `_HP` 变体、融资融券率、52 周相对位等，见源码 `SimpleProperty`。

### CumulativeProperty（区间累计属性，带 `days`）

| 含义 | 枚举名 | 倍率/单位 |
|---|---|---|
| 价格涨跌额 | `PRICE_CHANGE` | 1000 |
| 价格涨跌幅 | `PRICE_CHANGE_PCT` | 1e5（5.0=5%） |
| 价格振幅 | `AMPLITUDE` | 1e5 |
| 平均成交量 / 平均成交额 | `AVG_VOLUME` / `AVG_TURNOVER` | 1 / 1000 |
| 换手率 | `TURNOVER_RATIO` | 1e5 |

> 完整 9 个成员见源码 `CumulativeProperty`（含 `HIGH_TO_N_DAY_HIGH` 等）。

### FinancialProperty（财务属性，`financial_property` 必传 `term`）

| 含义 | 枚举名 | 倍率/单位 |
|---|---|---|
| 净利润 / 净利润增长率 | `NET_PROFIT` / `NET_PROFIT_GROWTH` | 1000 / 1e5 |
| 营业额 / 营业额增长率 | `REVENUE` / `REVENUE_GROWTH` | 1000 / 1e5 |
| 毛利率 / 净利率 | `GROSS_PROFIT_RATIO` / `NET_PROFIT_RATIO` | 1e5 |
| 净资产收益率（ROE） | `ROE` | 1e5（15.0=15%） |
| 资产负债率 | `DEBT_TO_ASSETS` | 1e5 |
| EBITDA / EBITDA 利润率 | `EBITDA` / `EBITDA_MARGIN` | 1000 / 1e5 |
| 投入资本回报率（ROIC） | `ROIC` | 1e5 |
| 基本 / 稀释每股收益 | `BASIC_EPS` / `DILUTED_EPS` | 1000 |
| 总股数 / 流通股数 | `TOTAL_SHARE` / `FLOAT_SHARE` | 1 |
| 流通市值 | `FLOAT_MARKET_CAP` | 1000 |
| 市销率 TTM / 市现率 TTM | `PS_TTM` / `PCF_TTM` | 1e5 |
| 经营活动现金流 TTM | `OPERATING_CASH_FLOW_TTM` | 1000 |
| 自由现金流 | `FREE_CASH_FLOW` | 1e3 |
| 主要 CAGR（3/5/10 年复合增长） | `REVENUE_CAGR` / `NET_PROFIT_CAGR` / `ROE_CAGR` / `EBITDA_CAGR` | 1e5 |

> 完整 ~130 成员含偿债（流动比率/速动比率/财务杠杆）、营运（周转率）、成长率、超预期（SURPRISE_*）等，见源码 `FinancialProperty`。

### FeaturedProperty（特色属性）

| 含义 | 枚举名 | 倍率/单位 |
|---|---|---|
| 筹码获利比例 | `CHIPS_PROFIT_RATIO` | 1e5 |
| 卖空持仓量 / 回补天数 | `SHORT_POSITION` / `COVER_DAYS` | 1 / 1e3 |
| 交易热度 / 搜索热度 / 综合热度 | `TRADE_INDEX` / `SEARCH_INDEX` / `AVERAGE_INDEX` | 1e5 |
| 个股机构持股比例 | `INST_RATIO` | 1e5 |
| 分析师评级 / 目标价 | `ANALYST_RATING` / `ANALYST_TARGET_PRICE` | 1000 / 1e9 |
| 晨星公允价值 / 星级 / 护城河 | `MORNINGSTAR_FAIR_VALUE` / `MORNINGSTAR_STAR` / `MORNINGSTAR_MOAT` | 1e9 / 1 / 1 |
| PE/PB 历史百分位 | `HIST_PERCENTILE_PE` / `HIST_PERCENTILE_PB` | 1e5 |
| 平均股息率（3/5 年） | `AVERAGE_DIVIDEND_YIELD` | 1e5 |
| 主力大单净流入（参数 CashFlowPeriod） | `CASH_FLOW_MAIN_NET_IN` | 1e3 |

> 完整 ~100 成员含员工人数、股东优待、澳洲股息抵免等，见源码 `FeaturedProperty`。

### BrokerProperty（经纪商持仓，港股，全 7 个）

| 含义 | 枚举名 | 倍率/单位 |
|---|---|---|
| 持仓分布集中度 | `CONCENTRATED_DISTRIBUTION` | 1e5 |
| 持仓经纪商变动 | `HOLDINGS_CHANGE` | 1e5 |
| 持仓经纪商数量 / 排行 | `BROKER_NUM` / `BROKER_RANK` | 1 |
| 经纪商持仓量占比 | `HOLDINGS_RATIO` | 1e5 |
| 中央结算持股占比 / 变动 | `CENTRAL_HOLDINGS_RATIO` / `CENTRAL_HOLDINGS_CHANGE` | 1e5 |

### KlineShapeProperty + KlineShapeType（K 线形态）

`KlineShapeProperty`（全 6 个）：`SHAPE_TYPE`(形态本身) / `RISE_PROB`(上涨概率,1e5) / `AFTER_SELECTED_CHG`(入选后涨跌幅,1e5) / `SELECTED_TIME` / `SUPPORT_LEVEL`(支撑位,1e9) / `PRESSURE_LEVEL`(压力位,1e9)。

`KlineShapeType`（`SHAPE_TYPE` 的 `value_set`，全 22 个有效值）：
- 看涨：`DOUBLE_BOTTOMS`(W底) / `TRIPLE_BOTTOMS` / `HEAD_SHOULDERS_BOTTOM` / `CUP_BOTTOM` / `TRUMPET_BOTTOM` / `FLAG` / `SYMMETRY_TRIANGLE` / `SUSTAINABLE_RHOMBUS` / `WEDGE` / `SUSTAINABLE_TRIANGLE`
- 看跌：`DOUBLE_PEAKS`(M顶) / `TRIPLE_PEAKS` / `HEAD_SHOULDERS_PEAK` / `CUP_PEAK` / `TRUMPET_PEAK` / `FLAG_DOWN` / `SYMMETRY_TRIANGLE_DOWN` / `SUSTAINABLE_RHOMBUS_DOWN` / `WEDGE_DOWN` / `SUSTAINABLE_TRIANGLE_DOWN`
- 分类：`BULLISH_TYPE` / `BEARISH_TYPE`

### OptionProperty（正股期权属性，全 11 个）

| 含义 | 枚举名 | 倍率/单位 |
|---|---|---|
| 正股 IV / IV 排名 / IV 百分位 | `STOCK_IV` / `STOCK_IV_RANK` / `STOCK_IV_PERCENTILE` | 1e6 |
| 财报发布日 IV（带财报周期参数） | `STOCK_EARNINGS_IV_CRUSH` | 1e6 |
| 正股 IV 涨跌 / 变化率 | `STOCK_IV_CHG` / `STOCK_IV_CHG_RATIO` | 1e6 |
| 正股 HV（带 `period` OptionHVPeriod） | `STOCK_HV` | 1e6 |
| IV-HV / IV/HV（默认 30 天） | `STOCK_IV_MINUS_HV` / `STOCK_IV_DIV_HV` | 1e6 |
| 期权成交量 / 总持仓 | `STOCK_OPTION_VOL` / `STOCK_OPTION_OPEN_IN` | 1 |

### Indicator（技术指标，`indicator_positional` 用）

常用：`PRICE`(最新价) / `MA5`/`MA10`/`MA20`/`MA60`/`MA120`/`MA250`(简单均线) / `MA`(动态) / `EMA5`..`EMA250` / `KDJ_K`/`KDJ_D`/`KDJ_J` / `MACD_DIF`/`MACD_DEA`/`MACD_MACD` / `RSI_12` / `BOLL_UPPER`/`BOLL_MIDDLE`/`BOLL_LOWER`。完整 ~39 成员见源码 `Indicator`。

### Pattern（技术形态，`indicator_pattern` 用，全 22 个有效值）

均线排列：`MA_LONG`/`MA_SHORT`/`EMA_LONG`/`EMA_SHORT`；金叉死叉：`KDJ_GOLD_CROSS`/`KDJ_DEATH_CROSS`/`MACD_GOLD_CROSS`/`MACD_DEATH_CROSS`/`RSI_GOLD_CROSS`/`RSI_DEATH_CROSS`；背离：`KDJ_TOP_DIVERGE`/`KDJ_BOTTOM_DIVERGE`/`MACD_TOP_DIVERGE`/`MACD_BOTTOM_DIVERGE`/`RSI_TOP_DIVERGE`/`RSI_BOTTOM_DIVERGE`；BOLL：`BOLL_BREAK_UPPER`/`BOLL_BREAK_LOWER`/`BOLL_CROSS_MID_UP`/`BOLL_CROSS_MID_DOWN`；总览：`BULLISH`/`BEARISH`。

## retrieves 与 sort

- **retrieves**：每项是**单个 `name`**（非 `fields` 数组），如 `{"type":"simple","name":"PRICE"}`。**不声明 `retrieves` 只返回 `stock_id`**。retrieve `type` 取值：`basic`/`simple`/`cumulative`/`financial`/`indicator`/`featured`/`broker`/`option`/`kline_shape`。
- **sort**（单字段 `set_sort`）或 **sorts**（多字段数组 `add_sort`）：`{"direction":"DESC","property_type":"simple","property_params":{"name":"MARKET_CAP"}}`。`direction` 用 `ScrSortDir`，`property_type` 见上方筛选类型表注。

## 注意事项 / 禁用与慎用

1. **枚举名大小写敏感、精确匹配**：`price`/`Price` 不解析（`_resolve` 用 `hasattr` 严格匹配），会原样透传给 OpenD 报错。
2. **`property_type` 用 camelCase `klineShape`**（非 `kline_shape`），与其余 snake_case `type` 混用，易错。
3. **`kline_shape` 的 `period` 必传**，仅支持 `DAY`(=11) 与 `HOUR_1`(=5)（注意 `WEEK`=21，不是 1 小时）。
4. **港股 BMP 权限不支持** V2 选股。
5. 港股财务仅 `Q1`+`ANNUAL`；`SURPRISE_LATEST`(200-204) HK/US ≈ ANNUAL，慎用。
6. **美股财务因子 filter 覆盖稀疏**（`ROE`/`NET_PROFIT` 等作 US filter 命中极少，实测 ROE ANNUAL 全美仅 ~3 只）；交集为 0 时先用单因子查 `all_count` 确认覆盖量，勿怀疑枚举名/单位，详见上方 Term 小节。
7. `Term.SURPRISE_*` 财务属性（4906-4944）需配 `SURPRISE_*` term。

> **期权筛选器（`get_option_screen`，协议号 3253，另一个接口）的坑**（不属于本表，仅交叉引用）：`OptUnderlyingIndicator.PLATE`=103 禁用（后端未实现，传报错）；`OptIndicator.PREMIUM`=2021 仅支持 sort/retrieve，作为 filter 报错；`OptIndicator.BUY_BREAK_EVEN_POINT`=3023 已废弃，用 `BUY_TO_BEP`=3011。

## V1（`get_stock_filter`）对比

V1 是旧接口（`StockField` 枚举 + CLI flags + 手动 `*1e8` 倍率，市值传「亿」），仅 8 个可排序字段（market_val/price/volume/turnover/turnover_rate/change_rate/pe/pb）。**V2 优先**（244+ 因子、声明式 JSON、OpenD 自动换算）。V1 仅在需极简筛选时用。
