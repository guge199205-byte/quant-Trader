# API 速查（完整函数签名）

## 行情 API（OpenQuoteContext）

### 订阅管理（4 个）

```
subscribe(code_list, subtype_list, is_first_push=True, subscribe_push=True, is_detailed_orderbook=False, extended_time=False, session=Session.NONE)  -- 订阅(消耗订阅额度, 每只股票每个类型占1个额度; 调用前应先用 query_subscription 检查额度; session 仅用于美股实时K线/分时/逐笔，不支持 OVERNIGHT)
unsubscribe(code_list, subtype_list, unsubscribe_all=False)  -- 反订阅(订阅后至少1分钟才能反订阅)
unsubscribe_all()  -- 反订阅所有
query_subscription(is_all_conn=True)  -- 查询订阅状态(调用subscribe前应先检查)
```

### 实时数据 - 需要先订阅（6 个）

```
get_stock_quote(code_list)  -- 获取实时报价
get_cur_kline(code, num, ktype=KLType.K_DAY, autype=AuType.QFQ)  -- 获取实时 K 线
get_rt_data(code)  -- 获取实时分时
get_rt_ticker(code, num=500)  -- 获取实时逐笔
get_order_book(code, num=10)  -- 获取实时摆盘
get_broker_queue(code)  -- 获取实时经纪队列(仅港股)
```

### 快照与历史（4 个）

```
get_market_snapshot(code_list)  -- 获取快照(无需订阅, 每次最多400个)
request_history_kline(code, start=None, end=None, ktype=KLType.K_DAY, autype=AuType.QFQ, fields=[KL_FIELD.ALL], max_count=1000, page_req_key=None, extended_time=False, session=Session.NONE)  -- 获取历史K线(消耗历史K线额度, 调用前应先用 get_history_kl_quota 检查剩余额度; 单次max_count最大1000, 超过需用page_req_key翻页; session 仅用于美股分时段历史K线，不支持 OVERNIGHT)
get_rehab(code)  -- 获取复权因子
get_history_kl_quota(get_detail=False)  -- 查询历史K线额度(调用request_history_kline前应先检查)
```

### 基础信息（7 个）

```
get_stock_basicinfo(market, stock_type=SecurityType.STOCK, code_list=None)  -- 获取股票静态信息
get_global_state()  -- 获取各市场状态（返回 dict，key 包括 market_hk/market_us/market_sh/market_sz/market_hkfuture/market_usfuture/server_ver/qot_logined/trd_logined 等）
request_trading_days(market=None, start=None, end=None, code=None)  -- 获取交易日历
get_market_state(code_list)  -- 获取市场状态
get_stock_filter(market, filter_list, plate_code=None, begin=0, num=200)  -- 条件选股
get_search_quote(keyword, max_count=10)  -- 搜索行情标的
get_search_news(keyword, max_count=10, news_sub_type=NewsSubType.ALL)  -- 搜索资讯
```

### 板块（3 个）

```
get_plate_list(market, plate_class)  -- 获取板块列表
get_plate_stock(plate_code, sort_field=SortField.CODE, ascend=True)  -- 获取板块内股票
get_owner_plate(code_list)  -- 获取股票所属板块
```

### 衍生品（9 个）

```
get_option_chain(code, index_option_type=IndexOptionType.NORMAL, start=None, end=None, option_type=OptionType.ALL, option_cond_type=OptionCondType.ALL, data_filter=None)  -- 获取期权链
get_option_expiration_date(code, index_option_type=IndexOptionType.NORMAL)  -- 获取期权到期日
get_option_strategy(code, option_strategy, expire_time, spread=None, far_expire_time=None, index_option_type=IndexOptionType.NORMAL, option_type=OptionType.ALL, strike_price=None)  -- 获取期权策略组合腿列表(返回OptionStrategyLeg列表，可作为get_option_quote/get_option_strategy_analysis的入参)
get_option_strategy_spread(code, option_strategy, expire_time, far_expire_time=None, index_option_type=IndexOptionType.NORMAL)  -- 获取期权策略有效价差列表(仅支持SPREAD/STRANGLE/COLLAR/BUTTERFLY/CONDOR/IRON_BUTTERFLY/IRON_CONDOR/DIAGONAL_SPREAD)
get_option_quote(combo_leg_list)  -- 获取期权快照行情(combo_leg_list为OptionStrategyLeg列表，通常由get_option_strategy返回)
get_option_strategy_analysis(combo_leg_list)  -- 期权策略损益分析，返回组合级 bid1/ask1(摆盘价)/最大盈亏/盈亏平衡点/盈利概率/Delta/Theta 等（组合摆盘价与组合下单定价优先使用本接口，勿对单腿快照手动加减）
get_referencestock_list(code, reference_type)  -- 获取关联股票(正股/窝轮/牛熊/期权)
get_future_info(code_list)  -- 获取期货合约信息
get_warrant(stock_owner='', req=None)  -- 获取窝轮/牛熊证
```

