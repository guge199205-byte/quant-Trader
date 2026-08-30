
# Tiger Open API 行情数据 / Market Data

> 中文 | English — 双语技能，代码示例通用。Bilingual skill with shared code examples.
> 官方文档 Docs: https://docs.itigerup.com/docs/quote-stock

## 初始化 / Initialize

```python
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.quote.quote_client import QuoteClient

client_config = TigerOpenClientConfig(props_path='/path/to/your/tiger_openapi_config.properties')
quote_client = QuoteClient(client_config=client_config)
# is_grab_permission 默认 True，自动抢占行情权限 / auto-grabs quote permissions
# 多设备同账号仅最后抢占的设备收到行情 / last device to grab gets data
```

> 建议模块级创建一次并复用。Create QuoteClient once and reuse.

---

## 市场状态 / Market Status

```python
from tigeropen.common.consts import Market

status = quote_client.get_market_status(Market.US)  # US, HK, CN, ALL
for s in status:
    print(f"{s.market}: status={s.trading_status}, open={s.open_time}")
# MarketStatus 属性: market, trading_status, status, open_time
# trading_status: NOT_YET_OPEN, PRE_HOUR_TRADING, TRADING, MIDDLE_CLOSE, POST_HOUR_TRADING, CLOSING, EARLY_CLOSING
```

```bash
# CLI
tigeropen quote market-status          # 所有市场 / All markets
tigeropen quote market-status --market US
tigeropen quote market-status --market HK
```

## 交易日历 / Trading Calendar

```python
cal = quote_client.get_trading_calendar(market=Market.US, begin_date='2025-01-01', end_date='2025-12-31')
# 返回 list[dict] / Returns list of dicts
# 每项: {'date': '2025-01-02', 'type': 'TRADING'}
# type: TRADING(交易日) / NON_TRADING(非交易日)
```

---

## 股票代码与信息 / Stock Symbols & Info

```python
# 获取所有代码 / Get all symbols
symbols = quote_client.get_symbols(market=Market.US)  # 返回 symbol 列表

# 获取代码和名称 / Get symbols with names
names = quote_client.get_symbol_names(market=Market.HK, lang=Language.zh_CN)
# 返回 DataFrame: symbol, name

# 股票详情 / Stock details
details = quote_client.get_stock_details(symbols=['AAPL'])
# 属性: symbol, market, exchange, sec_type, listing_date, float_shares, eps, adr_rate, etf
```

## 股票基本面 / Stock Fundamentals

```python
# 基本面指标 / Fundamental indicators
fundamentals = quote_client.get_stock_fundamental(symbols=['AAPL', 'TSLA'], market='US')
# 返回 pandas.DataFrame
```

**get_stock_fundamental 返回字段 / Return Fields** (pandas.DataFrame):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `symbol` | 股票代码 |
| `roe` | 净资产收益率 Return on equity |
| `roa` | 资产收益率 Return on assets |
| `pb_rate` | 市净率 P/B ratio |
| `ps_rate` | 市销率 P/S ratio |
| `divide_rate` | 股息率 Dividend yield |
| `week52_high` | 52周最高价 52-week high |
| `week52_low` | 52周最低价 52-week low |
| `ttm_eps` | TTM每股收益 TTM earnings per share |
| `lyr_eps` | 上年每股收益 Last year EPS |
| `volume_ratio` | 量比 Volume ratio |
| `turnover_rate` | 换手率 Turnover rate |
| `ttm_pe_rate` | TTM市盈率 TTM P/E ratio |
| `lyr_pe_rate` | 上年市盈率 Last year P/E ratio |
| `market_cap` | 总市值 Market capitalization |
| `float_market_cap` | 流通市值 Float market cap |

> ⚠️ 注意：字段名与 `get_financial_daily` 不同。`get_stock_fundamental` 用 `ttm_pe_rate`，而 `get_financial_daily` 用 `pe_ttm`。

---

## 实时行情 / Real-time Quotes

### 股票 / Stocks

```python
briefs = quote_client.get_stock_briefs(['AAPL', 'TSLA', '00700'])
# 返回 pandas.DataFrame / Returns pandas.DataFrame
for _, row in briefs.iterrows():
    print(f"{row['symbol']}: price={row['latest_price']}, volume={row['volume']}, "
          f"open={row['open']}, high={row['high']}, low={row['low']}, pre_close={row['pre_close']}")

# 含盘前盘后 / Include pre/after-hours
briefs = quote_client.get_stock_briefs(['AAPL'], include_hour_trading=True)

# 延迟行情(无权限时) / Delayed quotes (when no permission)
delayed = quote_client.get_stock_delay_briefs(symbols=['AAPL'])
```

