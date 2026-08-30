
# Tiger Open API 交易 / Trading

> 中文 | English — 双语技能，代码通用。Bilingual skill with shared code examples.
> 官方文档 Docs: https://docs.itigerup.com/docs/place-order

## 安全规范 / Safety Rules

> **默认使用模拟账户。Default to Paper Trading.**

### 模拟交易 vs 实盘交易 / Paper vs Live Trading

| 特性 Feature | 模拟 Paper | 实盘 Live |
|------|------|------|
| 资金 Funds | 虚拟，无风险 Virtual, no risk | 真实资金 Real money |
| 账户类型 `account_type` | `PAPER` | `STANDARD` / `GLOBAL` |
| 默认 Default | **是** Yes | 需用户明确要求 User must explicitly request |

### 实盘下单工作流 / Live Order Workflow

当用户要求实盘交易时，**必须严格执行以下流程，缺少任何步骤均不得下单** / When user requests live trading, every step below is **mandatory** — skip any step and do NOT place the order:

1. **确认账户 Verify account**: 调用 `get_managed_accounts()` 获取账户列表，筛选 `account_type != 'PAPER'` 的实盘账户 / Call `get_managed_accounts()`, filter for non-PAPER accounts
2. **预览订单 Preview**: 调用 `preview_order()` 查看预估佣金和保证金，并将结果展示给用户 / Call `preview_order()`, show the commission and margin estimate to the user
3. **等待用户明确确认 Wait for explicit user confirmation**: 将订单详情（标的、方向、数量、价格、账户、预估佣金）以表格形式展示，**然后停止，等待用户回复确认**。未收到用户明确的"确认"/"confirm"/"是"/"yes"之前，**禁止调用 `place_order()`** / Display order details in a table (symbol, action, quantity, price, account, est. commission), then **stop and wait**. Do NOT call `place_order()` until the user explicitly replies to confirm.
4. **执行下单 Execute**: 用户确认后才执行 `place_order()` / Only after user confirms, call `place_order()`
5. **检查状态 Check status**: `place_order()` 返回成功仅表示提交，需通过 `get_orders()` 确认成交 / Submission success ≠ execution; poll `get_orders()` to confirm fill

---

## 初始化 / Initialize

```python
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.trade.trade_client import TradeClient

client_config = TigerOpenClientConfig(props_path='/path/to/your/tiger_openapi_config.properties')
trade_client = TradeClient(client_config=client_config)

# 获取管理的账户 / Get managed accounts
accounts = trade_client.get_managed_accounts()
# 返回 AccountProfile 列表 / Returns list of AccountProfile objects
for a in accounts:
    print(f"{a.account}: type={a.account_type}, capability={a.capability}, status={a.status}")
```

**AccountProfile 属性 / AccountProfile Attributes**:

| 属性 Attribute | 说明 Description |
|---------------|-----------------|
| `account` | 交易账户ID Trading account ID |
| `account_type` | 账户类型: `PAPER`(模拟) / `STANDARD`(美股标准) / `GLOBAL`(全球) |
| `capability` | 保证金类型: `CASH`(现金账户) / `MGRN`(Reg T 保证金) / `PMGRN`(投资组合保证金) |
| `status` | 账户状态: `New`/`Funded`/`Open`/`Pending`/`Abandoned`/`Rejected`/`Closed`/`Unknown` |



---

## 合约 / Contracts

交易前必须获取合约对象。Contracts must be obtained before trading.

### 本地构造 / Local Construction

```python
from tigeropen.common.util.contract_utils import (
    stock_contract, option_contract, option_contract_by_symbol,
    future_contract, war_contract_by_symbol, iopt_contract_by_symbol,
    fund_contract, cc_contract
)

# 股票 / Stock
contract = stock_contract(symbol='AAPL', currency='USD')

# 港股 / HK Stock
contract = stock_contract(symbol='00700', currency='HKD')

# 期权 / Option
opt = option_contract_by_symbol(symbol='AAPL', expiry='20250829',
                                 strike=150.0, put_call='CALL', currency='USD')

# 期货 / Future
fut = future_contract(symbol='CL2509', currency='USD')

# 窝轮 / Warrant (必须提供 expiry, strike, put_call, local_symbol)
war = war_contract_by_symbol(symbol='12345', expiry='20251231',
                              strike=10.0, put_call='CALL',
                              local_symbol='12345', currency='HKD')

# 牛熊证 / CBBC (必须提供 expiry, strike, put_call, local_symbol)
iopt = iopt_contract_by_symbol(symbol='56789', expiry='20251231',
                                strike=20.0, put_call='CALL',
                                local_symbol='56789', currency='HKD')

# 基金 / Fund
fund = fund_contract(symbol='ARKK', currency='USD')

# 数字货币 / Crypto
cc = cc_contract(symbol='BTC/USD', currency='USD')
```

### 远程获取 / Remote Fetch