### 预测市场（Prediction Market / Event Contract）（13 个查询 + 3 个订阅 + 3 个推送 Handler）

合约代码 `EC.xxx`，YES/NO 二元预测合约。查询盘口/实时K线/逐笔前需先 `subscribe_event_contract` 订阅对应类型；历史 K 线（`request_history_event_contract_kline`）无需订阅，走历史 K 线额度；K 线仅支持 K_1M/K_5M/K_60M/K_DAY。

```
get_event_contract_category(category=None)  -- 预测市场分类列表
filter_competition(category=None, tag=None)  -- 赛事筛选（赛事名称 + 玩法全集）
get_event_contract_series_list(category=None, tag=None)  -- Series 列表
get_event_contract_event_list(series_code, status=None, next_page=None, count=None)  -- Event 列表（分页）
get_event_contract(event_code, next_page=None, count=None)  -- Contract 列表（返回 dict{contract_list, recommend_contracts}，分页）
get_event_contract_milestone_list(category=None, competition=None, related_event=None, next_page=None, count=None)  -- 里程碑列表（分页）
get_valid_combo_list(category=None, competition=None, series=None, next_page=None, count=None)  -- 可 Combo 事件列表（返回 data, mvc, next_page）
request_combo_quotes(combo_leg_list, mvc)  -- Combo 询价（combo_leg_list 为 ComboLeg 列表，pred_side 必填；mvc 透传）
get_event_contract_snapshot(code_list)  -- 预测市场快照（无需订阅）
get_event_contract_order_book(code, num=10)  -- 预测市场摆盘（需先订阅 ORDER_BOOK；返回 dict{yes_bids/asks, no_bids/asks}）
get_event_contract_kline(code, pre_side=None, ktype=KLType.K_DAY, kline_source=None, max_count=1000)  -- 预测市场 K 线（需先订阅对应 K 线类型）
get_event_contract_ticker(code, count=30)  -- 预测市场逐笔（需先订阅 TICKER）
request_history_event_contract_kline(code, start=None, end=None, pre_side=None, ktype=KLType.K_DAY, kline_source=None, max_count=1000, page_req_key=None)  -- 预测市场历史 K 线（无需订阅，自动分页）
subscribe_event_contract(code_list, subtype_list, kline_source_list=None, is_first_push=True, subscribe_push=True)  -- 订阅预测市场
unsubscribe_event_contract(code_list, subtype_list, kline_source_list=None)  -- 取消订阅（按维度）
unsubscribe_all_event_contract()  -- 取消当前连接所有预测市场订阅
```

### 资金（2 个）

```
get_capital_flow(stock_code, period_type=PeriodType.INTRADAY, start=None, end=None)  -- 获取资金流向
get_capital_distribution(stock_code)  -- 获取资金分布
```

### 自选股（3 个）

```
get_user_security_group(group_type=UserSecurityGroupType.ALL)  -- 获取自选股分组
get_user_security(group_name)  -- 获取自选股列表
modify_user_security(group_name, op, code_list)  -- 修改自选股
```

### 到价提醒（2 个）

```
get_price_reminder(code=None, market=None)  -- 获取到价提醒
set_price_reminder(code, op, key=None, reminder_type=None, reminder_freq=None, value=None, note=None)  -- 设置到价提醒
```

### IPO（1 个）

```
get_ipo_list(market)  -- 获取IPO列表
```

**行情 API 小计：41 个**

---

## 交易 API（OpenSecTradeContext / OpenFutureTradeContext / OpenCryptoTradeContext）

### 账户（3 个）

```
get_acc_list()  -- 获取交易业务账户列表
unlock_trade(password=None, password_md5=None, is_unlock=True)  -- 解锁/锁定交易（⚠️ 本技能不通过 API 解锁，需用户在 OpenD GUI 手动解锁）
accinfo_query(trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, refresh_cache=False, currency=Currency.HKD, asset_category=AssetCategory.NONE)  -- 查询账户资金
```

### 下单改单（4 个）

