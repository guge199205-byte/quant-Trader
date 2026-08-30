
# Tiger Open API 期权 / Options Trading

> 中文 | English — 双语技能。Bilingual skill.
> 官方文档 Docs: https://docs.itigerup.com/docs/quote-option

## 期权操作工作流 / Option Workflow
<!-- 当用户提到 "期权"、"期权链"、"到期日"、"行权价"、"Greeks"、"Call"、"Put"、"看涨"、"看跌"、"option"、"option chain"、"expiration"、"strike" 时 -->

当用户提到期权时，按以下流程操作 / When user mentions options, follow this workflow:

### 查询期权 / Query Options

1. **查到期日 Get expirations**: `get_option_expirations()` → 获取可选到期日列表 / Get available expiration dates
2. **查期权链 Get chain**: `get_option_chain()` → 获取指定到期日的所有合约，可用 `OptionFilter` 筛选 / Get all contracts for a given expiry, filter with `OptionFilter`
3. **查行情 Get quotes**: `get_option_briefs()` → 获取期权实时行情和希腊字母 / Get real-time quotes and Greeks

### 期权交易 / Option Trading

1. **构造合约 Build contract**: `option_contract_by_symbol(symbol, expiry, strike, put_call, currency)`
2. **预览订单 Preview**: `preview_order()` → 确认保证金和佣金 / Confirm margin and commission
3. **下单 Place order**: `place_order()` → 期权数量单位为"张" / Option quantity is in "contracts"

### 港股期权特殊处理 / HK Option Special Handling

- 港股期权标的代码不同于正股 / HK option underlyings differ from stock codes: `00700` → `TCH`（腾讯）
- 使用 `get_option_symbols()` 查询港股期权代码映射 / Use `get_option_symbols()` for HK option symbol mapping

---

## 初始化 / Initialize

```python
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.quote.quote_client import QuoteClient
from tigeropen.trade.trade_client import TradeClient

client_config = TigerOpenClientConfig(props_path='/path/to/your/tiger_openapi_config.properties')
quote_client = QuoteClient(client_config=client_config)
trade_client = TradeClient(client_config=client_config)
```

---

## 期权到期日 / Option Expirations

```python
expirations = quote_client.get_option_expirations(symbols=['AAPL'], market='US')
# 返回 DataFrame / Returns DataFrame
# 列: symbol, option_symbol, date, timestamp, period_tag
# period_tag: "m"=月度期权(monthly), "w"=周期权(weekly)
# 也支持 symbols=['AAPL', 'TSLA'] 批量查询
```

## 期权链 / Option Chain

```python
from tigeropen.quote.domain.filter import OptionFilter

# 基础期权链 / Basic chain
chain = quote_client.get_option_chain(symbol='AAPL', expiry='2025-08-29', market='US')
# 返回 DataFrame，包含 call/put 的行权价、价格、成交量、持仓量等

# 带筛选和希腊字母 / With filters and Greeks
option_filter = OptionFilter(
    implied_volatility_min=0.3,
    implied_volatility_max=0.8,
    delta_min=0.2,
    delta_max=0.8,
    gamma_min=0.005,
    theta_max=-0.05,
    open_interest_min=100,
    volume_min=50,
    in_the_money=True
)
chain = quote_client.get_option_chain(
    symbol='AAPL', expiry='2025-08-29',
    option_filter=option_filter,
    return_greek_value=True,  # 返回 delta/gamma/theta/vega/rho
    market='US')
```

### OptionFilter 筛选字段 / Filter Fields

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `implied_volatility_min/max` | 隐含波动率范围 IV range |
| `delta_min/max` | Delta 范围 |
| `gamma_min/max` | Gamma 范围 |
| `theta_min/max` | Theta 范围 |
| `vega_min/max` | Vega 范围 |
| `open_interest_min/max` | 持仓量范围 Open interest |
| `volume_min/max` | 成交量范围 Volume |
| `in_the_money` | 是否实值 In the money (True/False) |

**get_option_chain 返回字段 / Return Fields** (pandas.DataFrame, 21列):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `identifier` | 期权完整代码 (e.g. `AAPL  250829C00150000`) |
| `symbol` | 标的代码 Underlying symbol |
| `expiry` | 到期日毫秒时间戳 Expiration timestamp (ms) |
| `strike` | 行权价 Strike price |
| `put_call` | `CALL`(看涨) / `PUT`(看跌) |
| `multiplier` | 乘数（美股通常100） Option multiplier |
| `ask_price` | 卖价 Ask price |
| `ask_size` | 卖量 |
| `bid_price` | 买价 Bid price |
| `bid_size` | 买量 |
| `pre_close` | 前收盘价 Previous close |
| `latest_price` | 最新价 Latest price |
| `volume` | 成交量 Volume |
| `open_interest` | 未平仓合约数 Open interest |
| `last_timestamp` | 最后交易时间戳（毫秒） Last trade time (ms) |
| `implied_vol` | 隐含波动率 Implied volatility |
| `delta` | Delta（需 `return_greek_value=True`）|
| `gamma` | Gamma |
| `theta` | Theta（每日时间价值损耗，通常为负值） |
| `vega` | Vega（波动率敏感度） |
| `rho` | Rho（利率敏感度） |