```python
# 单个合约 / Single contract
contract = trade_client.get_contract(symbol='AAPL', sec_type='STK')
contract = trade_client.get_contract(symbol='AAPL', sec_type='OPT',
                                      expiry='20250829', strike=150.0, put_call='CALL')

# 批量合约 / Multiple contracts
# 关键字是 symbol（单数），但接受 list / keyword is `symbol` (singular), accepts a list
contracts = trade_client.get_contracts(symbol=['AAPL', 'TSLA'], sec_type='STK')

# 衍生品合约列表 / Derivative contracts
derivatives = trade_client.get_derivative_contracts(symbol='AAPL', sec_type='OPT')
```

### Contract 对象属性 / Contract Attributes

| 属性 Attribute | 说明 Description |
|---------------|-----------------|
| `symbol` | 代码 Symbol |
| `sec_type` | 证券类型 Security type (STK/OPT/FUT/WAR/IOPT) |
| `currency` | 货币 Currency |
| `exchange` | 交易所 Exchange |
| `name` | 名称 Name |
| `expiry` | 到期日 Expiry (options/futures) |
| `strike` | 行权价 Strike (options) |
| `put_call` | CALL/PUT (options) |
| `multiplier` | 乘数 Multiplier |
| `lot_size` | 每手股数 Lot size |
| `shortable` | 可否卖空 Shortable |
| `shortable_count` | 可卖空数量 Shortable count |
| `min_tick` | 最小价格变动 Min tick size |

---

## 下单 / Place Orders
<!-- 当用户提到 "买入"、"卖出"、"下单"、"buy"、"sell"、"order"、"place order" 时 -->

### 限价单 LMT / Limit Order

```python
from tigeropen.common.util.order_utils import limit_order

order = limit_order(account=client_config.account, contract=contract,
                    action='BUY', quantity=100, limit_price=150.0)
trade_client.place_order(order)
print(f"Order ID: {order.id}")
```

### 市价单 MKT / Market Order

```python
from tigeropen.common.util.order_utils import market_order, market_order_by_amount

order = market_order(account=client_config.account, contract=contract,
                     action='BUY', quantity=100)
trade_client.place_order(order)

# 按金额下单(碎股) / By amount (fractional shares)
order = market_order_by_amount(account=client_config.account, contract=contract,
                               action='BUY', amount=5000)  # 买入 $5000 的股票
trade_client.place_order(order)
```

> 也有 `limit_order_by_amount` 用于按金额的限价单。
> There's also `limit_order_by_amount` for limit orders by amount.

### ⚠️ 碎股卖出 / Selling Fractional Shares

**碎股（qty < 1，如 0.82379 股）不能直接传给 `limit_order(quantity=...)` 或 `market_order(quantity=...)`**，SDK 的 `quantity` 参数是整数，小数部分会被截断（如 `0.82379 → 0`），导致下单数量错误或为 0。

**正确做法 / Correct approach**:

```python
# 从持仓获取碎股数量（salable_qty 是带小数的原始值，如 0.82379）
positions = trade_client.get_positions(symbol='DVN')
pos = positions[0]
salable = pos.salable_qty  # e.g. 0.82379

# ❌ 错误：直接传 float → quantity 被截断为 0
# order = limit_order(account=..., contract=..., action='SELL', quantity=salable, ...)

# ✅ 方法一：市价单卖出碎股（推荐，碎股只支持市价单）
order = market_order(account=client_config.account, contract=contract,
                     action='SELL', quantity=1,  # 占位，实际由 quantity_scale 控制
                     )
order.quantity = salable          # 设置 float 数量
order.quantity_scale = len(str(salable).split('.')[-1])  # 自动计算小数位数，如 5
trade_client.place_order(order)

# ✅ 方法二：按持仓市值卖出（适合不知道精确股数时）
market_value = pos.market_value  # 当前市值
order = market_order_by_amount(account=client_config.account, contract=contract,
                               action='SELL', amount=market_value)
trade_client.place_order(order)
```

> **关键规则**: 碎股（fractional shares）**只支持市价单**，不支持限价单。`limit_order_by_amount` 对碎股仓位会被 API 拒绝（API 报错）。
> Fractional share positions only support **market orders**, not limit orders.

### 止损单 STP / Stop Order

```python
from tigeropen.common.util.order_utils import stop_order

order = stop_order(account=client_config.account, contract=contract,
                   action='SELL', quantity=100, aux_price=140.0)
# aux_price = 触发价 / trigger price
trade_client.place_order(order)
```

### 止损限价单 STP_LMT / Stop Limit Order

```python
from tigeropen.common.util.order_utils import stop_limit_order

order = stop_limit_order(account=client_config.account, contract=contract,
                         action='SELL', quantity=100,
                         limit_price=139.0, aux_price=140.0)
# 价格到 aux_price 时触发，以 limit_price 限价委托
trade_client.place_order(order)
```

### 跟踪止损单 TRAIL / Trailing Stop