```
place_order(price, qty, code, trd_side, order_type=OrderType.NORMAL, adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, remark=None, time_in_force=TimeInForce.DAY, fill_outside_rth=False, aux_price=None, trail_type=None, trail_value=None, trail_spread=None, session=Session.NONE)  -- 下单(限频: 15次/30秒; session 仅对美股生效，支持 RTH/ETH/OVERNIGHT/ALL)
place_combo_order(combo_leg_list, price, qty, order_type=OrderType.NORMAL, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, remark="", time_in_force=TimeInForce.DAY, expire_time=None)  -- 组合下单(限频: 15次/30秒; 与 place_order 共用一个限频)
modify_order(modify_order_op, order_id, qty, price, adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, aux_price=None, trail_type=None, trail_value=None, trail_spread=None)  -- 改单/撤单(限频: 20次/30秒)
cancel_all_order(trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, trdmarket=TrdMarket.NONE)  -- 撤销所有订单
```

### 订单查询（3 个）

```
order_list_query(order_id="", order_market=TrdMarket.NONE, status_filter_list=[], code='', start='', end='', trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, refresh_cache=False)  -- 查询今日订单
history_order_list_query(status_filter_list=[], code='', order_market=TrdMarket.NONE, start='', end='', trd_env=TrdEnv.REAL, acc_id=0, acc_index=0)  -- 查询历史订单
order_fee_query(order_id_list=[], acc_id=0, acc_index=0, trd_env=TrdEnv.REAL)  -- 查询订单费用
```

### 成交查询（2 个）

```
deal_list_query(code="", deal_market=TrdMarket.NONE, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, refresh_cache=False)  -- 查询今日成交
history_deal_list_query(code='', deal_market=TrdMarket.NONE, start='', end='', trd_env=TrdEnv.REAL, acc_id=0, acc_index=0)  -- 查询历史成交
```

### 持仓与资金（5 个）

```
position_list_query(code='', position_market=TrdMarket.NONE, pl_ratio_min=None, pl_ratio_max=None, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, refresh_cache=False, show_option_strategy_view=False)  -- 查询持仓（新增 show_option_strategy_view；返回新增 combo_id/strategy_type/position_type/acc_id/jp_acc_type）
acctradinginfo_query(order_type, code, price, order_id=None, adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, session=Session.NONE)  -- 查询最大可买/卖数量(session 仅对美股生效，支持 RTH/ETH/OVERNIGHT/ALL)
comboorder_tradinginfo_query(combo_leg_list, price, qty, order_type=OrderType.NORMAL, order_id=None, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0)  -- 查询组合可交易信息(返回 nlv_change/initial_margin_change/maintenance_margin_change/option_bp/max_withdraw_change/bp_decrease)
get_acc_cash_flow(clearing_date='', trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, cashflow_direction=CashFlowDirection.NONE)  -- 查询账户现金流水
get_margin_ratio(code_list)  -- 查询融资融券比率
```

**交易 API 小计：17 个**

---

## 加密货币（Crypto）API 差异速查

本节汇总加密货币相关接口相对常规股票的参数/行为差异，不额外计入 API 总数（复用以上接口 + `OpenCryptoTradeContext`）。

### 行情接口差异

| 接口 | 加密货币差异 |
|------|-------------|
| `OpenQuoteContext(..., security_firm=...)` | 新增 `security_firm` 入参，仅支持 `FUTUSECURITIES/FUTUINC/FUTUSG`；传入 `MY/AU/JP/CA` 或无效值接口报错 |
| `subscribe(code_list, subtype_list, ...)` | 币种/指数（`CC.BTC`）使用全球行情；币对（`CC.BTCUSD`）按创建连接时的 `security_firm` 取对应上游（HK→Hashkey，US→Coinbase，SG→DDEX）；币种和币对需分别订阅 |
| `get_order_book(code, num)` | 支持 1/5/10/20/40 档；指数不支持摆盘（返回空） |
| `get_broker_queue(code)` | 加密货币不支持经纪队列 |
| `request_history_kline(code, ...)` | 周期支持 K_1M/K_3M/K_5M/K_10M/K_15M/K_30M/K_60M/K_120M/K_180M/K_240M/K_DAY/K_WEEK/K_MON/K_QUARTER/K_YEAR；订阅同一币种不同周期只占 1 个额度 |
| `get_market_state(code_list)` | 加密货币状态映射：`NONE`→待开盘（EST 19:00:00 切T）、`MORNING`→交易中（EST 19:00:01–18:59:58）、`CLOSED`→收盘 |
| `get_capital_flow(stock_code, ...)` | `code` 支持币种 `CC.BTC` 和币对 `CC.BTCUSD`；`period_type` 支持 INTRADAY/DAY/WEEK/MONTH |
| `get_capital_distribution(stock_code)` | `code` 支持币种和币对 |