**get_stock_briefs 返回字段 / Return Fields** (pandas.DataFrame):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `symbol` | 股票代码 |
| `open` | 开盘价 Opening price |
| `high` | 最高价 Highest price |
| `low` | 最低价 Lowest price |
| `close` | 收盘价 Closing price |
| `pre_close` | 前收价 Previous close |
| `adj_pre_close` | 复权过的前收价 Adjusted previous close |
| `latest_price` | 最新价 Latest price |
| `latest_time` | 最新成交时间（毫秒时间戳） Latest trade time (ms) |
| `volume` | 成交量 Volume |
| `ask_price` | 卖一价 Ask price |
| `ask_size` | 卖一量 Ask size |
| `bid_price` | 买一价 Bid price |
| `bid_size` | 买一量 Bid size |
| `status` | 交易状态: `NORMAL`(正常) / `HALTED`(停牌) / `DELIST`(退市) / `NEW`(新股) / `ALTER`(变更) / `UNKNOWN` |
| `hour_trading_tag` | 盘前盘后标记（`Pre-Mkt`/`Post-Mkt`） |
| `hour_trading_latest_price` | 盘前盘后最新价 |
| `hour_trading_pre_close` | 盘前盘后前收价 |
| `hour_trading_latest_time` | 盘前盘后最新成交时间 |
| `hour_trading_volume` | 盘前盘后成交量 |
| `hour_trading_timestamp` | 盘前盘后时间戳（毫秒） |

> 盘前盘后字段仅在 `include_hour_trading=True` 时有数据。
```

```bash
# CLI (需要行情权限 / requires quote permission)
tigeropen quote briefs AAPL TSLA
tigeropen quote briefs AAPL --hour-trading   # 含盘前盘后 / include pre/after-hours
```

### 夜盘行情 / Overnight Quotes

```python
overnight = quote_client.get_quote_overnight(symbols=['AAPL', 'TSLA'])
# 返回夜盘交易时段行情 / Returns overnight trading session quotes
```

### 交易元数据 / Trading Metadata

```python
metas = quote_client.get_trade_metas(symbols=['AAPL', '00700'])
# 属性: symbol, market, sec_type, lot_size(每手股数), min_tick(最小价格变动), spread_scale
```

---

## K线数据 / K-line (Candlestick) Data

```python
from tigeropen.common.consts import BarPeriod, QuoteRight, TradingSession

# 日K / Daily (返回 DataFrame: symbol, time, open, high, low, close, volume, amount)
bars = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY, limit=60)

# 分钟K / Minute
bars_5m = quote_client.get_bars(['AAPL'], period=BarPeriod.FIVE_MINUTES, limit=100)

# 时间范围 / Time range
bars = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY,
                              begin_time='2025-01-01', end_time='2025-06-30')

# 复权 / Adjustment: BR(前复权/forward, default), NR(不复权/none)
bars_nr = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY, right=QuoteRight.NR)

# 多股票 / Multiple symbols
bars = quote_client.get_bars(['AAPL', 'TSLA', 'GOOG'], period=BarPeriod.DAY, limit=30)

# 指定日期的分钟K线 / Minute K-lines for specific date
bars = quote_client.get_bars(['AAPL'], period=BarPeriod.ONE_MINUTE, date='20250618')

# 指定交易时段 / Specific trading session (夜盘 overnight)
bars = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY,
                              trade_session=TradingSession.OverNight)

# 含基本面数据(PE/换手率) / With fundamental data (PE/turnover)
bars = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY, with_fundamental=True)

# 分页(单个标的，使用 page_token) / Pagination (single symbol, via page_token)
bars = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY, limit=100)
next_token = bars['next_page_token'].iloc[0]
if next_token:
    bars_next = quote_client.get_bars(['AAPL'], period=BarPeriod.DAY, limit=100, page_token=next_token)
```

### 分页获取大量K线 / Paginated K-line for Large Datasets

```python
# 自动分页获取(注意: symbol 为单个字符串) / Auto-paginated fetch (note: symbol is a single string)
bars = quote_client.get_bars_by_page(symbol='AAPL', period=BarPeriod.DAY,
                                      begin_time='2020-01-01', end_time='2025-01-01',
                                      total=2000)