```python
from tigeropen.common.util.order_utils import trail_order

# 百分比跟踪 / Percentage trailing
order = trail_order(account=client_config.account, contract=contract,
                    action='SELL', quantity=100, trailing_percent=5.0)

# 金额跟踪 / Amount trailing
order = trail_order(account=client_config.account, contract=contract,
                    action='SELL', quantity=100, aux_price=3.0)
trade_client.place_order(order)
```

### 算法单 TWAP/VWAP / Algo Orders

```python
from tigeropen.common.util.order_utils import algo_order, algo_order_params

params = algo_order_params(start_time=1625097600000, end_time=1625184000000,
                            participation_rate=0.1)
order = algo_order(account=client_config.account, contract=contract,
                   action='BUY', quantity=1000, strategy='TWAP',  # 或 'VWAP'
                   algo_params=params)
trade_client.place_order(order)
```

### 附加订单(止盈止损) / Bracket Orders (Take Profit / Stop Loss)

```python
from tigeropen.common.util.order_utils import limit_order_with_legs, order_leg

# 推荐使用 limit_order_with_legs / Recommended: use limit_order_with_legs
legs = [
    order_leg(leg_type='PROFIT', price=170.0),              # 止盈 / Take profit
    order_leg(leg_type='LOSS', price=140.0),                 # 止损 / Stop loss
    # 也可用跟踪止损 / Or trailing stop loss:
    # order_leg(leg_type='LOSS', trailing_percent=5.0),
]
order = limit_order_with_legs(account=client_config.account, contract=contract,
                              action='BUY', quantity=100, limit_price=150.0,
                              order_legs=legs)
trade_client.place_order(order)
```

> 附加订单仅支持限价单作为主订单，最多2个 leg。子订单在主订单全部成交后自动生效。
> Bracket orders only support limit as primary, max 2 legs. Legs activate after primary is fully filled.

### order_leg 参数 / order_leg Parameters

| 参数 Parameter | 说明 Description |
|---------------|-----------------|
| `leg_type` | `PROFIT`(止盈) 或 `LOSS`(止损) |
| `price` | 触发价格 Trigger price |
| `limit_price` | 限价(可选) Limit price (optional) |
| `trailing_percent` | 跟踪止损百分比 Trailing percent |
| `trailing_amount` | 跟踪止损金额 Trailing amount |
| `quantity` | 数量(默认同主单) Quantity (default: same as primary) |

---

### 下单参数总览 / Order Parameters Overview

| 参数 Parameter | 说明 Description | 必填 Required |
|---------------|-----------------|--------------|
| `account` | 交易账户 Trading account | ✅ |
| `contract` | 合约对象 Contract | ✅ |
| `action` | `BUY` / `SELL` | ✅ |
| `quantity` | 数量 Quantity | ✅ |
| `limit_price` | 限价 Limit price | LMT/STP_LMT |
| `aux_price` | 触发价/跟踪金额 Trigger/trailing amount | STP/STP_LMT/TRAIL |
| `trailing_percent` | 跟踪止损百分比 Trailing percent | TRAIL |
| `time_in_force` | `DAY`/`GTC`(撤销前有效)/`GTD`(指定日期) | - |
| `outside_rth` | 盘前盘后 Pre/after hours (仅美股 US only) | - |
| `trading_session_type` | 交易时段 `TradingSessionType` 枚举值(PRE_RTH_POST/OVERNIGHT/RTH/FULL/HK_AUC/HK_CTS/HK_AUC_CTS) | - |
| `order_id` | 自定义订单ID Custom order ID | - |
| `user_mark` | 自定义标记 Custom mark | - |
| `quantity_scale` | 数量精度(碎股) Quantity scale (fractional) | - |
| `total_cash_amount` | 按金额下单 Order by amount | - |
| `expire_time` | GTD到期时间 GTD expire time | GTD |

### 各市场支持的订单类型 / Order Types by Market

| 订单类型 | 美股 US | 港股 HK | 新加坡 SG | 期货 Futures | 期权 Options |
|---------|---------|---------|----------|-------------|-------------|
| MKT 市价 | ✅ | ✅ | ✅ | ✅ | ✅ |
| LMT 限价 | ✅ | ✅ | ✅ | ✅ | ✅ |
| STP 止损 | ✅ | - | - | ✅ | ✅ |
| STP_LMT 止损限价 | ✅ | - | - | ✅ | ✅ |
| TRAIL 跟踪止损 | ✅ | - | - | - | - |
| TWAP/VWAP 算法 | ✅ | - | - | - | - |
| AL 竞价限价 | - | ✅ | - | - | - |
| AM 竞价市价 | - | ✅ | - | - | - |
| OCA 一撤全撤 | ✅ | ✅ | - | - | - |

```bash
# CLI 下单 / Place orders via CLI
tigeropen trade order place --symbol AAPL --action buy --order-type lmt --quantity 10 --limit-price 150
tigeropen trade order place --symbol AAPL --action sell --order-type mkt --quantity 10
tigeropen trade order place --symbol 00700 --action buy --order-type lmt --quantity 100 --limit-price 350 --sec-type stk
tigeropen trade order place --symbol AAPL --action buy --order-type lmt --quantity 10 --limit-price 150 -y  # 跳过确认 skip confirm
```

