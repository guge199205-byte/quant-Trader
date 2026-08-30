<!-- TOC: 期权命令（Options） -->
# 期权命令（Options）

期权筛选、简写代码解析、期权链、波动率、行权概率、策略组合、损益分析、市场统计、异动、0DTE、财报期权、卖方策略。

> **硬约束**：组合期权摆盘价/下单 `--price` 必须用 `get_option_strategy_analysis.py` 的 `bid1`/`ask1`，禁止对各腿 `get_snapshot.py` 后手动加减买卖价。

## 目录

- 筛选期权
- 解析期权简写代码
  - 第一步：你来解析用户输入（脚本不做这一步）
  - 第二步：调用脚本从期权链匹配
  - 第三步：向用户展示结果
  - 期权代码格式说明
  - 期权操作工作流
- 获取期权到期日
- 获取期权链
- 获取期权波动率分析
- 获取期权行权概率
- 获取期权策略组合腿列表
- 组合期权摆盘价（硬约束）
- 获取期权策略有效价差
- 获取期权快照行情
- 期权策略损益分析
- 期权数据
  - 获取期权波动率分析（期权波动率分析）
  - 获取期权行权概率（期权行权概率）
  - 获取期权市场统计（成交量/持仓量时间序列）
  - 获取期权标的历史统计（P/C 比率时间序列）
  - 获取批量标的最新数据（IV/HV 多周期快照）
  - 获取期权标的历史波动率（IV/HV 时间序列）
  - 获取期权标的排行（热门标的排行）
  - 获取期权合约排行
  - 获取期权异动列表
  - 获取期权异动告警设置
  - 修改期权异动告警条件
  - 接收期权异动推送
  - 获取末日期权标的列表（0DTE 筛选）
  - 获取末日期权合约列表（0DTE 合约详情）
  - 获取财报期权标的列表（IV Crush / 预期波动）
  - 获取期权卖方策略列表（Covered Call / Cash Secured Put）
  - 获取期权策略组合腿列表（期权策略）
  - 获取期权策略有效价差（期权价差）
  - 获取期权快照行情（多腿期权报价）
  - 期权策略损益分析（组合摆盘价 + 损益分析）

---

### 筛选期权
当用户希望按 IV / Greeks / 持仓量 / 标的属性等条件筛选期权时：
```bash
python skills/futuapi/scripts/quote/get_option_screen.py --markets US_STOCK HK_STOCK [--config config.json] [--page-count 50] [--json]
```
- 协议号 3253；必传 `--markets`，取自 `OptMarketCategory`：`US_STOCK`(0) / `US_INDEX`(1) / `US_FUTURE`(2) / `HK_STOCK`(3) / `HK_INDEX`(4) / `JP_STOCK`(5) / `JP_INDEX`(6)
- 返回 `(last_page, all_count, DataFrame)` 三元组，DataFrame 默认 47 列（含 `underlying` dict）
- US_FUTURE / JP_STOCK / JP_INDEX **目前结果为空**（后续支持）
- 后端禁止同组混用 underlying + option，SDK 自动按需开新组：默认 AND（开新组）；同 indicator_type 显式 `or_with_previous=True` 时与上一条件 OR（同组）
- 数值统一传**原始值**（OpenD 负责倍率换算）：IV/HV/IV_RANK/IV_PERCENTILE 传**百分比原始数**（30% → **30.0**，不是 0.3）；DELTA/GAMMA/VEGA/THETA/RHO/概率类直接传原始数
- `OptUnderlyingIndicator.STOCK_LIST` 接受标的 **stock_id（int）**，不能直接传证券代码
- `OptUnderlyingIndicator.PLATE(103)` 传入会报错，**禁用**
- `OptIndicator.PREMIUM(2021)` 仅支持 sort/retrieve，作为 filter 会报错
- `BUY_BREAK_EVEN_POINT(3023)` 已废弃，新代码用 `BUY_TO_BEP(3011)`
- `add_underlying_retrieve` 不调用则返回的 `underlying` dict 不被填充（字段为 `'N/A'`）

OptUnderlyingIndicator 实测枚举：STOCK_LIST=101, INDEX_LIST=106, VOLUME=201, OPEN_INTEREST=202, IV=203, HV=204, IV_RANK=205, IV_PERCENTILE=206, IV_CHANGE=207, IV_CHANGE_RATIO=208, IV_HV_RATIO=209, IV_HV_SPREAD=210, MARKET_CAP=401, STOCK_PRICE=402, CHANGE_RATIO=403。