---

## 港股期权 / HK Options

港股期权标的代码与股票代码不同，需先映射。
HK option underlying symbols differ from stock codes; map first.

```python
# 获取映射 / Get mapping (e.g. 00700 -> TCH.HK)
# 返回 DataFrame: columns=['symbol'(期权代码), 'name', 'underlying_symbol'(正股代码)]
hk_symbols_df = quote_client.get_option_symbols(market='HK')
# 查找腾讯 / Find Tencent:
tencent = hk_symbols_df[hk_symbols_df['underlying_symbol'] == '00700'].iloc[0]
option_symbol = tencent['symbol']  # e.g. 'TCH.HK'

# 使用映射后的代码 / Use mapped symbol
expirations = quote_client.get_option_expirations(symbols=['TCH.HK'], market='HK')
chain = quote_client.get_option_chain(symbol='TCH.HK', expiry='2025-06-18', market='HK')
```

---

## 期权行情 / Option Quotes

```python
# 实时行情 / Real-time quotes
briefs = quote_client.get_option_briefs(identifiers=['AAPL  250829C00150000'])

# K线 / K-lines (支持周期: day, 1min, 5min, 30min, 60min)
bars = quote_client.get_option_bars(identifiers=['AAPL  250829C00150000'], period='day')
# 可选参数: sort_dir (SortDirection.ASC/DESC), limit, begin_time, end_time

# 深度行情 / Depth quotes
depth = quote_client.get_option_depth(identifiers=['AAPL  250829C00150000'], market='US')

# 逐笔成交 / Trade ticks
ticks = quote_client.get_option_trade_ticks(identifiers=['AAPL  250829C00150000'])

# 分时 / Timeline (支持 US 和 HK 市场 / Supports US and HK markets)
timeline = quote_client.get_option_timeline(identifiers=['AAPL  250829C00150000'])
# HK 期权: quote_client.get_option_timeline(identifiers=['TCH.HK250828C00610000'], market='HK')
```

**get_option_briefs 返回字段 / Return Fields** (pandas.DataFrame, 27列):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `identifier` | 期权完整代码 Full option identifier |
| `symbol` | 标的代码 Underlying symbol |
| `expiry` | 到期日毫秒时间戳 |
| `strike` | 行权价 Strike price |
| `put_call` | `CALL` / `PUT` |
| `multiplier` | 乘数 Option multiplier |
| `ask_price` | 卖价 Ask price |
| `ask_size` | 卖量 Ask size |
| `bid_price` | 买价 Bid price |
| `bid_size` | 买量 Bid size |
| `pre_close` | 前收价 Previous close |
| `latest_price` | 最新价 Latest price |
| `latest_time` | 最新交易时间 Latest trade time |
| `volume` | 成交量 Volume |
| `open_interest` | 未平仓数量 Open interest |
| `open` | 开盘价 Open price |
| `high` | 最高价 High price |
| `low` | 最低价 Low price |
| `change` | 价格变动 Price change |
| `volatility` | 历史波动率 Historical volatility |
| `rates_bonds` | 无风险利率 Risk-free interest rate |
| `mid_price` | 买卖中间价 Mid price (ask+bid)/2 |
| `mid_timestamp` | 中间价时间戳 Mid price timestamp (ms) |
| `mark_price` | 标记价格 Mark price |
| `mark_timestamp` | 标记价格时间戳 Mark price timestamp (ms) |
| `pre_mark_price` | 前标记价格 Previous mark price |
| `selling_return` | 卖出收益率 Selling return (for covered call/cash-secured put) |

### 期权代码格式 / Option Symbol Format

- 美股 US: `'AAPL  250829C00150000'`
  - 格式: `标的` + 空格填充至6位 + `YYMMDD` + `C/P` + 行权价*1000(8位)
  - Format: symbol padded to 6 chars + YYMMDD + C/P + strike*1000 (8 digits)
- 港股 HK: `'TCH.HK 230616C00550000'`
  - 注意使用映射后的代码 / Use mapped symbol