### 特殊下单场景 / Special Order Scenarios

```python
# 盘前盘后 / Pre/after-hours (美股 US only)
order = limit_order(account=client_config.account, contract=contract,
                    action='BUY', quantity=100, limit_price=150.0)
order.outside_rth = True
trade_client.place_order(order)

# 夜盘(通宵) / Overnight trading (使用 TradingSessionType 枚举)
from tigeropen.common.consts import TradingSessionType
order = limit_order(account=client_config.account, contract=contract,
                    action='BUY', quantity=100, limit_price=150.0)
order.trading_session_type = TradingSessionType.OVERNIGHT.value
trade_client.place_order(order)
# TradingSessionType 可选值: PRE_RTH_POST, OVERNIGHT, RTH, FULL, HK_AUC, HK_CTS, HK_AUC_CTS

# GTC 订单 / Good Till Cancelled
order = limit_order(account=client_config.account, contract=contract,
                    action='BUY', quantity=100, limit_price=150.0)
order.time_in_force = 'GTC'
trade_client.place_order(order)

# 港股下单 / HK stock order (注意: 数量须为 lot_size 的整数倍，可通过 get_trade_metas 查询)
hk_contract = stock_contract(symbol='00700', currency='HKD')
order = limit_order(account=client_config.account, contract=hk_contract,
                    action='BUY', quantity=100, limit_price=350.0)
trade_client.place_order(order)

# 期货下单 / Futures order
fut_contract = future_contract(symbol='CL2509', currency='USD')
order = limit_order(account=client_config.account, contract=fut_contract,
                    action='BUY', quantity=1, limit_price=70.0)
trade_client.place_order(order)

# 价格修正工具 / Price correction utility
from tigeropen.common.util.price_util import PriceUtil
price_util = PriceUtil(trade_client)
corrected = price_util.correct_price(symbol='AAPL', price=150.123)
```

### 港股竞价单 / HK Auction Orders

```python
from tigeropen.common.util.order_utils import auction_limit_order, auction_market_order

# 竞价限价单 AL / Auction Limit Order
order = auction_limit_order(account=client_config.account, contract=hk_contract,
                            action='BUY', quantity=100, limit_price=350.0)
trade_client.place_order(order)

# 竞价市价单 AM / Auction Market Order
order = auction_market_order(account=client_config.account, contract=hk_contract,
                             action='BUY', quantity=100)
trade_client.place_order(order)
```

> 竞价单用于港股竞价时段(开盘竞价/收盘竞价)。`time_in_force` 支持 `DAY` 和 `OPG`。
> Auction orders for HK pre-opening/closing auction sessions. `time_in_force` supports `DAY` and `OPG`.

### OCA 订单(一撤全撤) / OCA Order (One-Cancels-All)

```python
from tigeropen.common.util.order_utils import oca_order, order_leg

# OCA 订单: 多个子订单中任一成交，其余自动撤销
# OCA order: when any leg fills, others are automatically cancelled
legs = [
    order_leg(leg_type='LMT', limit_price=160.0),   # 限价单
    order_leg(leg_type='STP', price=140.0),           # 止损单
]
order = oca_order(account=client_config.account, contract=contract,
                  action='SELL', order_legs=legs, quantity=100)
trade_client.place_order(order)
```

---

## 冰山单 / Iceberg Order（SDK 3.6.0+）

大单拆分为小单分批展示，减少市场冲击 / Splits a large order into visible slices.

```python
from tigeropen.common.consts import PriceType
from tigeropen.common.util.order_utils import iceberg_order

# 基础形式：display_size 必填
order = iceberg_order(account, contract, action='BUY', quantity=1000,
                      limit_price=180.0, display_size=100)

# 完整形式
order = iceberg_order(
    account, contract, action='BUY', quantity=1000, limit_price=180.0,
    display_size=100,            # 每次展示数量（必填）
    min_display_size=50,         # 最小展示数量，默认等于 display_size
    check_intervals=30,          # 刷新间隔（秒），不填由服务端决定
    price_type=PriceType.LIMIT_PRICE,
    start_time=None,             # 生效开始时间（epoch 毫秒）
    end_time=None,               # 生效结束时间（epoch 毫秒）
    time_in_force='DAY')

trade_client.place_order(order)
```

`PriceType` 枚举共 4 个值：`LIMIT_PRICE`（限价）、`ASK_PRICE`（卖一价）、
`BID_PRICE`（买一价）、`LATEST_PRICE`（最新价）。

---

## 期权提前行权 / Option Early Exercise（SDK 3.5.9+）