config.json 示例（CALL OR PUT 同组 + IV>30% 跨组 + 按持仓量降序）：
```json
{
  "filters": [
    {"kind": "option", "indicator_type": "OPTION_TYPE", "values": [1]},
    {"kind": "option", "indicator_type": "OPTION_TYPE", "values": [2], "or_with_previous": true},
    {"kind": "underlying", "indicator_type": "IV", "lower": 30.0}
  ],
  "sorts": [{"indicator_type": "OPEN_INTEREST", "desc": true}],
  "option_retrieves": ["OPTION_TYPE", "STRIKE_PRICE", "OPEN_INTEREST", "IMPLIED_VOLATILITY"],
  "underlying_retrieves": ["STOCK_PRICE", "IV", "MARKET_CAP"]
}
```


### 解析期权简写代码

当用户提供期权描述时（如 `JPM 260320 267.50C`、`腾讯 260320 420.00 购`），**必须先由你解析出正股代码、到期日、行权价、期权类型，再调用脚本从期权链中精准匹配**。

```bash
python skills/futuapi/scripts/quote/resolve_option_code.py --underlying US.JPM --expiry 2026-03-20 --strike 267.50 --type CALL [--json]
```

#### 第一步：你来解析用户输入（脚本不做这一步）

用户可能使用多种格式描述期权，你需要根据上下文拆解出 4 个要素：

| 要素 | 说明 | 你的职责 |
|------|------|---------|
| **正股代码** | 必须带市场前缀（如 `US.JPM`、`HK.00700`） | 根据上下文判断市场：`JPM` → 美股 → `US.JPM`；`腾讯` → 港股 → `HK.00700`；`苹果` → 美股 → `US.AAPL` |
| **到期日** | `yyyy-MM-dd` 格式 | 从 `YYMMDD` 转换：`260320` → `2026-03-20` |
| **行权价** | 数字 | 直接提取：`267.50` |
| **期权类型** | `CALL` 或 `PUT` | `C`/`Call`/`购`/`认购`/`看涨` → `CALL`；`P`/`Put`/`沽`/`认沽`/`看跌` → `PUT` |

**用户输入格式示例**：

| 用户输入 | 你解析出的参数 |
|---------|--------------|
| `JPM 260320 267.50C` | `--underlying US.JPM --expiry 2026-03-20 --strike 267.50 --type CALL` |
| `腾讯 260320 420.00 购` | `--underlying HK.00700 --expiry 2026-03-20 --strike 420.00 --type CALL` |
| `AAPL 261218 200P` | `--underlying US.AAPL --expiry 2026-12-18 --strike 200 --type PUT` |
| `苹果 260117 250 看跌` | `--underlying US.AAPL --expiry 2026-01-17 --strike 250 --type PUT` |
| `买入 BABA 260620 120C` | `--underlying US.BABA --expiry 2026-06-20 --strike 120 --type CALL` |

**市场判断规则**：
- 用户给出中文股票名（腾讯、阿里、美团等）→ 根据你的知识判断市场和代码
- 用户给出英文 Ticker（JPM、AAPL、TSLA）→ 通常是美股，用 `US.` 前缀
- 用户给出带前缀的代码（US.JPM、HK.00700）→ 直接使用
- 不确定时 → 用 AskUserQuestion 询问用户

#### 第二步：调用脚本从期权链匹配

```bash
# 脚本通过期权链接口精准查找，返回富途期权代码
python skills/futuapi/scripts/quote/resolve_option_code.py --underlying US.JPM --expiry 2026-03-20 --strike 267.50 --type CALL --json
```

脚本会自动：
1. 调用 `get_option_chain` 获取该正股在指定到期日的所有期权
2. 按行权价 + 期权类型精准匹配
3. 返回期权代码（如 `US.JPM260320C267500`）
4. 匹配失败时列出最接近的合约供参考

#### 第三步：向用户展示结果

展示期权代码时，使用 "富途期权代码是 `xxx`" 格式。

#### 期权代码格式说明

富途 的期权代码由以下部分拼接而成：

```
{市场}.{正股简称}{YYMMDD}{C/P}{行权价×1000}
```