# 也支持 trade_session 和 with_fundamental 参数
# Also supports trade_session and with_fundamental params
```

**get_bars 返回字段 / Return Fields** (pandas.DataFrame):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `symbol` | 股票代码 |
| `time` | K线时间（毫秒时间戳） Timestamp in milliseconds |
| `open` | 开盘价 Opening price |
| `high` | 最高价 Highest price |
| `low` | 最低价 Lowest price |
| `close` | 收盘价 Closing price |
| `volume` | 成交量 Trading volume |
| `amount` | 成交额 Trading amount |
| `next_page_token` | 下一页令牌（单标的分页时使用） Next page token (single symbol pagination) |

> `with_fundamental=True` 时额外返回 `pe_ttm`、`turnover_rate` 等字段。

**K线周期 / Bar Periods**: `day/week/month/year/1min/3min/5min/10min/15min/30min/45min/60min/2hour/3hour/4hour/6hour`

```bash
# CLI
tigeropen quote bars AAPL                               # 日K，251根
tigeropen quote bars AAPL --period 5min --limit 100    # 5分钟K线
tigeropen quote bars AAPL --begin-time 2026-01-01 --end-time 2026-03-31  # 时间范围
tigeropen quote bars AAPL TSLA                   # 多标的
```

---

## 深度行情 / Depth Quote (Level 2)

```python
# 美股深度 / US depth (需要 L2 权限 / requires L2 permission)
depth = quote_client.get_depth_quote(['AAPL'], market=Market.US)
# 返回 asks(卖盘) 和 bids(买盘)，每档: price, volume, order_count
# US: 最多40档 / up to 40 levels

# 港股深度 / HK depth
depth = quote_client.get_depth_quote(['00700'], market=Market.HK)
# HK: 最多10档 / up to 10 levels
```

**get_depth_quote 返回结构 / Return Structure**:

单个标的时返回 dict / Single symbol returns a dict:
```python
{
    'symbol': 'AAPL',
    'asks': [(ask_price1, ask_volume1, order_count1), (ask_price2, ...)],  # 卖盘，从低到高
    'bids': [(bid_price1, bid_volume1, order_count1), (bid_price2, ...)],  # 买盘，从高到低
}

# 访问示例 / Access example:
asks = depth['asks']  # [(226.50, 300, 2), (226.55, 500, 1), ...]
bids = depth['bids']  # [(226.45, 400, 3), (226.40, 200, 1), ...]
for price, volume, order_count in asks[:5]:
    print(f"Ask: price={price}, vol={volume}, orders={order_count}")
```

多个标的时返回嵌套 dict / Multiple symbols return nested dict:
```python
{
    'AAPL': {'symbol': 'AAPL', 'asks': [...], 'bids': [...]},
    'TSLA': {'symbol': 'TSLA', 'asks': [...], 'bids': [...]},
}
```

```bash
# CLI (--market 为必选参数 / --market is required)
tigeropen quote depth AAPL --market US
tigeropen quote depth 00700 --market HK
```

## 逐笔成交 / Trade Ticks

```python
from tigeropen.common.consts import TradingSession

ticks = quote_client.get_trade_ticks(symbols=['AAPL'], limit=50)
# 返回 DataFrame: symbol, time, price, volume, direction(+/-), index

# 指定交易时段 / Specific trading session
ticks = quote_client.get_trade_ticks(symbols=['AAPL'], limit=100,
                                      trade_session=TradingSession.Regular)

# 分页(使用 begin_index/end_index) / Pagination via begin_index/end_index
ticks = quote_client.get_trade_ticks(symbols=['AAPL'], begin_index=0, end_index=100)
```

```bash
# CLI
tigeropen quote ticks AAPL
tigeropen quote ticks AAPL --limit 50
```

## 分时数据 / Timeline (Intraday)

```python
# 当日分时 / Today's timeline
timeline = quote_client.get_timeline(['AAPL'], include_hour_trading=True)
# 返回 DataFrame: symbol, time, price, avg_price, pre_close, volume, trade_session

# 指定交易时段 / Specific trading session
timeline = quote_client.get_timeline(['AAPL'], trade_session=TradingSession.OverNight)

# 历史某日分时 / Historical date
timeline = quote_client.get_timeline_history(['AAPL'], date='2025-06-18')
# 也支持 trade_session 参数 / Also supports trade_session param
```

```bash
# CLI
tigeropen quote timeline AAPL                     # 今日分时
tigeropen quote timeline AAPL --date 2026-03-20   # 历史某日
```

---

## 资金流向 / Capital Flow

```python
from tigeropen.common.consts import CapitalPeriod

# 资金流向 / Capital flow (使用 CapitalPeriod 枚举)
flow = quote_client.get_capital_flow(symbol='AAPL', market='US',
                                      period=CapitalPeriod.DAY,
                                      begin_time='2025-01-01', end_time='2025-06-30')
# 返回 DataFrame: time, timestamp, net_inflow, symbol, period
# CapitalPeriod 可选值: INTRADAY, DAY, WEEK, MONTH, YEAR, QUARTER, HALFAYEAR

# 资金分布 / Capital distribution
distribution = quote_client.get_capital_distribution(symbol='AAPL', market='US')
# 属性: net_inflow, super_large_net_inflow, large/middle/small_net_inflow
```

```bash
# CLI
tigeropen quote capital flow AAPL --market US --period day
tigeropen quote capital distribution AAPL --market US
```

## 港股经纪商 / HK Broker Data

```python
from tigeropen.common.consts import Market, SortDirection