```python
from tigeropen.common.consts import OptionExerciseType

# 1. 行权检验（预估行权后持仓变化）/ Check
check = trade_client.check_option_exercise(
    contract_id=123456789,
    exercise_type=OptionExerciseType.EXERCISE,
    quantity=1.0,
    executing_date='2026-08-21')

# 2. 查询可行权持仓 / Exercisable positions
positions = trade_client.get_option_exercise_positions(
    exercise_type=OptionExerciseType.EXERCISE)

# 3. 提交行权申请 / Submit
ok = trade_client.submit_option_exercise(
    contract_id=123456789,
    exercise_type=OptionExerciseType.EXERCISE,
    quantity=1.0,
    executing_date='2026-08-21')

# 4. 查询行权申请记录（分页）/ Records
records = trade_client.get_option_exercise_records(page=1, size=20)

# 5. 撤销行权申请 / Cancel
cancelled = trade_client.cancel_option_exercise(exercise_id=987654321)
```

`OptionExerciseType` 枚举：`EXERCISE`（值 `"Exercise"`，提前行权）、
`EXPIRE`（值 `"Expire"`，提前放弃行权）。
`EXPIRE` 类型可在 `check_option_exercise` 中额外传 `itm_rate`（0-10，价内率阈值）。

`submit_option_exercise` / `cancel_option_exercise` 返回 `bool`。

---

## 预览订单 / Preview Order
<!-- 当用户提到 "预览"、"佣金"、"保证金"、"preview"、"commission" 时 -->

```python
preview = trade_client.preview_order(order)
# 返回 dict / Returns dict
```

**preview_order 返回字段 / Return Fields** (dict):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `init_margin_before` | 下单前账户初始保证金 Initial margin before order |
| `init_margin` | 下单后预计初始保证金 Estimated initial margin after order |
| `maint_margin_before` | 下单前维持保证金 Maintenance margin before order |
| `maint_margin` | 下单后预计维持保证金 Estimated maintenance margin after order |
| `margin_currency` | 保证金货币 Margin currency (e.g. `USD`) |
| `equity_with_loan_before` | 下单前含借贷值股权 Equity with loan value before |
| `equity_with_loan` | 下单后含借贷值股权 Equity with loan value after |
| `min_commission` | 预期最低佣金 Minimum estimated commission |
| `max_commission` | 预期最高佣金 Maximum estimated commission |
| `commission_currency` | 佣金货币 Commission currency |
| `warning_text` | 无法下单原因（仅在拒绝时返回）Rejection reason (only when rejected) |

> 若无法下单，仅返回 `warning_text` 字段。

```bash
# CLI
tigeropen trade order preview --symbol AAPL --action buy --order-type lmt --quantity 10 --limit-price 150
```

## 修改订单 / Modify Order
<!-- 当用户提到 "改单"、"修改订单"、"modify"、"change order" 时 -->

```python
order = trade_client.get_order(id=123456789)
trade_client.modify_order(order,
                           quantity=200,
                           limit_price=155.0,
                           time_in_force='GTC',
                           outside_rth=True)
```

```bash
# CLI (ORDER_ID 为全局订单 id / global order id)
tigeropen trade order modify ORDER_ID --limit-price 155 --quantity 200
```

> 订单类型不可修改。仅 `Submitted` 或 `PartiallyFilled` 状态可改。
> Order type cannot be changed. Only Submitted/PartiallyFilled orders can be modified.

## 撤销订单 / Cancel Order
<!-- 当用户提到 "撤单"、"取消订单"、"cancel order" 时 -->

```python
trade_client.cancel_order(id=123456789)

# 批量撤销 / Batch cancel
for order in trade_client.get_open_orders():
    trade_client.cancel_order(id=order.id)
```

```bash
# CLI
tigeropen trade order cancel ORDER_ID
tigeropen trade order cancel ORDER_ID -y   # 跳过确认 / skip confirmation
```

> 撤销为异步操作。使用 `id` (全局订单ID)。Cancellation is async. Use global order `id`.

---

## 查询订单 / Query Orders
<!-- 当用户提到 "订单"、"委托"、"orders"、"order status"、"order list" 时 -->

```python
# 所有订单 / All orders
orders = trade_client.get_orders(limit=100)

# 待成交 / Open (pending) orders
open_orders = trade_client.get_open_orders()

# 已成交 / Filled orders (需指定时间，最多90天)
filled = trade_client.get_filled_orders(start_time='2025-01-01', end_time='2025-03-31')

# 已取消 / Cancelled orders
cancelled = trade_client.get_cancelled_orders()

# 指定订单 / Single order
order = trade_client.get_order(id=123456789)

# 条件筛选 / Filtered query
orders = trade_client.get_orders(
    symbol='AAPL', sec_type='STK', market='US',
    states=['Filled', 'Cancelled'],
    sort_by='latest_time',  # latest_time(默认) / limit_price
    limit=100,
    is_brief=False  # True: 精简模式 / brief mode
)

# 分页查询 / Pagination
orders = trade_client.get_orders(limit=20)
# 使用 page_token 翻页 / Use page_token for next page
if hasattr(orders, 'page_token') and orders.page_token:
    next_page = trade_client.get_orders(limit=20, page_token=orders.page_token)
```