| 部分 | 说明 | 示例 |
|------|------|------|
| 市场 | `US`（美股）、`HK`（港股） | `US` |
| 正股简称 | 美股用 Ticker，港股用简称缩写 | `JPM`、`TCH`（腾讯）、`MIU`（小米） |
| YYMMDD | 到期日（年月日各两位） | `260320` = 2026-03-20 |
| C/P | `C` = Call（认购），`P` = Put（认沽） | `C` |
| 行权价×1000 | 行权价乘以 1000，去掉小数点 | `267500` = 267.50 |

**完整示例**：

| 期权描述 | 期权代码 |
|---------|---------|
| JPM 2026-03-20 267.50 Call | `US.JPM260320C267500` |
| AAPL 2026-12-18 200 Put | `US.AAPL261218P200000` |
| 腾讯 2026-03-27 470 Call | `HK.TCH260327C470000` |
| 小米 2026-04-29 33 Put | `HK.MIU260429P33000` |
| TIGR 2026-04-10 6.50 Put | `US.TIGR260410P6500` |

> 注意：港股期权的正股简称不是股票代码，而是交易所分配的缩写（如腾讯=TCH，小米=MIU）。因此不要手动拼接期权代码，应通过 `resolve_option_code.py` 从期权链中查找。

#### 期权操作工作流

当用户提及期权时（如"查看/买入/卖出某个期权"），按以下流程操作：

1. **识别期权代码**：
   - 如果用户给出期权描述（如 `JPM 260320 267.50C` 或 `腾讯 260320 420 购`），按上述两步解析 → 调用 `resolve_option_code.py` 获取富途期权代码
   - 如果用户只给出正股名称和期权意向（如"看看 JPM 下周到期的 Call"），先用 `get_option_expiration_date.py` 查到期日，再用 `get_option_chain.py` 列出对应期权供用户选择

2. **查询期权行情**：
   - 单腿期权：获得富途期权代码后，用 `get_snapshot.py`、`get_kline.py` 等查询
   - **多腿/组合期权摆盘价（bid1/ask1）**：**必须**用 `get_option_strategy_analysis.py`（见下方「组合期权摆盘价」硬约束），**禁止**对各腿分别 `get_snapshot.py` 后手动加减买卖价

3. **期权交易**：
   - 期权下单与股票下单使用相同的 `place_order.py` 脚本
   - 期权数量单位为"张"
   - 美股期权价格精度为小数 2 位

### 获取期权到期日
当用户问"期权到期日"、"有哪些到期日" 时：
```bash
python skills/futuapi/scripts/quote/get_option_expiration_date.py US.AAPL [--json]
```

### 获取期权链
当用户问"期权链"、"有哪些期权" 时：
```bash
python skills/futuapi/scripts/quote/get_option_chain.py US.AAPL [--start 2026-03-01] [--end 2026-03-31] [--json]
```

### 获取期权波动率分析
当用户问"期权波动率"、"隐含波动率"、"历史波动率"、"波动率溢价" 时：
```bash
python skills/futuapi/scripts/quote/get_option_volatility.py US.AAPL280317C260000 [--query-time-period 2] [--hv-time-period 30] [--json]
```

### 获取期权行权概率
当用户问"行权概率"、"期权行权概率"、"期权到期能否行权的概率"时：
```bash
python skills/futuapi/scripts/quote/get_option_exercise_probability.py US.AAPL280317C260000 [--json]
```

### 获取期权策略组合腿列表
当用户问"期权策略"、"策略组合腿"、"STRADDLE"、"SPREAD"、"STRANGLE"、"BUTTERFLY"、"CONDOR"、"期权组合"时：
```bash
python skills/futuapi/scripts/quote/get_option_strategy.py HK.00700 STRADDLE 2026-05-22 [--spread 10.0] [--far-expire-time 2026-06-26] [--option-type CALL] [--strike-price 300.0] [--json]
```
- 支持策略类型：STRADDLE / SPREAD / STRANGLE / BUTTERFLY / CONDOR / IRON_BUTTERFLY / IRON_CONDOR / COLLAR / DIAGONAL_SPREAD
- 返回的组合腿列表可作为 `get_option_strategy_analysis.py`（**组合摆盘价/下单定价优先**）和 `get_option_quote.py`（Greeks/最新价快照）的输入

### 组合期权摆盘价（硬约束）

当用户询问**期权组合/策略的摆盘价、买卖价、组合报价**，或需要为 `place_combo_order` / `comboorder_tradinginfo_query` 确定 `--price` 时：