# 经纪商席位 / Broker seats (返回 StockBroker 对象)
broker = quote_client.get_stock_broker('00700', limit=40)
# broker.bid_broker: 买方经纪商列表(LevelBroker: level, price, broker_count, broker)
# broker.ask_broker: 卖方经纪商列表

# 经纪商持仓(CCASS) / Broker holdings (CCASS) - 独立方法
# market/order_by/direction/limit/page — 无 symbol 参数 / no symbol parameter
hold = quote_client.get_broker_hold(market=Market.HK, order_by='marketValue',
                                    direction=SortDirection.DESC, limit=40)
```

## 热门交易排行 / Hot Trading Rank

```python
rank = quote_client.get_trade_rank(market='US')
# 返回热门交易标的排行
```

---

## 基本面数据 / Fundamental Data

### 每日财务指标 / Daily Financial Metrics

```python
financial = quote_client.get_financial_daily(
    symbols=['AAPL'],
    fields=['pe_ttm', 'pb', 'ps_ttm', 'market_value', 'shares_outstanding', 'market_capitalization'],
    begin_date='2025-01-01', end_date='2025-06-30')
# 返回 DataFrame: symbol, date, 及所选字段
# 可选字段: market_value, pe_ttm, pb, ps_ttm, pcf_ttm, market_capitalization,
#          shares_outstanding, total_share, eps_ttm, dividend_ttm, dividend_rate_ttm
```

### 财务报告 / Financial Reports

```python
report = quote_client.get_financial_report(
    symbols=['AAPL'],
    fields=['total_revenue', 'net_income', 'eps_diluted', 'gross_profit'],
    period_type='LTM')  # Annual/Quarterly/LTM/CumulativeQuarterly
# 可选字段类别:
# Income: total_revenue, cost_of_revenue, gross_profit, operating_income, net_income, eps_diluted, ebitda...
# Balance: total_assets, total_liabilities, total_equity, cash_and_equivalents, total_debt...
# CashFlow: operating_cash_flow, capital_expenditure, free_cash_flow, dividends_paid...
```

```bash
# CLI
tigeropen quote fundamental financial AAPL --fields total_revenue,net_income
tigeropen quote fundamental financial AAPL --period-type QUARTERLY
tigeropen quote fundamental financial AAPL --begin-date 2024-01-01 --end-date 2026-01-01
```

### 分红 / Dividends

```python
dividends = quote_client.get_corporate_dividend(
    symbols=['AAPL'], market='US',
    begin_date='2024-01-01', end_date='2025-12-31')
# 属性: symbol, announce_date, record_date, pay_date, amount, currency
```

```bash
# CLI
tigeropen quote fundamental dividend AAPL --begin-date 2024-01-01 --end-date 2026-01-01
```

### 拆合股 / Stock Splits

```python
splits = quote_client.get_corporate_split(
    symbols=['AAPL'], market='US',
    begin_date='2020-01-01', end_date='2025-12-31')
# 属性: symbol, execute_date, from_factor, to_factor
```

### 代码变更 / Symbol Change（SDK 3.7.0+）

```python
changes = quote_client.get_corporate_symbol_change(
    symbols=['META'], market='US',
    begin_date='2022-01-01', end_date='2023-01-01')
# 返回 pandas.DataFrame
```

### 退市事件 / Delisting（SDK 3.7.0+）

```python
delistings = quote_client.get_corporate_delisting(
    symbols=['AAPL'], market='US',
    begin_date='2024-01-01', end_date='2026-01-01')
```

### 新股上市 / IPO（SDK 3.7.0+）

```python
ipos = quote_client.get_corporate_ipo(
    symbols=['AAPL'], market='US',
    begin_date='2024-01-01', end_date='2026-01-01')
```

三个方法签名一致：`(symbols, market, begin_date, end_date, timezone=None)`，
均返回 `pandas.DataFrame`。`CorporateActionType` 新增 `SYMBOL_CHANGE`、`DELISTING`、`IPO`。
All three share the signature `(symbols, market, begin_date, end_date, timezone=None)`.

### 财报日历 / Earnings Calendar

```python
earnings = quote_client.get_corporate_earnings_calendar(
    market='US', begin_date='2025-06-01', end_date='2025-06-30')
# 属性: symbol, name, market, earnings_date, timing(BMO/AMC)
```

```bash
# CLI
tigeropen quote fundamental earnings --begin-date 2026-03-01 --end-date 2026-03-31
tigeropen quote fundamental earnings --market HK --begin-date 2026-03-01 --end-date 2026-03-31
```

---

## 期权行情 / Option Quotes

> 详细的期权交易功能见 tigeropen-option 技能 / See tigeropen-option skill for trading

```python
# 到期日 / Expirations (返回 DataFrame: symbol, option_symbol, date, timestamp, period_tag)
expirations = quote_client.get_option_expirations(symbols=['AAPL'], market='US')
# period_tag: "m"=月度期权(monthly), "w"=周期权(weekly)