### Order 对象属性 / Order Attributes

| 属性 Attribute | 说明 Description |
|---------------|-----------------|
| `id` | 全局订单ID Global order ID |
| `order_id` | 用户订单ID User order ID |
| `account` | 交易账户ID Account ID |
| `symbol` | 标的代码 Symbol |
| `sec_type` | 证券类型 Security type |
| `action` | BUY/SELL |
| `order_type` | MKT/LMT/STP/STP_LMT/TRAIL |
| `status` | 订单状态 Order status (见下方 Status 表) |
| `quantity` | 下单数量 Order quantity |
| `filled_quantity` | 成交数量 Filled quantity |
| `remaining_quantity` | 剩余数量 Remaining quantity |
| `limit_price` | 限价 Limit price |
| `aux_price` | 触发价/跟踪金额 Aux/trigger price |
| `avg_fill_price` | 平均成交价 Average fill price |
| `trade_time` | 最新成交时间 Last trade time |
| `time_in_force` | DAY/GTC/GTD |
| `outside_rth` | 是否盘前盘后 Outside regular trading hours |
| `trading_session_type` | 交易时段类型 Trading session type |
| `order_legs` | 附加订单列表 Attached order legs |
| `algo_params` | 算法参数 Algo parameters |
| `user_mark` | 自定义标记 Custom mark |
| `reason` | 拒绝/错误原因 Rejection reason |
| `commission` | 实际佣金 Actual commission |
| `combo_type` | 组合类型 Combo type |
| `currency` | 货币 Currency |
| `exchange` | 交易所 Exchange |
| `contract` | 合约对象 Contract object |

### 订单状态 / Order Status

| 状态 Status | 说明 Description |
|------------|-----------------|
| `Initial` | 初始化 Created |
| `PendingSubmit` | 待提交 Pending submit |
| `Submitted` | 已提交 Submitted |
| `PartiallyFilled` | 部分成交 Partially filled |
| `Filled` | 全部成交 Fully filled |
| `Cancelled` | 已取消 Cancelled |
| `Inactive` | 已失效/拒绝 Rejected |
| `PendingCancel` | 待取消 Pending cancel |

```bash
# CLI
tigeropen trade order list                                   # 所有订单
tigeropen trade order list --status Filled                   # 已成交
tigeropen trade order list --status Submitted                # 持仓中
tigeropen trade order list --symbol AAPL --limit 10         # 按标的筛选
tigeropen trade order get ORDER_ID                           # 查单个订单
```

---

## 成交记录 / Transaction Records

```python
# 按标的查询 / By symbol
transactions = trade_client.get_transactions(
    symbol='AAPL', sec_type='STK',
    start_time=1625097600000, end_time=1625184000000)

# 按订单查询 / By order ID
transactions = trade_client.get_transactions(order_id='123456789',
                                              start_time=1625097600000,
                                              end_time=1625184000000)
# 分页 / Pagination
response = trade_client.get_transactions(symbol='AAPL', start_time=..., end_time=..., page_token='')
next_page = response.page_token  # 下一页令牌
```

**Transaction 对象属性 / Transaction Attributes**:

| 属性 Attribute | 说明 Description |
|---------------|-----------------|
| `id` | 成交记录ID Transaction ID |
| `account` | 交易账户ID Account ID |
| `order_id` | 关联订单ID Related order ID |
| `contract` | 合约对象 Contract object |
| `action` | BUY/SELL |
| `filled_quantity` | 成交数量 Filled quantity |
| `filled_quantity_scale` | 数量精度 Quantity scale (fractional shares) |
| `filled_price` | 成交价格 Filled price |
| `filled_amount` | 成交金额 Filled amount |
| `transacted_at` | 成交时间（毫秒时间戳） Transaction time (ms) |

```bash
# CLI (--symbol 为必填 / --symbol is required)
tigeropen trade transaction list --symbol AAPL
tigeropen trade transaction list --symbol AAPL --start-time 2025-01-01 --end-time 2025-06-30
tigeropen trade transaction list --symbol AAPL --limit 20
```

---

## 账户资产 / Account Assets
<!-- 当用户提到 "资产"、"资金"、"余额"、"购买力"、"assets"、"balance"、"buying power"、"cash" 时 -->

### 综合资产(推荐) / Prime Assets (Recommended)

```python
assets = trade_client.get_prime_assets(base_currency='USD', consolidated=True)
# ⚠️ 资产字段在 segments 字典中，不在 assets 对象上
# Fields are on segment objects, NOT directly on assets
# segments 键: 'S'=证券(Standard), 'G'=环球(Global), 'C'=商品/期货, 'F'=基金, 'D'=数字货币
seg = assets.segments.get('S') or assets.segments.get('G')
print(f"总资产 Net: {seg.net_liquidation}")
print(f"可用资金 Available: {seg.cash_available_for_trade}")
print(f"购买力 Buying Power: {seg.buying_power}")
print(f"现金 Cash: {seg.cash_balance}")
print(f"维持保证金 Margin: {seg.maintain_margin}")
print(f"未实现盈亏 Unrealized PnL: {seg.unrealized_pl}")
print(f"已实现盈亏 Realized PnL: {seg.realized_pl}")

# 每个 Segment.currency_assets 包含各币种明细
for currency, ca in seg.currency_assets.items():
    print(f"  {currency}: net={ca.net_liquidation}, forex_rate={ca.forex_rate}")
```