---

## 期权分析 / Option Analysis

```python
from tigeropen.common.consts import OptionAnalysisPeriod

# 基础用法(默认52周) / Basic usage (default 52-week)
analysis = quote_client.get_option_analysis(symbols=['AAPL'])

# 指定分析周期 / Specify analysis period
analysis = quote_client.get_option_analysis(
    symbols=['AAPL'],
    period=OptionAnalysisPeriod.TWENTY_SIX_WEEK)

# 每个标的不同周期 / Per-symbol periods
analysis = quote_client.get_option_analysis(
    symbols=[
        'AAPL',  # 使用默认周期
        {'symbol': 'TSLA', 'period': '26week'},  # 指定26周
    ])

# 返回 List[OptionAnalysis]，属性:
# - symbol: 标的代码
# - implied_vol_30_days: 30日隐含波动率
# - his_volatility: 历史波动率
# - iv_his_v_ratio: IV/HV 比率
# - call_put_ratio: 看涨/看跌比率
# - iv_metric: IVMetric 对象，包含:
#     - period: 分析周期
#     - percentile: IV 百分位
#     - rank: IV 排名
```

### OptionAnalysisPeriod 分析周期

| 周期 Period | 值 Value | 说明 Description |
|------------|---------|-----------------|
| `THREE_YEAR` | `3year` | 3年 / 3 years |
| `FIFTY_TWO_WEEK` | `52week` | 52周(1年，默认) / 52 weeks (default) |
| `TWENTY_SIX_WEEK` | `26week` | 26周(6个月) / 26 weeks |
| `THIRTEEN_WEEK` | `13week` | 13周(3个月) / 13 weeks |

---

## 期权合约 / Option Contracts

```python
from tigeropen.common.util.contract_utils import option_contract_by_symbol

# 本地构造 / Local construction
opt = option_contract_by_symbol(
    symbol='AAPL', expiry='20250829',
    strike=150.0, put_call='CALL', currency='USD')

# 远程获取 / Remote fetch
opt = trade_client.get_contract(
    symbol='AAPL', sec_type='OPT',
    expiry='20250829', strike=150.0, put_call='CALL')
```

---

## 单腿期权下单 / Single-leg Option Order

```python
from tigeropen.common.util.order_utils import limit_order

# 买入看涨期权 / Buy call
order = limit_order(account=client_config.account, contract=opt,
                    action='BUY', quantity=1, limit_price=5.0)
trade_client.place_order(order)

# 卖出看跌期权 / Sell put
put_contract = option_contract_by_symbol(
    symbol='AAPL', expiry='20250829', strike=140.0, put_call='PUT', currency='USD')
order = limit_order(account=client_config.account, contract=put_contract,
                    action='SELL', quantity=1, limit_price=3.0)
trade_client.place_order(order)
```

> 期权1张合约 = 100股标的。1 option contract = 100 shares of underlying.

---

## 多腿组合策略 / Multi-leg Combo Strategies

```python
from tigeropen.common.util.order_utils import combo_order, contract_leg
```

### 牛市看涨价差 / Bull Call Spread (VERTICAL)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=145.0, put_call='CALL', action='BUY', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=155.0, put_call='CALL', action='SELL', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='VERTICAL', action='BUY',
                    quantity=1, order_type='LMT', limit_price=3.0)
trade_client.place_order(order)
```

### 跨式策略 / Straddle (STRADDLE)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=150.0, put_call='CALL', action='BUY', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=150.0, put_call='PUT', action='BUY', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='STRADDLE', action='BUY',
                    quantity=1, order_type='LMT', limit_price=8.0)
trade_client.place_order(order)
```

### 宽跨式策略 / Strangle (STRANGLE)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=160.0, put_call='CALL', action='BUY', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=140.0, put_call='PUT', action='BUY', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='STRANGLE', action='BUY',
                    quantity=1, order_type='LMT', limit_price=5.0)
trade_client.place_order(order)
```

### 日历价差 / Calendar Spread (CALENDAR)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=150.0, put_call='CALL', action='SELL', ratio=1),  # 近月卖
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20251219',
                 strike=150.0, put_call='CALL', action='BUY', ratio=1),   # 远月买
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='CALENDAR', action='BUY',
                    quantity=1, order_type='LMT', limit_price=2.0)
trade_client.place_order(order)
```

### 对角线价差 / Diagonal Spread (DIAGONAL)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=155.0, put_call='CALL', action='SELL', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20251219',
                 strike=145.0, put_call='CALL', action='BUY', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='DIAGONAL', action='BUY',
                    quantity=1, order_type='LMT', limit_price=5.0)