# 期权链 / Option chain
from tigeropen.quote.domain.filter import OptionFilter
chain = quote_client.get_option_chain(symbol='AAPL', expiry='2025-08-29', market='US')
# 带筛选 / With filters
option_filter = OptionFilter(implied_volatility_min=0.3, delta_min=0.2, open_interest_min=100, in_the_money=True)
chain = quote_client.get_option_chain(symbol='AAPL', expiry='2025-08-29',
                                       option_filter=option_filter, return_greek_value=True, market='US')

# 实时行情 / Real-time quotes
briefs = quote_client.get_option_briefs(identifiers=['AAPL  250829C00150000'])

# K线 / K-lines (支持: day, 1min, 5min, 30min, 60min; 可选 sort_dir)
bars = quote_client.get_option_bars(identifiers=['AAPL  250829C00150000'], period='day')

# 深度 / Depth
depth = quote_client.get_option_depth(identifiers=['AAPL  250829C00150000'], market='US')

# 逐笔 / Ticks
ticks = quote_client.get_option_trade_ticks(identifiers=['AAPL  250829C00150000'])

# 分时 / Timeline
timeline = quote_client.get_option_timeline(identifiers=['AAPL  250829C00150000'])

# 港股期权代码映射 / HK option symbol mapping
hk_symbols = quote_client.get_option_symbols(market='HK')  # e.g. 00700 -> TCH.HK
```

**get_option_chain 返回字段 / Return Fields** (pandas.DataFrame):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `identifier` | 期权完整代码 Full option identifier |
| `symbol` | 标的代码 Underlying symbol |
| `expiry` | 到期日毫秒时间戳 Expiration timestamp (ms) |
| `strike` | 行权价 Strike price |
| `put_call` | `CALL`(看涨) / `PUT`(看跌) |
| `multiplier` | 期权乘数（美股通常100） Option multiplier |
| `ask_price` | 卖价 Ask price |
| `ask_size` | 卖量 Ask size |
| `bid_price` | 买价 Bid price |
| `bid_size` | 买量 Bid size |
| `pre_close` | 前收盘价 Previous close |
| `latest_price` | 最新价 Latest price |
| `volume` | 成交量 Volume |
| `open_interest` | 未平仓合约数 Open interest |
| `last_timestamp` | 最后交易时间戳 Last trade timestamp (ms) |
| `implied_vol` | 隐含波动率 Implied volatility |
| `delta` | Delta 值 |
| `gamma` | Gamma 值 |
| `theta` | Theta 值（每日时间价值损耗） |
| `vega` | Vega 值（波动率敏感度） |
| `rho` | Rho 值（利率敏感度） |

**get_option_briefs 返回字段 / Return Fields** (pandas.DataFrame):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `identifier` | 期权完整代码 Full option identifier |
| `symbol` | 标的代码 Underlying symbol |
| `expiry` | 到期日毫秒时间戳 Expiration timestamp (ms) |
| `strike` | 行权价 Strike price |
| `put_call` | `CALL` / `PUT` |
| `multiplier` | 期权乘数 Option multiplier |
| `ask_price` | 卖价 Ask price |
| `ask_size` | 卖量 Ask size |
| `bid_price` | 买价 Bid price |
| `bid_size` | 买量 Bid size |
| `pre_close` | 前收盘价 Previous close |
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
| `mid_price` | 买卖价中间价 Mid price (ask+bid)/2 |
| `mid_timestamp` | 中间价时间戳 Mid price timestamp (ms) |
| `mark_price` | 标记价格 Mark price |
| `mark_timestamp` | 标记价格时间戳 Mark price timestamp (ms) |
| `pre_mark_price` | 前标记价格 Previous mark price |
| `selling_return` | 卖出收益率 Selling return |

### 期权分析 / Option Analysis

```python
from tigeropen.common.consts import OptionAnalysisPeriod

analysis = quote_client.get_option_analysis(symbols=['AAPL'],
                                             period=OptionAnalysisPeriod.FIFTY_TWO_WEEK)
# 返回 List[OptionAnalysis]
# 属性: symbol, implied_vol_30_days, his_volatility, iv_his_v_ratio, call_put_ratio
# iv_metric: IVMetric(period, percentile, rank)
```

**期权代码格式 / Option Symbol Format**:
- 美股 US: `'AAPL  250829C00150000'` (标的 + YYMMDD + C/P + 行权价*1000)
- 港股 HK: `'TCH.HK 230616C00550000'`

```bash
# CLI
tigeropen quote option expirations AAPL                        # 到期日列表
tigeropen quote option chain AAPL 2026-06-19                   # 期权链
tigeropen quote option briefs "AAPL  260619C00150000"          # 实时行情
tigeropen quote option bars "AAPL  260619C00150000" --period day --limit 10  # K线
```

---

## 期货行情 / Futures Quotes

```python
# 期货交易所 / Exchanges
exchanges = quote_client.get_future_exchanges()
# CME, NYMEX, COMEX, CBOT, CBOE, HKFE, SGX, OSE