**必须**调用 `get_option_strategy_analysis.py`，**禁止**：
- 对各腿分别调用 `get_snapshot.py` 再手动加减 bid/ask
- 对各腿分别查单腿行情后自行推算组合买卖价

推荐流程：
1. `get_option_strategy.py`（可选）→ 获取标准策略腿列表
2. **`get_option_strategy_analysis.py`** → 读取 **`bid1`（组合买一）** / **`ask1`（组合卖一）**
3. 需要下单：以 `bid1`/`ask1` 作为限价参考（买入通常参考 `ask1`，卖出通常参考 `bid1`）→ `comboorder_tradinginfo_query.py` → `place_combo_order.py`

`legs` 入参：`[{"code":"...","action":"BUY|SELL","quantity":1.0}, ...]`（与 `get_option_strategy` 输出字段一致）

与 `get_option_quote.py` 的分工：
- **`get_option_strategy_analysis`**：组合级 **bid1/ask1** + 最大盈亏/盈亏平衡点/Greeks（**摆盘价与组合下单定价优先**）
- **`get_option_quote`**：最新价、涨跌、Greeks 等快照（**不用于组合摆盘价**，勿替代 `get_option_strategy_analysis`）

### 获取期权策略有效价差
当用户问"期权价差"、"有效价差"、"策略价差列表"时：
```bash
python skills/futuapi/scripts/quote/get_option_strategy_spread.py HK.00700 STRANGLE 2026-05-22 [--json]
```
- 仅支持：SPREAD / STRANGLE / COLLAR / BUTTERFLY / CONDOR / IRON_BUTTERFLY / IRON_CONDOR / DIAGONAL_SPREAD

### 获取期权快照行情
当用户问"期权快照"、"期权实时行情"、"多腿期权 Greeks"时（通常配合 `get_option_strategy.py` 使用）：
```bash
python skills/futuapi/scripts/quote/get_option_quote.py '[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0},{"code":"HK.TCH260522C330000","action":"BUY","quantity":1.0}]' [--json]
```
- 输入为期权腿 JSON 数组，字段：code（期权代码）、action（BUY/SELL）、quantity（数量）
- **不用于组合摆盘价**：组合 bid/ask 请用 `get_option_strategy_analysis.py`（见上方硬约束）

### 期权策略损益分析
当用户问"损益分析"、"期权盈亏"、"最大盈利"、"最大亏损"、"盈亏平衡点"、"盈利概率"、**"组合摆盘价"、"组合买卖价"、"组合 bid ask"、"组合报价"**时：
```bash
python skills/futuapi/scripts/quote/get_option_strategy_analysis.py '[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0},{"code":"HK.TCH260522C330000","action":"BUY","quantity":1.0}]' [--json]
```
- 返回 **`bid1`/`ask1`（组合摆盘价）**、最大盈亏、盈亏平衡点、盈利概率、Delta、Theta 等
- **组合期权摆盘价与 `place_combo_order` 的 `--price` 应优先取自本接口**，勿用单腿快照自行计算



---

### 期权数据

#### 获取期权波动率分析（期权波动率分析）
当用户问"期权波动率"、"隐含波动率"、"历史波动率"、"IV"、"HV"、"波动率溢价"、"IV vs HV"、"波动率对比"、"option volatility"、"期权 IV"、"implied volatility" 时：
```bash
python skills/futuapi/scripts/quote/get_option_volatility.py [--query-time-period QUERY_TIME_PERIOD] [--hv-time-period HV_TIME_PERIOD] [--json] code
```
- 入参为**期权代码**，可先用 `resolve_option_code.py` 解析

**接口限制（市场）**：仅支持期权合约代码

**参数说明**：
- code: 期权代码，如 US.AAPL260427C270000
- --query-time-period: 查询时间周期：1=周, 2=月, 3=季度, 4=半年, 5=年（默认 2=月）
- --hv-time-period: 标的物历史波动率周期（5~250 日，默认 30）

#### 获取期权行权概率（期权行权概率）
当用户问"行权概率"、"期权行权概率"、"exercise probability"、"strike probability"、"期权到期能否行权的概率"、"ITM 概率"、"期权 delta 对应概率" 时：
```bash
python skills/futuapi/scripts/quote/get_option_exercise_probability.py [--json] code
```
- 入参为**期权代码**，可先用 `resolve_option_code.py` 解析