### Segment 关键字段 / Segment Key Fields

| 字段 Field | 说明 Description | APP 对应 |
|------------|-----------------|----------|
| `net_liquidation` | 总资产（净清算价值） Net liquidation | 总资产 |
| `cash_balance` | 现金额 Cash balance | 现金 |
| `cash_available_for_trade` | 可用资金（含保证金额度） Available funds | 可用资金 |
| `cash_available_for_withdrawal` | 可提现金额 Withdrawable cash | 可提金额 |
| `gross_position_value` | 证券总价值 Total position value | 市值 |
| `buying_power` | 购买力 Buying power | 购买力 |
| `unrealized_pl` | 浮动盈亏 Unrealized P&L | 未实现盈亏 |
| `realized_pl` | 已实现盈亏 Realized P&L | 已实现盈亏 |
| `init_margin` | 初始保证金 Initial margin | 初始保证金 |
| `maintain_margin` | 维持保证金 Maintenance margin | 维持保证金 |
| `overnight_margin` | 隔夜保证金 Overnight margin | - |
| `excess_liquidation` | 剩余流动性 Excess liquidity (= equity - maintain_margin) | 剩余流动性 |
| `leverage` | 杠杆倍数 Leverage | 杠杆 |
| `locked_funds` | 锁定资金 Locked funds | - |

> **多币种注意 / Multi-currency Note**: 持仓涉及多币种时（如同时持有 USD 和 HKD 标的），查询资产时指定 `base_currency` 统一币种口径，避免直接累加不同币种的数值。每个 Segment 的 `currency_assets` 包含各币种明细和 `forex_rate` 汇率。
> When positions span multiple currencies, specify `base_currency` in the query. Do not sum values across currencies directly. Use `currency_assets` for per-currency breakdown with `forex_rate`.

### 全球资产 / Global Assets

```python
assets_list = trade_client.get_assets()
for a in assets_list:
    print(f"Buying Power: {a.buying_power}")
    print(f"Maintenance Margin: {a.maintenance_margin_requirement}")
    print(f"Unrealized PnL: {a.unrealized_pnl}")
```

### 资产分析 / Asset Analytics

```python
analytics = trade_client.get_analytics_asset(
    start_date='2025-01-01', end_date='2025-06-30',
    seg_type='SEC',  # SEC(证券)/FUT(期货)/ALL
    currency='USD')
# 返回 summary(pnl, pnl_percentage, annualized_return) 和 history(每日资产/盈亏)
```

```bash
# CLI
tigeropen account assets                                      # 综合资产（推荐）
tigeropen account assets --currency USD                      # 指定计价货币
tigeropen account analytics --start-date 2025-01-01 --end-date 2025-12-31  # 资产分析
```

---

## 持仓 / Positions
<!-- 当用户提到 "持仓"、"仓位"、"我的股票"、"positions"、"holdings"、"portfolio" 时 -->

```python
from tigeropen.common.consts import SecurityType, Market

# 所有持仓 / All positions
positions = trade_client.get_positions()

# 按类型 / By security type
positions = trade_client.get_positions(sec_type=SecurityType.STK)
for p in positions:
    print(f"{p.contract.symbol}: qty={p.quantity}, cost={p.average_cost}, "
          f"price={p.market_price}, value={p.market_value}, "
          f"pnl={p.unrealized_pnl}, salable={p.salable_qty}")

# 按市场 / By market
us_pos = trade_client.get_positions(sec_type=SecurityType.STK, market=Market.US)
hk_pos = trade_client.get_positions(sec_type=SecurityType.STK, market=Market.HK)

# 按代码 / By symbol
aapl_pos = trade_client.get_positions(sec_type=SecurityType.STK, symbol='AAPL')

# 期权持仓 / Option positions
opt_pos = trade_client.get_positions(sec_type=SecurityType.OPT)

# 期货持仓 / Futures positions
fut_pos = trade_client.get_positions(sec_type=SecurityType.FUT)
```

### Position 对象属性 / Position Attributes