trade_client.place_order(order)
```

### 备兑策略 / Covered Call (COVERED)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='STK', action='BUY', ratio=100),  # 买100股
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=160.0, put_call='CALL', action='SELL', ratio=1),     # 卖1张call
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='COVERED', action='BUY',
                    quantity=1, order_type='LMT', limit_price=145.0)
trade_client.place_order(order)
```

### 保护性看跌 / Protective Put (PROTECTIVE)

```python
legs = [
    contract_leg(symbol='AAPL', sec_type='STK', action='BUY', ratio=100),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=140.0, put_call='PUT', action='BUY', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='PROTECTIVE', action='BUY',
                    quantity=1, order_type='LMT', limit_price=152.0)
trade_client.place_order(order)
```

### 熊市看跌价差 / Bear Put Spread (VERTICAL)

方向性策略，预期股价下跌，有限盈利有限亏损。

```python
# 买高行权价Put，卖低行权价Put（净支出）
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=155.0, put_call='PUT', action='BUY', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=145.0, put_call='PUT', action='SELL', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='VERTICAL', action='BUY',
                    quantity=1, order_type='LMT', limit_price=4.0)
trade_client.place_order(order)
```

---

### 牛市看跌价差（信用价差）/ Bull Put Spread (Credit Spread)

卖出实值Put，买入更低行权价Put保护，收取权利金净信用，预期股价维持或上涨。

```python
# 卖高行权价Put，买低行权价Put（净收入信用）
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=145.0, put_call='PUT', action='SELL', ratio=1),  # 卖Put收信用
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=135.0, put_call='PUT', action='BUY', ratio=1),   # 买Put限风险
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='VERTICAL', action='SELL',
                    quantity=1, order_type='LMT', limit_price=2.50)
# limit_price 为净收入信用（正值），max_loss = (145-135)*100 - 250 = 750
trade_client.place_order(order)
```

---

### 熊市看涨价差（信用价差）/ Bear Call Spread (Credit Spread)

卖出虚值Call，买入更高行权价Call保护，预期股价维持或下跌。

```python
# 卖低行权价Call，买高行权价Call
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=165.0, put_call='CALL', action='SELL', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=175.0, put_call='CALL', action='BUY', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='VERTICAL', action='SELL',
                    quantity=1, order_type='LMT', limit_price=2.00)
trade_client.place_order(order)
```

---

### 铁鹰策略 / Iron Condor

同时卖出 Bull Put Spread + Bear Call Spread，适合区间震荡行情，收取双边权利金。

```python
# Iron Condor = Bull Put Spread + Bear Call Spread（4条腿，用 CUSTOM）
legs = [
    # Put spread（下方保护）
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=140.0, put_call='PUT', action='BUY', ratio=1),   # 买低Put
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=150.0, put_call='PUT', action='SELL', ratio=1),  # 卖高Put
    # Call spread（上方保护）
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=165.0, put_call='CALL', action='SELL', ratio=1), # 卖低Call
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=175.0, put_call='CALL', action='BUY', ratio=1),  # 买高Call
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='CUSTOM', action='SELL',
                    quantity=1, order_type='LMT', limit_price=3.50)
# limit_price = 净收入信用（正值）
# max_profit = 350（股价到期在 150-165 区间内）
# max_loss = (165-150)*100 - 350 = 1150（突破任一侧边界）
trade_client.place_order(order)
```

---

### 铁蝴蝶策略 / Iron Butterfly

卖出同行权价的 Straddle，再分别买入两侧保护，适合极低波动率预期，比 Iron Condor 收入更高但区间更窄。

```python
atm_strike = 155.0  # ATM 行权价（接近当前股价）
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=145.0, put_call='PUT', action='BUY', ratio=1),   # 买下方Put
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=atm_strike, put_call='PUT', action='SELL', ratio=1),  # 卖ATM Put
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=atm_strike, put_call='CALL', action='SELL', ratio=1), # 卖ATM Call
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=165.0, put_call='CALL', action='BUY', ratio=1),  # 买上方Call
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='CUSTOM', action='SELL',
                    quantity=1, order_type='LMT', limit_price=6.00)
trade_client.place_order(order)
```

---

### 穷人备兑开仓 / Poor Man's Covered Call (PMCC)

用深度实值的长期 LEAPS Call 替代持有正股，成本更低，结构类似 Covered Call。

