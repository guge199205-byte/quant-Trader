# 接口限制（调用前必须考虑）

调用接口时必须考虑以下限制，避免因额度不足或频率超限导致请求失败。

## 频率限制

限频规则：30 秒内最多 n 次，第 1 次和第 n+1 次的间隔需大于 30 秒。

| 接口 | 限频 |
|------|------|
| `place_order` | 15 次/30 秒 |
| `place_combo_order` | 15 次/30 秒（与 `place_order` 共用） |
| `modify_order` | 20 次/30 秒 |
| `order_list_query` | 10 次/30 秒 |

**批量操作注意**：当需要循环调用受限频接口时（如批量下单、批量查询历史K线），必须在循环中加入适当的 `time.sleep()` 间隔，避免触发限频。

## 订阅额度限制

- 每只股票订阅一个类型占 1 个订阅额度，取消订阅释放额度
- 同一只股票的不同 SubType 分别计数
- 订阅后至少 1 分钟才能反订阅
- 反订阅后需所有连接都反订阅同一标的，额度才会释放
- 不足 1 分钟关闭连接不会释放订阅额度，需等待 1 分钟后自动反订阅
- 通过 `query_subscription.py` 查询已使用的额度
- 香港市场需要 LV1 及以上权限才能订阅
- 美股盘前盘后需设置 `--extended-time`

## 历史 K 线额度限制

- 最近 30 天内，每请求 1 只股票的历史 K 线占 1 个额度
- 30 天内重复请求同一只股票不会重复累计
- 同一股票不同周期的 K 线只占 1 个额度
- **调用 `request_history_kline` 前**，应先通过 `get_history_kl_quota(get_detail=True)` 检查剩余额度是否充足
- **批量获取多只股票 K 线时**，先检查额度，确认剩余额度 >= 需要请求的股票数量后再执行

## 额度等级

订阅额度和历史 K 线额度根据用户资产和交易活跃度分级：

| 用户类型 | 订阅额度 | 历史 K 线额度 |
|---------|---------|-------------|
| 开户用户 | 100 | 100 |
| 总资产达 1 万 HKD | 300 | 300 |
| 总资产达 50 万 HKD / 月交易笔数 > 200 / 月交易额 > 200 万 HKD（任一） | 1000 | 1000 |
| 总资产达 500 万 HKD / 月交易笔数 > 2000 / 月交易额 > 2000 万 HKD（任一） | 2000 | 2000 |

## 其他限制

| 接口 | 限制 |
|------|------|
| `get_market_snapshot` | 每次最多 400 个标的 |
| `get_order_book` | num 最大为 10（加密货币支持 1/5/10/20/40） |
| `get_rt_ticker` | num 最大 1000 |
| `get_cur_kline` | num 最大 1000 |
| `request_history_kline` | 单次 max_count 最大 1000，超过需用 page_req_key 翻页 |
| `get_stock_filter` | 单次最多 200 个结果 |
| `get_search_quote` | 每 30 秒最多 10 次 |
| `get_search_news` | 每 30 秒最多 10 次 |
| 预测市场盘口/实时K线/逐笔 | `get_event_contract_order_book`/`get_event_contract_kline`/`get_event_contract_ticker` 查询前必须先 `subscribe_event_contract` 订阅对应类型，否则报错 |
| `request_history_event_contract_kline` | 预测市场历史 K 线，无需订阅，占用历史 K 线额度 |
| `get_event_contract_order_book` | num 需 > 0，默认 10 |
| 预测市场 K 线 | 仅支持 K_1M/K_5M/K_60M/K_DAY，其余类型报错；`max_count` 上限 1000 |
| `get_event_contract_ticker` | count 最大 1000 |

## 加密货币接口限制

### 连接限制

- `OpenQuoteContext(security_firm=...)` 加密货币行情仅在 `security_firm` 为 `FUTUSECURITIES/FUTUINC/FUTUSG` 时生效；传入 `MY/AU/JP/CA` 等其他值或无效值接口报错
- `OpenCryptoTradeContext(security_firm=...)` 仅支持 `FUTUSECURITIES/FUTUINC/FUTUSG`；传入其他券商返回空
- 加密货币交易 **仅支持 REAL 实盘**，不支持 SIMULATE 模拟交易
- 加密货币不支持融资融券；`get_margin_ratio` 对 crypto code 直接报错

### 订阅与额度

- 加密货币订阅额度计算方式与正股一致：币种/币对每订阅 1 个类型占 1 个额度
- **币种（`CC.BTC`）和币对（`CC.BTCUSD`）是不同标的**，需分别订阅各占 1 个额度
- 同一标的不同 K 线周期只占 1 个额度
- 历史 K 线额度占用方式与正股一致；**多券商会占用 1 个额度**
- 加密货币指数（`CC.BTC`）**不支持摆盘**，订阅 ORDER_BOOK 返回空；仅币对支持
- 加密货币不支持经纪队列（`get_broker_queue` 无效）
- 总资产门槛覆盖加密货币资产（后续优化为多券商最大资产判断）

### 下单限制

| 券商 | 限价单 | 市价单 | 备注 |
|------|-------|-------|------|
| FUTUSECURITIES（FUTUHK） | ✅ | ✅ | 其他订单类型报"不支持的订单类型" |
| FUTUINC（moomoo US） | ✅ | ✅ | 同上 |
| FUTUSG（moomoo SG） | ✅ | ❌ | 市价单报"不支持的订单类型" |

- **非整数数量**：加密货币 `qty` 为 float，支持如 `0.000136` 的小数下单
- **code 必须在该账户白名单内**，否则报错："{security_firm}{acc_type}{acc_id} 不支持交易 {code}"
- **session 不校验**（加密货币 7×24 交易，不区分时段）
- **有效期限**：限价单固定传 `GTC`，市价单固定传 `IOC`（无需用户指定）
- **改单/失效/生效/删单**：不支持，报"不支持的订单操作，如需修改订单信息，请撤单后重新提交"；仅支持 `ModifyOrderOp.CANCEL` 和 `cancel_all_order`
- **保证金账户不支持加密货币**，`acctradinginfo_query` 不能查询加密货币最大可买可卖

### 资金流水限制

- 加密货币 `get_acc_cash_flow` 必须传 `start` + `end`，按创建时间（`create_time`）联日查询
- 证券/期货账户必须传 `clearing_date`，按清算日单日查询
- 参数不匹配时报错："{account}仅支持通过{parameter}查询资金流水"

### 市场状态

| 映射 | 枚举值 | 对应 EST 时间 |
|------|-------|--------------|
| 切 T（待开盘） | `NONE` | EST 19:00:00 |
| 交易中（早盘） | `MORNING` | EST 19:00:01 – 18:59:58 |
| 收盘 | `CLOSED` | EST 18:59:59 |