| 属性 Attribute | 说明 Description | APP 对应 |
|---------------|-----------------|----------|
| `contract` | 合约对象 Contract | - |
| `qty` / `position_qty` | 持仓数量 Quantity | 持有数量 |
| `average_cost` | 含佣金持仓均价 Average cost (incl. commission) | 平均成本 |
| `average_cost_by_average` | 均价成本（不含佣金） Average cost (excl. commission) | - |
| `market_price` | 最新价格 Market price | 现价 |
| `market_value` | 市值 Market value | 市值 |
| `unrealized_pnl` | 浮动盈亏 Unrealized P&L (FIFO) | 未实现盈亏 |
| `unrealized_pnl_by_average` | 均价成本浮动盈亏 Unrealized P&L (avg cost) | - |
| `unrealized_pnl_percent` | 浮动盈亏率 Unrealized P&L % | 盈亏百分比 |
| `realized_pnl` | 已实现盈亏 Realized P&L (FIFO) | 已实现盈亏 |
| `salable_qty` | 可卖数量 Salable quantity | 可卖 |
| `today_pnl` | 今日盈亏额 Today's P&L | 今日盈亏 |
| `today_pnl_percent` | 今日盈亏率 Today's P&L % | - |
| `ratio` | 持仓占比 Position ratio | - |
| `latest_price` | 最新成交价 Latest trade price | - |
| `currency` | 币种 Currency | - |

```bash
# CLI
tigeropen trade position list                                 # 所有股票持仓
tigeropen trade position list --sec-type opt                  # 期权持仓
tigeropen trade position list --sec-type fut                  # 期货持仓
tigeropen trade position list --market hk                     # 港股持仓
tigeropen trade position list --symbol AAPL                  # 指定标的
```

---

## 预估可交易数量 / Estimate Tradable Quantity

```python
# ⚠️ 必须先构建 order 对象再传入，不支持关键字参数展开
# Must build an order object first — does NOT accept keyword args
order = limit_order(account=client_config.account, contract=contract,
                    action='BUY', quantity=1, limit_price=150.0)
result = trade_client.get_estimate_tradable_quantity(order)
# 返回: tradable_quantity(可交易), financing_quantity(融资可交易), position_quantity(持仓数量)
```

## 资金划转 / Fund Transfer (Between Segments)

```python
# 查询可划转金额 / Query transferable amount
# ⚠️ 没有 to_segment 参数，只有 from_segment + currency
available = trade_client.get_segment_fund_available(
    from_segment='SEC', currency='USD')

# 划转 / Transfer
trade_client.transfer_segment_fund(
    from_segment='SEC', to_segment='FUT', amount=10000.0, currency='USD')

# 取消划转 / Cancel transfer
trade_client.cancel_segment_fund(id='transfer_id')

# 划转历史 / Transfer history
history = trade_client.get_segment_fund_history()
```

## 出入金记录 / Deposit/Withdrawal Records

```python
# ⚠️ get_funding_history 只接受 seg_type 参数，无 start_time/end_time/currency
# Use get_fund_details for filtered history with date range and currency
records = trade_client.get_funding_history(seg_type='SEC')  # SEC / FUT / ALL

# 带时间范围和币种过滤用 get_fund_details
details = trade_client.get_fund_details(
    seg_types=['SEC'],          # 必填 / required
    currency='USD',
    start_date='2025-01-01',
    end_date='2025-06-30',
    limit=100)
```

## 换汇下单 / Forex Order

```python
from tigeropen.common.consts import SegmentType

trade_client.place_forex_order(
    seg_type=SegmentType.SEC,  # SEC(证券板块) 或 FUT(期货板块)
    source_currency='USD', target_currency='HKD', source_amount=10000.0)
```

---

## 交易时间参考 / Trading Hours Reference

| 市场 Market | 时段 Session | 时间 Time (当地/Local) |
|-------------|-------------|----------------------|
| 美股 US | 盘前 Pre-market | 04:00-09:30 ET |
| | 常规 Regular | 09:30-16:00 ET |
| | 盘后 After-hours | 16:00-20:00 ET |
| | 夜盘 Overnight | 20:00-04:00 ET |
| 港股 HK | 竞价 Pre-opening | 09:00-09:30 |
| | 上午 Morning | 09:30-12:00 |
| | 下午 Afternoon | 13:00-16:00 |
| | 收盘竞价 Closing | 16:00-16:10 |
| 新加坡 SG | 全天 Full | 09:00-17:16 |

> 更多交易规则见 / More: https://docs.itigerup.com/docs/trade-rules

---

## 注意事项 / Notes

- `place_order` 返回成功仅表示提交成功，需查询确认成交。可检查 `order.id` 和 `order.status` / Success only means submitted; check `order.id` and `order.status`
- 市价单(MKT)和止损单(STP)不支持盘前盘后 / MKT and STP don't support pre/after-hours
- 同一证券不能同时持有多头和空头 / Cannot hold long and short simultaneously
- 下单前建议 `preview_order` / Use preview before placing real orders
- 交易佣金与 App 一致，无额外费用 / Trading fees same as app
- 已成交订单查询需指定时间范围，最多90天 / Filled orders require time range, max 90 days
- 港股下单数量须为 `lot_size` 的整数倍，可通过 `get_trade_metas` 查询 / HK orders must be multiples of `lot_size`
- `quantity_scale` 用于碎股精度控制 / `quantity_scale` controls fractional share precision
- `adjust_limit` 参数可用于价格自动修正 / `adjust_limit` for automatic price adjustment