```python
# 买入远期深度实值Call（LEAPS，delta ~0.7-0.9），替代持股
# 卖出近期虚值Call收取权利金
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20270117',  # 远期 ~1.5年
                 strike=120.0, put_call='CALL', action='BUY', ratio=1),  # 深度实值
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',  # 近期 ~1个月
                 strike=165.0, put_call='CALL', action='SELL', ratio=1), # 虚值
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='DIAGONAL', action='BUY',
                    quantity=1, order_type='LMT', limit_price=28.0)
# 注意: 近月Call行权价必须 > 远月Call行权价，否则存在最大亏损为负的风险
trade_client.place_order(order)
```

---

### 合成股票 / Synthetic Stock (SYNTHETIC)

买Call卖同行权价Put，经济效果等同于持有100股，资金占用更少。

```python
# 合成多头（等同于持股）: 买Call + 卖Put，同行权价同到期日
legs = [
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=150.0, put_call='CALL', action='BUY', ratio=1),
    contract_leg(symbol='AAPL', sec_type='OPT', expiry='20250829',
                 strike=150.0, put_call='PUT', action='SELL', ratio=1),
]
order = combo_order(account=client_config.account, legs=legs,
                    combo_type='SYNTHETIC', action='BUY',
                    quantity=1, order_type='LMT', limit_price=0.50)
# 合成空头: 两腿 action 均改为相反（卖Call+买Put）
trade_client.place_order(order)
```

---

### 常用策略对比速查 / Strategy Quick Reference

| 策略 Strategy | 方向 Bias | 盈亏结构 | 风险 Risk | 典型用途 Use Case |
|--------------|-----------|---------|-----------|-----------------|
| Bull Call Spread | 温和看涨 | 有限盈亏 | 有限（净支出） | 方向性，控制成本 |
| Bear Put Spread | 温和看跌 | 有限盈亏 | 有限（净支出） | 方向性下行保护 |
| Bull Put Spread | 温和看涨/中性 | 有限盈亏 | 有限（净收入） | 收取权利金，高胜率 |
| Bear Call Spread | 温和看跌/中性 | 有限盈亏 | 有限（净收入） | 收取权利金，高胜率 |
| Iron Condor | 中性区间震荡 | 有限盈亏 | 有限 | 低波动率环境，双边收费 |
| Iron Butterfly | 极度中性 | 有限盈亏 | 有限 | 极低波动预期，权利金最高 |
| Straddle（买） | 高波动预期 | 无限盈利 | 有限（净支出） | 财报/事件驱动 |
| Strangle（买） | 高波动预期 | 无限盈利 | 有限（更低成本） | 财报/事件驱动 |
| Covered Call | 温和看涨/持股 | 有限盈利 | 持股风险 | 持股增收 |
| Sell Put / CSP | 温和看涨 | 有限盈利 | 较大（行权买股） | 目标价买入+收费 |
| PMCC | 温和看涨 | 有限盈利 | 有限 | 低成本备兑替代 |
| Wheel Strategy | 温和看涨 | 稳定收租 | 行权买股 | 长期稳定增收 |
| Calendar Spread | 中性/轻方向 | 有限盈亏 | 有限 | 时间价值套利 |
| Diagonal Spread | 轻方向 | 有限盈亏 | 有限 | 方向性时间套利 |
| Synthetic Stock | 强方向 | 无限盈亏 | 较大 | 杠杆替代持股 |

---

### 组合策略类型总览 / Combo Strategy Types

| ComboType | 策略 Strategy | 说明 Description |
|-----------|--------------|-----------------|
| `VERTICAL` | 垂直价差 | 同到期日不同行权价 Same expiry, different strikes |
| `STRADDLE` | 跨式 | 同行权价同到期日 Call+Put Same strike & expiry |
| `STRANGLE` | 宽跨式 | 不同行权价同到期日 Call+Put Different strikes, same expiry |
| `CALENDAR` | 日历价差 | 同行权价不同到期日 Same strike, different expiries |
| `DIAGONAL` | 对角线价差 | 不同行权价不同到期日 Different strikes & expiries |
| `COVERED` | 备兑 | 持有股票+卖Call Long stock + short call |
| `PROTECTIVE` | 保护性 | 持有股票+买Put Long stock + long put |
| `SYNTHETIC` | 合成 | 合成多/空头 Synthetic long/short |
| `CUSTOM` | 自定义 | 自定义组合 Custom combination |

### contract_leg 参数 / contract_leg Parameters

| 参数 Parameter | 说明 Description | 必填 |
|---------------|-----------------|------|
| `symbol` | 标的代码 Symbol | ✅ |
| `sec_type` | `OPT` / `STK` | ✅ |
| `expiry` | 到期日 YYYYMMDD (期权) | OPT |
| `strike` | 行权价 Strike (期权) | OPT |
| `put_call` | `CALL` / `PUT` (期权) | OPT |
| `action` | `BUY` / `SELL` | ✅ |
| `ratio` | 比率 Ratio (STK用100, OPT用1) | ✅ |