**接口限制（市场）**：仅支持期权合约代码

**参数说明**：
- code: 期权代码，如 US.AAPL260427C270000

#### 获取期权市场统计（成交量/持仓量时间序列）
当用户问"期权市场统计"、"期权成交量统计"、"期权持仓量统计"、"option market statistic"、"option volume trend"、"option open interest trend"、"期权市场成交量趋势"、"期权市场持仓量趋势" 时：
```bash
python skills/futuapi/scripts/quote/get_option_market_statistic.py --market US_SECURITY --data-type VOLUME [--begin 2024-01-01] [--end 2024-06-01] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX
- --data-type: 数据类型（必填）: VOLUME(成交量), OPEN_INTEREST(持仓量)
- --begin: 开始日期 YYYY-MM-DD（不传默认近一年）
- --end: 结束日期 YYYY-MM-DD
- 跨度不超过一年；自动分页拉取全部数据

#### 获取期权标的历史统计（P/C 比率时间序列）
当用户问"期权标的历史统计"、"Put/Call 比率"、"PCR"、"P/C ratio"、"期权成交量比率"、"期权持仓比率"、"underlying option statistic" 时：
```bash
python skills/futuapi/scripts/quote/get_option_underlying_his_statistic.py US.AAPL [--index-option-type NORMAL] [--begin 2025-01-01] [--end 2025-06-01] [--json]
```

**参数说明**：
- code: 标的股票代码（必填），如 US.AAPL
- --index-option-type: 指数期权类型: NORMAL, SMALL（仅指数标的需要）
- --begin/--end: 日期范围，跨度最多 364 天
- 持仓量数据有 T-1 日延迟

#### 获取批量标的最新数据（IV/HV 多周期快照）
当用户问"期权标的总览"、"批量标的数据"、"标的 IV 快照"、"underlying overview"、"批量 IV HV"、"期权标的成交量" 时：
```bash
python skills/futuapi/scripts/quote/get_option_underlying_overview.py US.AAPL US.TSLA US.NVDA [--index-option-type NORMAL] [--json]
```

**参数说明**：
- codes: 标的股票代码列表（必填），空格分隔，最多 500 个
- --index-option-type: 指数期权类型: NORMAL, SMALL
- 快照接口，返回当前最新数据；持仓量有 T-1 延迟

#### 获取期权标的历史波动率（IV/HV 时间序列）
当用户问"标的历史波动率"、"IV 走势"、"HV 走势"、"IV 时间序列"、"underlying historical volatility"、"IV trend"、"HV trend"、"IV history" 时：
```bash
python skills/futuapi/scripts/quote/get_option_underlying_his_volatility.py US.AAPL [--index-option-type NORMAL] [--begin 2025-01-01] [--end 2025-06-01] [--json]
```

**参数说明**：
- code: 标的股票代码（必填），如 US.AAPL
- --index-option-type: 指数期权类型: NORMAL, SMALL
- --begin/--end: 日期范围，跨度最多 364 天

#### 获取期权标的排行（热门标的排行）
当用户问"期权标的排行"、"期权热门标的"、"underlying rank"、"option underlying rank"、"期权标的成交量排行"、"IV 排行"、"HV 排行" 时：
```bash
python skills/futuapi/scripts/quote/get_option_underlying_rank.py --market US_SECURITY --sort-type VOLUME [--sort-direction 0] [--count 20] [--trading-date 2025-06-01] [--config filters.json] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX
- --sort-type: 排序字段（必填）: VOLUME, VOLUME_RATIO, OPEN_INTEREST, OPEN_INTEREST_RATIO, PRICE, PRICE_CHANGE, IV, IV_CHANGE, HV, HV_CHANGE, IV_RANK, IV_PERCENTILE, MARKET_CAP
- --sort-direction: 0=降序(默认), 1=升序
- --count: 每页数量 [1,200]
- --config: JSON 筛选配置文件（支持 13 种筛选因子）