# 交易所合约 / Exchange contracts
contracts = quote_client.get_future_contracts(exchange='CME')

# 指定合约 / Specific contract
contract = quote_client.get_future_contract(contract_code='CL2509')

# 主力合约 / Current/main contract
current = quote_client.get_current_future_contract(future_type='CL')

# 连续合约 / Continuous contracts
continuous = quote_client.get_future_continuous_contracts(future_type='CL')

# 所有合约(某品种) / All contracts for a type
all_contracts = quote_client.get_all_future_contracts(future_type='CL')

# 交易时间 / Trading times
times = quote_client.get_future_trading_times(identifier='CL2509')

# 实时行情 / Real-time quotes
brief = quote_client.get_future_brief(identifiers=['CL2509'])

# 深度行情 / Depth
depth = quote_client.get_future_depth(identifiers=['CL2509'])
# 单个合约返回: {'identifier': 'CL2509', 'asks': [{'price': 63.07, 'size': 10}, ...], 'bids': [...]}
# 多个合约返回: {'CL2509': {'asks': [...], 'bids': [...]}, 'ES2509': {...}}

# 逐笔 / Ticks
ticks = quote_client.get_future_trade_ticks(identifier='CL2509', limit=50)

# K线 / K-lines
bars = quote_client.get_future_bars(identifiers=['CL2509'], period=BarPeriod.DAY, limit=60)

# 分页K线 / Paginated K-lines
bars = quote_client.get_future_bars_by_page(identifier='CL2509', period=BarPeriod.DAY,
                                             begin_time='2025-01-01', end_time='2025-06-30')
```

**get_future_brief 返回字段 / Return Fields** (pandas.DataFrame):

| 字段 Field | 说明 Description |
|-----------|-----------------|
| `identifier` | 期货合约代码 Contract code (e.g. `CL2509`) |
| `ask_price` | 卖价 Ask price |
| `ask_size` | 卖量 Ask size |
| `bid_price` | 买价 Bid price |
| `bid_size` | 买量 Bid size |
| `pre_close` | 前收价 Previous close |
| `latest_price` | 最新价 Latest price |
| `latest_size` | 最新成交量 Latest trade volume |
| `latest_time` | 最新成交时间（毫秒时间戳） Latest time (ms) |
| `volume` | 当日累计成交手数 Total volume today |
| `open_interest` | 未平仓合约数量 Open interest |
| `open_interest_change` | 未平仓合约变化量 Open interest change |
| `open` | 开盘价 Open price |
| `high` | 最高价 High price |
| `low` | 最低价 Low price |
| `settlement` | 结算价 Settlement price |
| `limit_up` | 涨停价 Upper price limit |
| `limit_down` | 跌停价 Lower price limit |
| `avg_price` | 均价 Average price |

```bash
# CLI
tigeropen quote future exchanges                       # 交易所列表
tigeropen quote future contracts CME                  # 交易所合约
tigeropen quote future briefs ES2606 CL2509           # 实时报价
tigeropen quote future bars CL2509 --period day --limit 20  # K线
```

---

## 基金行情 / Fund Quotes

> CLI 暂不支持，请使用 Python SDK。No CLI equivalent — use Python SDK.

```python
# 基金代码 / Fund symbols
symbols = quote_client.get_fund_symbols()

# 基金合约 / Fund contracts
contracts = quote_client.get_fund_contracts(symbols=['ARKK'])

# 最新行情 / Latest quote
quote = quote_client.get_fund_quote(symbols=['ARKK'])

# 历史行情 / Historical quotes
history = quote_client.get_fund_history_quote(symbols=['ARKK'],
                                               begin_time='2025-01-01', end_time='2025-06-30')
```

---

## 数字货币行情 / Cryptocurrency Quotes

> CLI 暂不支持，请使用 Python SDK。No CLI equivalent — use Python SDK.

```python
from tigeropen.common.consts import SecurityType

# 代码列表 / Symbols
symbols = quote_client.get_symbols(market=Market.US, sec_type=SecurityType.CC)

# 实时行情 / Real-time quotes
# 注意：没有 get_cc_briefs 方法；用 get_stock_briefs 并传 sec_type
# There is no get_cc_briefs; use get_stock_briefs with sec_type
briefs = quote_client.get_stock_briefs(symbols=['BTC/USD', 'ETH/USD'],
                                       sec_type=SecurityType.CC)

# K线 / K-lines
bars = quote_client.get_bars(['BTC/USD'], period=BarPeriod.DAY, limit=30, sec_type=SecurityType.CC)