---

## 查询期权持仓 / Query Option Positions

```python
from tigeropen.common.consts import SecurityType

opt_positions = trade_client.get_positions(sec_type=SecurityType.OPT)
for p in opt_positions:
    c = p.contract
    print(f"{c.symbol} {c.expiry} {c.strike} {c.put_call}: "
          f"qty={p.qty}, cost={p.average_cost}, value={p.market_value}, pnl={p.unrealized_pnl}")
```

---

## 期权计算工具 / Option Calculator Tools

> 需安装 `pip install quantlib==1.40`

### 期权定价与希腊字母 / Option Pricing & Greeks

```python
from tigeropen.examples.option_helpers.helpers import (
    FDAmericanDividendOptionHelper,   # 美式期权(美股/港股/ETF) American options
    FDEuropeanDividendOptionHelper,   # 欧式期权(指数期权) European options
)

# 计算期权价格 / Calculate option price
helper = FDAmericanDividendOptionHelper(
    option_type='CALL',    # CALL/PUT
    expiry='2025-08-29',   # 到期日
    settlement='2025-03-18',  # 结算日
    strike=150.0,          # 行权价
    underlying=155.0,      # 标的价格
    rate=0.05,             # 无风险利率
    volatility=0.25,       # 波动率
)
result = helper.calculate()
# result: npv(期权价格), delta, gamma, theta, vega, rho

# 从期权价格反算隐含波动率 / Calculate implied volatility from price
helper = FDAmericanDividendOptionHelper(
    option_type='CALL', expiry='2025-08-29', settlement='2025-03-18',
    strike=150.0, underlying=155.0, rate=0.05,
    option_price=8.5,  # 期权市场价格
)
iv = helper.implied_volatility()
```

### 期权指标计算器 / Option Metrics Calculator

```python
from tigeropen.examples.option_helpers.util import OptionUtil

option_util = OptionUtil(client_config=client_config)

# 计算期权指标 / Calculate option metrics
# 输入期权代码，自动查询市场数据计算
metrics = option_util.calculate('TSLA 260220C00385000')
# 返回: Greeks, profit_probability(盈利概率), annualized_return_for_selling(卖出年化收益率), leverage_ratio(杠杆比率)
```

---

## 复杂业务场景 / Advanced Strategy Scenarios

### Wheel Strategy（车轮策略）概述

Wheel Strategy = **Sell Put → 被行权后持有股票 → Sell Covered Call → 反复循环**

```
阶段1: Sell Cash-Secured Put
  └── 若未被行权 → 收保证金，重复卖Put
  └── 若被行权   → 以行权价买入股票
         |
阶段2: Sell Covered Call
  └── 若未被行权 → 收权利金，重复卖Call
  └── 若被行权   → 以行权价卖出股票，回到阶段1
```

**策略目标**: 持续通过卖出期权收取权利金，降低实际持股成本。

---

### 场景一：扫描股票池的 Sell Put 机会

扫描一组股票，找出近期到期、Delta 合适、权利金收益率高的 Put 合约。