#### 获取期权合约排行
当用户问"期权合约排行"、"期权排行"、"option rank"、"期权成交量排行"、"期权持仓排行"、"OI 排行"、"期权 IV 排行" 时：
```bash
python skills/futuapi/scripts/quote/get_option_rank.py --market US_SECURITY --sort-type VOLUME [--sort-direction 0] [--count 20] [--trading-date 2025-06-01] [--config filters.json] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX
- --sort-type: 排序类型（必填）: VOLUME, TURNOVER, OI, OI_INCREMENT, OI_DECREMENT, OI_MARKET_CAP, OI_MARKET_CAP_INCREMENT, OI_MARKET_CAP_DECREMENT, CHANGE_RATE, IV
- --sort-direction: 0=降序(默认), 1=升序
- --count: 每页数量 [1,200]
- --config: JSON 筛选配置文件（支持 18 种筛选因子）

#### 获取期权异动列表
当用户问"期权异动"、"期权大单"、"option event"、"期权异动列表"、"unusual option activity"、"option flow"、"期权扫单" 时：
```bash
python skills/futuapi/scripts/quote/get_option_event.py --market US_SECURITY [--count 50] [--config filters.json] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX
- --count: 每页数量 [1,300]
- --config: JSON 筛选/排序配置文件（支持 25+ 种筛选因子 + 排序）

**配置示例**：
```json
{
  "filters": [
    {"indicator_type": "OPTION_TYPE", "value_list": [1]},
    {"indicator_type": "TURNOVER", "interval_min": 100000.0},
    {"indicator_type": "OWNER_LIST", "security_list": ["US.TSLA", "US.AAPL"]}
  ],
  "sort": {"indicator_type": "TURNOVER", "direction": "DESCEND"}
}
```

#### 获取期权异动告警设置
当用户问"期权异动告警"、"异动提醒列表"、"option event alert"、"我的期权告警"、"查看告警设置" 时：
```bash
python skills/futuapi/scripts/quote/get_option_event_alert.py [--count 50] [--json]
```

**参数说明**：
- --count: 每页数量 [1,500]，默认 200
- 自动分页拉取全部告警设置

**返回字段**（--json 输出）：
- key: 告警唯一标识
- enable: 告警开关
- option_market: 市场品类（OptionMarket）
- watchlist_group_name: 自选股分组名称
- underlying: 指定标的代码
- option_type: 期权类型 CALL/PUT
- side_type_list: 成交方向列表
- order_type_list: 订单类型列表
- market_cap_range_min/max: 标的市值范围
- market_cap_min_inclusive/max_inclusive: 标的市值是否闭区间
- expiry_days_range_min/max: 距到期天数范围
- expiry_days_min_inclusive/max_inclusive: 距到期天数是否闭区间
- price_range_min/max: 异动成交价范围
- price_min_inclusive/max_inclusive: 异动成交价是否闭区间
- size_range_min/max: 异动成交量范围（张）
- size_min_inclusive/max_inclusive: 异动成交量是否闭区间
- premium_range_min/max: 异动成交额范围
- premium_min_inclusive/max_inclusive: 异动成交额是否闭区间
- iv_range_min/max: 隐含波动率范围(%)
- iv_min_inclusive/max_inclusive: 隐含波动率是否闭区间
- earnings_date_begin/end: 财报时间筛选日期(yyyy-MM-dd)
- note: 备注

#### 修改期权异动告警条件
当用户问"设置期权异动告警"、"新增告警"、"删除告警"、"修改告警"、"set option alert"、"add alert"、"delete alert" 时：
```bash
python skills/futuapi/scripts/quote/set_option_event_alert.py --op ADD --config alert.json [--json]
python skills/futuapi/scripts/quote/set_option_event_alert.py --op DELETE --key 14694 [--json]
python skills/futuapi/scripts/quote/set_option_event_alert.py --op ENABLE --key 14694 [--json]
python skills/futuapi/scripts/quote/set_option_event_alert.py --op DISABLE --key 14694 [--json]
python skills/futuapi/scripts/quote/set_option_event_alert.py --op DELETE_ALL [--json]
```

**参数说明**：
- --op: 操作类型（必填）: ADD, DELETE, MODIFY, ENABLE, DISABLE, DELETE_ALL
- --key: 告警唯一标识（DELETE/MODIFY/ENABLE/DISABLE 时使用）
- --config: JSON 配置文件（ADD/MODIFY 时使用）