### 交易接口差异（OpenCryptoTradeContext）

| 接口 | 加密货币差异 |
|------|-------------|
| `OpenCryptoTradeContext(security_firm=...)` | `security_firm` 仅支持 `FUTUSECURITIES/FUTUINC/FUTUSG`；不需要 `filter_trdmarket` |
| `get_acc_list()` | 返回 `trdmarket_auth=TrdMarket.CRYPTO`，`trd_env=REAL`，`acc_type=CASH` |
| `accinfo_query(trd_env=TrdEnv.REAL, ...)` | 仅支持 `REAL`；返回新增 `crypto_mv`(float)、`exposure_level`(ExposureLevel)、`exposure_limit`(float USD)、`used_limit`(float USD)、`remaining_limit`(float USD) |
| `position_list_query(...)` | `code` 使用 `CC.BTC`（Base currency）；`qty`/`can_sell_qty` 为 float；返回新增 `currency` 字段（默认 USD） |
| `acctradinginfo_query(...)` | 仅支持现金账户查询最大可买可卖（保证金账户不支持加密货币） |
| `place_order(code='CC.BTCUSD', qty=0.000136, ...)` | `code` 用币对；`qty` 为 float（支持非整数）；FUTUHK/FUTUINC 支持限价+市价，FUTUSG 仅限价；限价单传 `time_in_force=GTC`，市价单传 `IOC`；session 不校验 |
| `modify_order(modify_order_op, ...)` | 仅支持 `ModifyOrderOp.CANCEL`；`NORMAL/DISABLE/ENABLE/DELETE` 报错"不支持的订单操作" |
| `cancel_all_order(...)` | 支持 |
| `order_list_query/history_order_list_query/order_fee_query` | `code` 用 `CC.BTCUSD` |
| `deal_list_query/history_deal_list_query` | `code` 用 `CC.BTCUSD` |
| `get_acc_cash_flow(start, end, ...)` | 加密货币按 `[start, end]` 联日查询（按 create_time）；返回新增 `create_time`，`settlement_date` 返回 N/A |
| `get_margin_ratio(code_list)` | 加密货币不支持融资融券，直接报错 |

### 代码命名规范

| 场景 | 代码格式 | 示例 |
|------|---------|------|
| 订阅行情、币对行情、下单、订单/成交、可买可卖 | `CC.{BaseQuote}`（无斜杠） | `CC.BTCUSD`、`CC.ETHUSD`、`CC.BTCHKD` |
| 币种/指数行情、持仓、资金流向/分布 | `CC.{Base currency}` | `CC.BTC`、`CC.ETH`、`CC.SOL` |

### 券商支持矩阵

| 券商 | Crypto 交易 | 限价单 | 市价单 | 行情上游 |
|------|------------|--------|--------|---------|
| FUTUSECURITIES（富途香港） | ✅ | ✅ | ✅ | Hashkey |
| FUTUINC（moomoo US） | ✅ | ✅ | ✅ | Coinbase |
| FUTUSG（moomoo SG） | ✅ | ✅ | ❌ | DDEX |
| 其他（AU/JP/MY/CA） | ❌ | - | - | - |

---

## 推送 Handler（12 个）

### 行情推送（10 个）

```
StockQuoteHandlerBase   -- 报价推送回调
OrderBookHandlerBase    -- 摆盘推送回调
CurKlineHandlerBase     -- K线推送回调
TickerHandlerBase       -- 逐笔推送回调
RTDataHandlerBase       -- 分时推送回调
BrokerHandlerBase       -- 经纪队列推送回调
PriceReminderHandlerBase -- 到价提醒推送回调
EventContractOrderBookHandlerBase  -- 预测市场摆盘推送回调（需 subscribe_event_contract 订阅 ORDER_BOOK）
EventContractKlineHandlerBase      -- 预测市场 K 线推送回调（需订阅对应 K 线类型）
EventContractTickerHandlerBase     -- 预测市场逐笔推送回调（需订阅 TICKER）
```

### 交易推送（2 个）

```
TradeOrderHandlerBase   -- 订单状态推送回调
TradeDealHandlerBase    -- 成交推送回调
```

注意：交易推送不需要单独订阅，设置 Handler 后自动接收。

---

## 基础接口