```python
import pandas as pd
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.quote.quote_client import QuoteClient
from tigeropen.quote.domain.filter import OptionFilter

client_config = TigerOpenClientConfig(props_path='/path/to/config/')
quote_client = QuoteClient(client_config=client_config)

# 1. 定义股票池
watchlist = ['AAPL', 'MSFT', 'TSLA', 'NVDA', 'AMD']

# 2. 筛选参数（根据策略调整）
DELTA_MIN = -0.35     # Put delta 为负，-0.35 表示约 35% 被行权概率
DELTA_MAX = -0.15     # 偏保守，目标 delta 在 -0.15 ~ -0.35
IV_MIN = 0.20         # 过滤低波动率标的（权利金太少）
OI_MIN = 100          # 最小持仓量，保证流动性
VOLUME_MIN = 10
DAYS_TO_EXPIRY = 30   # 目标到期天数（21-45天最佳）

# 3. 查询各标的的近期到期日
def get_target_expiry(symbol, target_days=DAYS_TO_EXPIRY):
    """找距当前最接近目标天数的到期日"""
    import datetime
    expirations = quote_client.get_option_expirations(symbols=[symbol], market='US')
    today = datetime.date.today()
    target_date = today + datetime.timedelta(days=target_days)

    # 筛选月度期权（period_tag='m'），找最近的
    monthly = expirations[expirations['period_tag'] == 'm'].copy()
    monthly['date'] = pd.to_datetime(monthly['date']).dt.date
    monthly['diff'] = (monthly['date'] - target_date).abs()

    if monthly.empty:
        return None
    best = monthly.loc[monthly['diff'].idxmin()]
    return str(best['date'])

# 4. 扫描每个标的
opportunities = []

for symbol in watchlist:
    try:
        expiry = get_target_expiry(symbol)
        if not expiry:
            continue

        # 查询 Put 期权链，按 delta 筛选
        option_filter = OptionFilter(
            delta_min=DELTA_MIN,
            delta_max=DELTA_MAX,
            implied_volatility_min=IV_MIN,
            open_interest_min=OI_MIN,
            volume_min=VOLUME_MIN,
        )
        chain = quote_client.get_option_chain(
            symbol=symbol,
            expiry=expiry,
            option_filter=option_filter,
            return_greek_value=True,
            market='US'
        )

        if chain is None or chain.empty:
            continue

        # 只取 PUT
        puts = chain[chain['put_call'] == 'PUT'].copy()
        if puts.empty:
            continue

        # 计算关键指标
        puts['mid_price'] = (puts['bid_price'] + puts['ask_price']) / 2
        puts['bid_ask_spread'] = puts['ask_price'] - puts['bid_price']
        puts['bid_ask_ratio'] = puts['bid_ask_spread'] / puts['mid_price']
        # 年化权利金收益率 = 权利金/行权价 * (365/天数)
        import datetime
        today = datetime.date.today()
        expiry_date = datetime.date.fromisoformat(expiry)
        days_left = (expiry_date - today).days
        puts['premium_yield'] = (puts['bid_price'] / puts['strike']) * (365 / max(days_left, 1))

        # 流动性过滤: bid/ask 价差不超过 mid 的 30%
        liquid = puts[puts['bid_ask_ratio'] < 0.30]

        if liquid.empty:
            continue

        # 按年化收益率排序，取最优
        best = liquid.sort_values('premium_yield', ascending=False).head(3)
        best['underlying'] = symbol
        best['expiry_date'] = expiry
        best['days_left'] = days_left
        opportunities.append(best)

    except Exception as e:
        print(f"Skip {symbol}: {e}")
        continue

# 5. 汇总结果
if opportunities:
    result = pd.concat(opportunities, ignore_index=True)
    display_cols = [
        'underlying', 'expiry_date', 'days_left', 'strike', 'delta',
        'bid_price', 'ask_price', 'mid_price', 'implied_vol',
        'open_interest', 'volume', 'premium_yield'
    ]
    result = result[display_cols].sort_values('premium_yield', ascending=False)
    print(result.to_string(index=False))
else:
    print("No opportunities found.")
```

**输出示例 / Sample Output**:

```
underlying expiry_date  days_left  strike  delta  bid_price  ask_price  mid_price  implied_vol  open_interest  volume  premium_yield
      NVDA  2025-05-16         38   800.0  -0.28       8.50      9.00       8.75       0.420          1523     312          0.418
      TSLA  2025-05-16         38   220.0  -0.25       4.20      4.50       4.35       0.510           892     245          0.389
      AAPL  2025-05-16         38   170.0  -0.22       1.80      2.00       1.90       0.280          2104     567          0.215
```

---

### 场景二：执行 Sell Put 下单

从扫描结果中选择目标合约并下单：

```python
from tigeropen.common.util.contract_utils import option_contract_by_symbol
from tigeropen.common.util.order_utils import limit_order
from tigeropen.trade.trade_client import TradeClient

trade_client = TradeClient(client_config=client_config)

# ⚠️ 默认使用模拟账户，实盘需用户确认
account = client_config.account

# 选择目标 Put 合约
symbol = 'NVDA'
expiry = '20250516'   # YYYYMMDD 格式
strike = 800.0
limit_price = 8.50    # 以 bid 价卖出（保守），或 mid 价（激进）
quantity = 1          # 1张 = 100股

# 构造合约
put_contract = option_contract_by_symbol(
    symbol=symbol,
    expiry=expiry,
    strike=strike,
    put_call='PUT',
    currency='USD'
)

# 卖出 Put
order = limit_order(
    account=account,
    contract=put_contract,
    action='SELL',
    quantity=quantity,
    limit_price=limit_price
)

# 预览（建议先预览确认保证金）
preview = trade_client.preview_order(order)
print(f"预估保证金: {preview.init_margin}")
print(f"预估佣金: {preview.commission}")

# 确认后下单
result = trade_client.place_order(order)
print(f"订单ID: {result.id}, 状态: {result.status}")
```