# 分时 / Timeline
timeline = quote_client.get_timeline(['BTC/USD'], sec_type=SecurityType.CC)
```

---

## 窝轮/牛熊证行情 / Warrant & CBBC Quotes

> CLI 暂不支持，请使用 Python SDK。No CLI equivalent — use Python SDK.

```python
from tigeropen.common.consts import SortDirection

# 窝轮筛选器 / Warrant scanner
# 排序参数名为 sort_field_name / sort_dir(SortDirection 枚举)
warrants = quote_client.get_warrant_filter(
    symbol='00700',
    sort_field_name='changeRate', sort_dir=SortDirection.DESC)
# 窝轮类型等条件通过 filter_params=WarrantFilterParams(...) 传入
# other criteria go in filter_params (WarrantFilterParams)

# 窝轮行情 / Warrant quotes
briefs = quote_client.get_warrant_briefs(symbols=['12345'])
```

---

## 选股器 / Market Scanner

### 选股工作流 / Scanner Workflow

```
1. 确定筛选条件 → 2. 构造 StockFilter 对象列表 → 3. 设置排序 SortFilterData → 4. 调用 market_scanner → 5. 分页读取结果
```

```python
from tigeropen.quote.domain.filter import StockFilter, SortFilterData
from tigeropen.common.consts.filter_fields import (
    StockField, AccumulateField, FinancialField, MultiTagField,
    AccumulatePeriod, FinancialPeriod
)
from tigeropen.common.consts import Market, SortDirection

# ── 基础筛选: 市值 + PE ─────────────────────────────────────────────────────────
f_cap   = StockFilter(StockField.MarketValue, filter_min=1e10)          # 总市值 > 100亿
f_pe    = StockFilter(StockField.PeTTM, filter_max=30)                  # PE TTM < 30
sort    = SortFilterData(StockField.MarketValue, sort_dir=SortDirection.DESC)

result = quote_client.market_scanner(
    market=Market.US,
    filters=[f_cap, f_pe],
    sort_field_data=sort,
    page_size=20
)

print(f"共 {result.total_count} 条 / Total: {result.total_count}")
for item in result.items:
    print(f"{item.symbol}: 市值={item[f_cap]:.2e}, PE={item[f_pe]:.1f}")

# ── 分页读取 / Pagination ───────────────────────────────────────────────────────
cursor_id = result.cursor_id
while result.page < result.total_page - 1 and cursor_id:
    result = quote_client.market_scanner(
        market=Market.US, filters=[f_cap, f_pe],
        sort_field_data=sort, page_size=20, cursor_id=cursor_id
    )
    cursor_id = result.cursor_id
    for item in result.items:
        print(item.symbol, item[f_cap])
```

```python
# ── 今日涨幅筛选 / Today's gainers ────────────────────────────────────────────
# 使用 StockField.current_ChangeRate (盘中涨跌幅) 筛选当日涨幅 > 3%
f_change = StockFilter(StockField.current_ChangeRate, filter_min=3)
sort     = SortFilterData(StockField.current_ChangeRate, sort_dir=SortDirection.DESC)
result   = quote_client.market_scanner(market=Market.US, filters=[f_change],
                                        sort_field_data=sort, page_size=20)
for item in result.items:
    print(f"{item.symbol}: +{item[f_change]:.1f}%")
```

```python
# ── 高 ROE 筛选 / High ROE (annual) ──────────────────────────────────────────
# AccumulateField.ROE 需要指定 accumulate_period，返回值为小数 (0.15 = 15%)
f_roe  = StockFilter(AccumulateField.ROE, filter_min=0.15,
                     accumulate_period=AccumulatePeriod.ANNUAL)
sort   = SortFilterData(AccumulateField.ROE, sort_dir=SortDirection.DESC,
                        period=AccumulatePeriod.ANNUAL)
result = quote_client.market_scanner(market=Market.US, filters=[f_roe],
                                      sort_field_data=sort, page_size=20)
for item in result.items:
    print(f"{item.symbol}: ROE={item[f_roe]*100:.1f}%")
```

```python
# ── FinancialField (LTM 口径) ─────────────────────────────────────────────────
f_roe_fin = StockFilter(FinancialField.ReturnOnEquityRate, filter_min=0.15,
                        financial_period=FinancialPeriod.LTM)
result    = quote_client.market_scanner(market=Market.US, filters=[f_roe_fin], page_size=20)

# ── MultiTagField: 有期权且非 ETF ─────────────────────────────────────────────
f_opts = StockFilter(MultiTagField.OptionsAvailable, tag_list=['1'])
f_etf  = StockFilter(MultiTagField.Type, tag_list=['0'])  # 0 = 普通股
result = quote_client.market_scanner(market=Market.US,
                                      filters=[f_opts, f_etf], page_size=20)