```
OpenQuoteContext(host='127.0.0.1', port=11111, is_encrypt=None, security_firm=SecurityFirm.NONE, ai_type=1)  -- 创建行情连接（security_firm 仅在订阅加密货币行情时生效，支持 FUTUSECURITIES/FUTUINC/FUTUSG；其他市场行情忽略该参数）
OpenSecTradeContext(filter_trdmarket=TrdMarket.NONE, host='127.0.0.1', port=11111, security_firm=SecurityFirm.FUTUSECURITIES, ai_type=1)  -- 创建证券交易连接（security_firm 需根据用户所属券商设置，见 FUTU_SECURITY_FIRM 枚举表）
OpenFutureTradeContext(host='127.0.0.1', port=11111, security_firm=SecurityFirm.FUTUSECURITIES, ai_type=1)  -- 创建期货交易连接（security_firm 同上）
OpenCryptoTradeContext(host='127.0.0.1', port=11111, is_encrypt=None, security_firm=SecurityFirm.FUTUSECURITIES)  -- 创建加密货币交易连接（security_firm 仅支持 FUTUSECURITIES/FUTUINC/FUTUSG，其他券商返回空；不需要传 filter_trdmarket）
ctx.close()  -- 关闭连接
ctx.set_handler(handler)  -- 注册推送回调
SysNotifyHandlerBase  -- 系统通知回调
```

**全部 API 总计：行情 35 + 交易 17 + 推送 Handler 9 + 基础 7 = 68 个接口**

## SubType 订阅类型完整列表

| SubType | 说明 | 对应推送 Handler |
|---------|------|-----------------|
| `QUOTE` | 报价 | `StockQuoteHandlerBase` |
| `ORDER_BOOK` | 摆盘 | `OrderBookHandlerBase` |
| `TICKER` | 逐笔 | `TickerHandlerBase` |
| `K_1M` ~ `K_MON` | K 线 | `CurKlineHandlerBase` |
| `RT_DATA` | 分时 | `RTDataHandlerBase` |
| `BROKER` | 经纪队列（仅港股） | `BrokerHandlerBase` |

## 关键枚举值

- **TrdSide**: `BUY` | `SELL`
- **OrderType**: `NORMAL`(限价) | `MARKET`(市价)
- **TrdEnv**: `REAL` | `SIMULATE` — 加密货币仅支持 `REAL`
- **TimeInForce**: `DAY` | `GTC` | `IOC`(立即成交或取消) — `IOC` 仅用于加密货币市价单；加密货币限价单固定 `GTC`
- **ModifyOrderOp**: `NORMAL`(改单) | `CANCEL`(撤单) | `DISABLE` | `ENABLE` | `DELETE` — 加密货币只支持 `CANCEL`
- **TrdMarket**: `HK` | `US` | `CN` | `HKCC` | `SG` | `MY` | `JP` | `CRYPTO`(加密货币)
- **Market（行情）**: `HK` | `US` | `SH` | `SZ` | `JP`（仅正股，不支持衍生品）| `SG`（正股+窝轮，不支持期权）| `MY`（正股+窝轮，行情需账户权限）| `HK_FUTURE` | `US_FUTURE` | `CC`(加密货币)
- **SecurityType**: `STOCK` | `IDX` | `ETF` | `WARRANT` | `BOND` | `DRVT` | `PLATE` | `CRYPTO`
- **ExchType**: 新增 `ExchType_CC_CRYPTO = 19`（加密货币交易所）
- **Session**: `NONE` | `RTH`(盘中) | `ETH`(盘前盘后) | `OVERNIGHT`(夜盘) | `ALL`(全部) — 订阅仅支持 RTH/ETH/ALL（不支持 OVERNIGHT）；下单支持 RTH/ETH/OVERNIGHT/ALL；加密货币不校验 session
- **ExposureLevel（持仓限额状态，仅加密货币账户返回）**:
  - `NORMAL` 正常（剩余限额/持仓限额 > 10%）
  - `NEAR_LIMIT` 即将用尽（0% < 剩余/持仓 ≤ 10%）
  - `RESTRICTED` 受限（剩余限额 = 0，禁止买入）
  - `SAFE` 安全（含贷权益 ≥ 初始保证金）
  - `MODERATE` 适中（剩余流动性 ≥ 10% × 含贷权益）
  - `WARNING` 预警（剩余流动性 < 10% × 含贷权益）
  - `MARGIN_CALL` 危险（含贷权益 ≤ 维持保证金）
- **SecurityFirm**: `FUTUSECURITIES` | `FUTUINC` | `FUTUSG` | `FUTUAU` | `FUTUJP` | `FUTUMY` | `FUTUCA` | `NONE` — 加密货币仅支持前三家