---

### 场景三：监控持仓并执行 Covered Call（Wheel 第二阶段）

当 Sell Put 被行权，持有正股后，自动寻找 Covered Call 机会：

```python
from tigeropen.common.consts import SecurityType

# 1. 查询当前期权持仓（监控被行权的 Put）
opt_positions = trade_client.get_positions(sec_type=SecurityType.OPT)
assigned_symbols = []

for pos in opt_positions:
    c = pos.contract
    # Put 被行权后会消失，但持有对应的正股
    print(f"期权持仓: {c.symbol} {c.expiry} {c.strike} {c.put_call} qty={pos.qty}")

# 2. 查询正股持仓（被行权后的股票）
stk_positions = trade_client.get_positions(sec_type=SecurityType.STK)

for pos in stk_positions:
    symbol = pos.contract.symbol
    qty = pos.qty
    avg_cost = pos.average_cost

    if qty < 100:  # 至少持有100股才能卖1张Covered Call
        continue

    max_contracts = qty // 100  # 可卖的最大张数

    print(f"\n{symbol}: 持有 {qty}股, 均价 ${avg_cost:.2f}, 可卖 {max_contracts} 张 Covered Call")

    # 3. 查询 OTM Call 期权（行权价 > 当前价格，略高于持仓成本）
    expiry = get_target_expiry(symbol)  # 使用前面定义的函数
    if not expiry:
        continue

    call_filter = OptionFilter(
        delta_min=0.20,   # Call delta 0.20-0.35 （约20-35%被行权概率）
        delta_max=0.35,
        open_interest_min=50,
        volume_min=5,
    )
    chain = quote_client.get_option_chain(
        symbol=symbol, expiry=expiry,
        option_filter=call_filter,
        return_greek_value=True, market='US'
    )

    if chain is None or chain.empty:
        continue

    calls = chain[chain['put_call'] == 'CALL'].copy()
    if calls.empty:
        continue

    # 筛选行权价高于均价（避免亏损卖出股票）
    calls = calls[calls['strike'] > avg_cost]

    if calls.empty:
        print(f"  {symbol}: 无合适的 Covered Call 合约（所有行权价低于持仓均价）")
        continue

    # 按权利金收益率排序
    calls['mid_price'] = (calls['bid_price'] + calls['ask_price']) / 2
    calls = calls.sort_values('mid_price', ascending=False)

    best_call = calls.iloc[0]
    print(f"  推荐 Covered Call: 行权价 ${best_call['strike']:.1f}, "
          f"Delta={best_call['delta']:.2f}, "
          f"权利金 bid=${best_call['bid_price']:.2f}")
```

---

### Wheel Strategy 关键参数选择指南

| 参数 | 保守型 | 平衡型 | 激进型 |
|------|--------|--------|--------|
| Put Delta | -0.15 ~ -0.20 | -0.25 ~ -0.30 | -0.35 ~ -0.40 |
| Call Delta | 0.20 ~ 0.25 | 0.25 ~ 0.35 | 0.35 ~ 0.45 |
| 到期天数 DTE | 45-60 天 | 30-45 天 | 14-30 天 |
| IV 要求 | IV Rank > 30% | IV Rank > 20% | 无特殊要求 |
| 流动性 | OI > 500 | OI > 100 | OI > 50 |

**止损原则**: 当期权市值达到权利金的 2-3 倍时考虑平仓止损。

```python
# 检查是否需要止损
for pos in opt_positions:
    if pos.qty < 0:  # 持有空头期权（卖出方）
        entry_credit = abs(pos.average_cost)  # 收到的权利金（每股）
        current_price = pos.market_price       # 当前期权价格
        loss_ratio = current_price / entry_credit

        if loss_ratio > 2.0:  # 亏损超过2倍权利金
            c = pos.contract
            print(f"⚠️ 止损警告: {c.symbol} {c.strike}{c.put_call} "
                  f"当前亏损 {loss_ratio:.1f}x 权利金，考虑平仓")
```

---

## 注意事项 / Notes

- 港股期权需 `get_option_symbols` 获取代码映射 / HK options need symbol mapping
- 期权每张合约通常代表100股标的 / Each contract = 100 shares
- 希腊字母通过 `return_greek_value=True` 获取 / Greeks via `return_greek_value=True`
- 组合策略下单时所有 leg 标的必须一致 / All legs must share same underlying
- 期权行情需要期权行情权限 / Option quotes require option quote permission
- 期权计算工具需安装 quantlib / Calculator tools require `pip install quantlib==1.40`
- 更多期权知识见 / More: https://docs.itigerup.com/docs/quote-option