```

### 字段类型说明 / Field Types

| 类型 | 导入路径 | 适用场景 | 是否需要 period |
|------|---------|---------|----------------|
| `StockField` | `filter_fields.StockField` | 价格、成交量、市值、PE等实时指标 | 否 |
| `AccumulateField` | `filter_fields.AccumulateField` | ROE、营收增长、涨跌幅（带周期）| 是（`accumulate_period`）|
| `FinancialField` | `filter_fields.FinancialField` | LTM 财务指标（净利率、ROE等）| 固定 `FinancialPeriod.LTM` |
| `MultiTagField` | `filter_fields.MultiTagField` | 行业、概念、期权可用等标签 | 否（`tag_list`）|

### 常用字段速查 / Common Fields

**StockField（价格/市场数据）**

| 字段 | 说明 |
|------|-----|
| `MarketValue` | 总市值 Total market cap |
| `FloatMarketVal` | 流通市值 Float market cap |
| `current_ChangeRate` | 今日涨跌幅% Intraday change rate |
| `Volume` | 成交量 Volume |
| `Amount` | 成交额 Turnover amount |
| `TurnoverRate` | 换手率 Turnover rate |
| `PeTTM` | 市盈率 TTM P/E ratio |
| `CurPrice` | 最新价 Latest price |
| `Week52High` / `Week52Low` | 52周高/低 52-week high/low |

**AccumulateField（需指定 `accumulate_period`）**

| 字段 | 说明 | 值说明 |
|------|-----|-------|
| `ChangeRate` | 涨跌幅（含周期）| 百分比数值，如 5.0 |
| `ROE` | 净资产收益率 | 小数，如 0.15 = 15% |
| `ROA` | 总资产收益率 | 小数 |
| `NetIncome_Ratio_Annual` | 净利润同比增长率 | 百分比 |
| `GrossProfitRate` | 毛利率 | 小数 |
| `Total_Revenue` | 营业收入 | 绝对值 |

**AccumulatePeriod 常用值**: `ANNUAL`（年度）、`QUARTERLY`（季度）、`SEMIANNUAL`（半年）、`Five_Days`、`Beginning_Of_The_Year_To_Now`

```bash
# CLI
# 今日涨幅 > 3%, 按涨幅降序
tigeropen quote scanner --filter current_ChangeRate:3: --sort current_ChangeRate --sort-dir DESC --limit 20

# 大市值且 PE < 30（美股）
tigeropen quote scanner --filter MarketValue:1e10: --filter PeTTM::30 --sort MarketValue --limit 20

# 高 ROE 筛选（年度，ROE > 15%）
tigeropen quote scanner --filter acc.ROE:0.15::ANNUAL --sort acc.ROE:ANNUAL --sort-dir DESC --limit 20

# 港股选股
tigeropen quote scanner --market HK --filter MarketValue:1e9: --sort MarketValue --limit 20

# 大市值 + 高 ROE + 低 PE（组合筛选）
tigeropen quote scanner --filter MarketValue:1e10: --filter acc.ROE:0.15::ANNUAL --filter PeTTM::30 --sort MarketValue --limit 20
```

> **filter 格式 / Filter format**:
> - `FIELD:min:max` — StockField 范围，如 `MarketValue:1e9:1e12`、`PeTTM::20`
> - `acc.FIELD:min:max:PERIOD` — AccumulateField，如 `acc.ROE:0.15::ANNUAL`
> - `fin.FIELD:min:max` — FinancialField (LTM)，如 `fin.ReturnOnEquityRate:0.15:`
> - `tag.FIELD:tag1,tag2` — MultiTagField，如 `tag.OptionsAvailable:1`

---

## 行情权限管理 / Quote Permission Management

```python
# 查询权限 / Query permissions
permissions = quote_client.get_quote_permission()
for p in permissions:
    print(f"{p['name']}: expires={p['expire_at']}")  # -1 = 永不过期 never expires

# 抢占权限 / Grab permissions (多设备切换时 / multi-device)
quote_client.grab_quote_permission()

# K线配额 / K-line quota
quota = quote_client.get_kline_quota(with_details=True)
for q in quota:
    print(f"{q['method']}: used={q['used']}, remain={q['remain']}")
```

**权限类型**: `usQuoteBasic`, `usStockQuoteLv2Totalview`, `hkStockQuoteLv2`, `usOptionQuote`, `hkFutureQuoteLv2`, `CBOEFuturesQuoteLv2`

---

## 注意事项 / Notes

- 行情权限需单独购买，API 与 App 独立 / Quote permissions require separate purchase
- 多设备同账号：最后抢占的设备获取权限 / Last device to grab gets permissions
- K线有配额限制，大量数据用 `get_bars_by_page` / Use paginated fetch for large datasets
- 港股代码5位数 `00700`(腾讯) / HK codes are 5-digit like `00700`
- 深度行情需 L2 权限 / Depth quotes require Level 2 permission
- 更多详情见 / More details: https://docs.itigerup.com/docs/quote-stock