**JSON 配置字段**：
- 监控范围（三选一）：option_market / watchlist_group_name / underlying
- option_type: 期权类型 CALL/PUT
- side_type_list: 成交方向列表（BUY/SELL/NEUTRAL）
- order_type_list: 订单类型列表（SWEEP/BLOCK/NORMAL/CROSS/FLOOR）
- market_cap_range_min/max: 标的市值范围
- expiry_days_range_min/max: 距到期天数范围
- price_range_min/max: 异动成交价范围
- size_range_min/max: 异动成交量范围（张）
- premium_range_min/max: 异动成交额范围
- iv_range_min/max: 隐含波动率范围(%)
- 每个范围支持独立开闭区间（如 size_min_inclusive: false 表示开区间），默认 true 闭区间
- earnings_date_begin/end: 财报时间筛选日期(yyyy-MM-dd)
- note: 备注（最多20字）

#### 接收期权异动推送
当用户问"期权异动推送"、"实时期权异动"、"push option event"、"订阅期权异动"、"期权异动通知" 时：
```bash
python skills/futuapi/scripts/subscribe/push_option_event.py [--duration 300] [--json]
```

**参数说明**：
- --duration: 持续接收时间（秒，默认 300）
- 需先通过 set_option_event_alert 设置提醒条件，推送才会触发
- Ctrl+C 可中断

#### 获取末日期权标的列表（0DTE 筛选）
当用户问"末日期权"、"0DTE"、"zero dte"、"当日到期期权"、"0DTE 标的"、"末日期权筛选" 时：
```bash
python skills/futuapi/scripts/quote/get_option_zero_dte_screener.py --market US_SECURITY [--sort-type VOLUME] [--asc] [--count 20] [--config filters.json] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX
- --sort-type: 排序类型: VOLUME, IV, CHANGE_RATIO, OPEN_INTEREST, MARKET_CAP
- --asc: 升序排列
- --count: 每页数量 [1,500]，默认 50
- --config: JSON 筛选配置文件（支持 10 种筛选因子）
- 返回结果中的 chain_info 可作为 get_option_zero_dte_contract 的输入

#### 获取末日期权合约列表（0DTE 合约详情）
当用户问"末日期权合约"、"0DTE 合约"、"zero dte contract"、"0DTE 期权链"、"末日期权详情" 时：
```bash
python skills/futuapi/scripts/quote/get_option_zero_dte_contract.py --owner US.TSLA --chain-info chain.json [--sort-type VOLUME] [--asc] [--config filters.json] [--json]
```

**参数说明**：
- --owner: 标的股票代码（必填），如 US.TSLA
- --chain-info: chain_info JSON 文件路径（必填，来自 get_option_zero_dte_screener 返回）
- --sort-type: 排序类型: VOLUME, OPEN_INTEREST, IV, DELTA
- --config: JSON 筛选配置文件（支持 15 种筛选因子）
- 无分页，一次返回全部

#### 获取财报期权标的列表（IV Crush / 预期波动）
当用户问"财报期权"、"earnings option"、"IV crush"、"财报波动"、"期权财报"、"earnings screener"、"财报日期权" 时：
```bash
python skills/futuapi/scripts/quote/get_option_earnings_screener.py --market US_SECURITY [--sort-type EARNINGS_DATE] [--asc] [--count 50] [--config filters.json] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, HK_SECURITY（仅支持这两个市场）
- --sort-type: 排序类型: EARNINGS_DATE, VOLUME, IV, MARKET_CAP, CHANGE_RATIO, PRICE, IV_RANK, IV_PERCENTILE, HV, OPEN_INTEREST, LAST_REPORT_IV_CRUSH, HISTORY_REPORT_IV_CRUSH, LAST_REPORT_CHG_RATIO, HISTORY_REPORT_CHG_RATIO, ESTIMATE_EPS_YOY, ESTIMATE_REVENUE_YOY, EXPECTED_MOVE_RATIO
- --count: 每页数量 [1,500]，默认 50
- --config: JSON 筛选配置文件（支持 20 种筛选因子）

#### 获取期权卖方策略列表（Covered Call / Cash Secured Put）
当用户问"期权卖方策略"、"covered call"、"cash secured put"、"CC 策略"、"CSP 策略"、"卖方筛选"、"seller screener"、"期权收租" 时：
```bash
python skills/futuapi/scripts/quote/get_option_seller_screener.py --market US_SECURITY --seller-type COVERED_CALL [--sort-type ANNUALIZED_RETURN] [--asc] [--config filters.json] [--json]
```

**参数说明**：
- --market: 期权市场（必填）: US_SECURITY, US_INDEX, HK_SECURITY, HK_INDEX
- --seller-type: 卖方策略（必填）: COVERED_CALL, CASH_SECURED_PUT
- --sort-type: 排序类型: ANNUALIZED_RETURN, INTERVAL_RETURN, ITM_PROBABILITY, PREMIUM
- --config: JSON 筛选配置文件（支持 26 种筛选因子：标的级 13 种 + 期权级 13 种）
- 无分页，一次返回全部

---

#### 获取期权策略组合腿列表（期权策略）
当用户问"期权策略"、"策略组合腿"、"STRADDLE"、"SPREAD"、"STRANGLE"、"BUTTERFLY"、"CONDOR"、"期权组合"时：
```bash
python skills/futuapi/scripts/quote/get_option_strategy.py [--spread 10.0] [--far-expire-time 2026-06-26] [--index-option-type NORMAL] [--option-type CALL] [--strike-price 300.0] [--json] code option_strategy expire_time
```
- 入参：code（标的代码）、option_strategy（策略类型）、expire_time（到期日 yyyy-MM-dd）

**接口限制（频率）**：每 30 秒最多 30 次

**参数说明**：
- code: 标的代码，如 HK.00700 / US.AAPL
- option_strategy: 策略类型，支持 STRADDLE / SPREAD / STRANGLE / BUTTERFLY / CONDOR / IRON_BUTTERFLY / IRON_CONDOR / COLLAR / DIAGONAL_SPREAD
- expire_time: 到期日，格式 yyyy-MM-dd
- --spread: 价差值（部分策略必填）
- --far-expire-time: 远端到期日（DIAGONAL_SPREAD 使用）
- --option-type: CALL / PUT / ALL
- --strike-price: 行权价
---

#### 获取期权策略有效价差（期权价差）
当用户问"期权价差"、"有效价差"、"策略价差列表"时：
```bash
python skills/futuapi/scripts/quote/get_option_strategy_spread.py [--far-expire-time 2026-06-26] [--index-option-type NORMAL] [--json] code option_strategy expire_time
```
- 入参：code（标的代码）、option_strategy（策略类型）、expire_time（到期日）

**接口限制（频率）**：每 30 秒最多 30 次；仅支持 SPREAD / STRANGLE / COLLAR / BUTTERFLY / CONDOR / IRON_BUTTERFLY / IRON_CONDOR / DIAGONAL_SPREAD

**参数说明**：
- code: 标的代码，如 HK.00700
- option_strategy: 策略类型（见上方支持列表）
- expire_time: 到期日，格式 yyyy-MM-dd
---

#### 获取期权快照行情（多腿期权报价）
当用户问"期权快照"、"期权实时行情"、"多腿期权 Greeks"时（通常配合 `get_option_strategy.py` 使用）：
```bash
python skills/futuapi/scripts/quote/get_option_quote.py [--json] legs
```
- 入参为期权腿 JSON 数组字符串
- **不用于组合摆盘价**：组合 bid/ask 必须用 `get_option_strategy_analysis.py`

**接口限制（频率）**：每 30 秒最多 30 次

**参数说明**：
- legs: JSON 数组，如 `'[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0}]'`
  - code: 期权代码
  - action: BUY / SELL
  - quantity: 数量（浮点数）
---

#### 期权策略损益分析（组合摆盘价 + 损益分析）
当用户问"损益分析"、"期权盈亏"、"最大盈利"、"最大亏损"、"盈亏平衡点"、"盈利概率"、**"组合摆盘价"、"组合买卖价"、"组合 bid ask"、"组合报价"**时：
```bash
python skills/futuapi/scripts/quote/get_option_strategy_analysis.py [--json] legs
```
- 入参为期权腿 JSON 数组字符串；返回 **`bid1`/`ask1`（组合摆盘价）**、最大盈亏、盈亏平衡点、盈利概率、Delta、Theta
- **硬约束**：组合期权摆盘价与 `place_combo_order` / `comboorder_tradinginfo_query` 的 `--price` **必须优先取自本接口**，禁止对各腿 `get_snapshot.py` 后手动加减

**接口限制（频率）**：每 30 秒最多 30 次

**参数说明**：
- legs: JSON 数组，如 `'[{"code":"HK.TCH260522P330000","action":"BUY","quantity":1.0},{"code":"HK.TCH260522C330000","action":"BUY","quantity":1.0}]'`

---

**相关技能路由：** 相关：组合下单 → trade-commands.md；期权卖方/0DTE 筛选见正文；IV 来自 get_option_strategy_analysis（硬约束）。
