<!-- TOC: 行情命令（Quote） -->
# 行情命令（Quote）

行情/报价/K线/盘口/资金流/板块/搜索/选股。期权相关命令见 `options.md`。

> **一键全景汇总**：需同时看某标的的行情+财报+评级+估值+期权时，用 `python skills/futuapi/scripts/quote/collect.py US.AAPL --json [--with-options]`，并行抓取返回精简 JSON（配合 `references/analysis-frameworks.md` 财报点评卡片）。

### 常见标的速查表

当用户使用中文名称、英文简称或 Ticker 时，按下表映射为完整代码。不在表中的标的根据你的知识判断市场和代码，不确定时用 AskUserQuestion 询问用户。

#### 港股

| 常见称呼 | 代码 |
|---------|------|
| 腾讯 | `HK.00700` |
| 阿里巴巴、阿里 | `HK.09988` |
| 美团 | `HK.03690` |
| 小米 | `HK.01810` |
| 京东 | `HK.09618` |
| 百度 | `HK.09888` |
| 网易 | `HK.09999` |
| 快手 | `HK.01024` |
| 比亚迪 | `HK.01211` |
| 中芯国际 | `HK.00981` |
| 华虹半导体 | `HK.01347` |
| 商汤 | `HK.00020` |
| 理想汽车、理想 | `HK.02015` |
| 蔚来 | `HK.09866` |
| 小鹏 | `HK.09868` |
| 恒生指数 ETF | `HK.02800` |
| 盈富基金 | `HK.02800` |

#### 美股

| 常见称呼 | 代码 |
|---------|------|
| 苹果、Apple | `US.AAPL` |
| 特斯拉、Tesla | `US.TSLA` |
| 英伟达、NVIDIA | `US.NVDA` |
| 微软、Microsoft | `US.MSFT` |
| 谷歌、Google、Alphabet | `US.GOOG` |
| 亚马逊、Amazon | `US.AMZN` |
| Meta、脸书、Facebook | `US.META` |
| 富途、Futu | `US.FUTU` |
| 台积电、TSM | `US.TSM` |
| AMD | `US.AMD` |
| 高通、Qualcomm | `US.QCOM` |
| 奈飞、Netflix | `US.NFLX` |
| 迪士尼、Disney | `US.DIS` |
| 摩根大通、JPMorgan、JPM | `US.JPM` |
| 高盛、Goldman | `US.GS` |
| 阿里巴巴（美股）、BABA | `US.BABA` |
| 京东（美股）、JD | `US.JD` |
| 拼多多、PDD | `US.PDD` |
| 百度（美股）、BIDU | `US.BIDU` |
| 蔚来（美股）、NIO | `US.NIO` |
| 小鹏（美股）、XPEV | `US.XPEV` |
| 理想（美股）、LI | `US.LI` |
| 标普500 ETF、SPY | `US.SPY` |
| 纳指 ETF、QQQ | `US.QQQ` |

#### A 股

| 常见称呼 | 代码 |
|---------|------|
| 贵州茅台、茅台 | `SH.600519` |
| 平安银行 | `SZ.000001` |
| 中国平安 | `SH.601318` |
| 招商银行 | `SH.600036` |
| 宁德时代 | `SZ.300750` |
| 五粮液 | `SZ.000858` |


## 行情命令

## 目录

- 获取市场快照
- 获取 K 线
- 获取买卖盘
- 获取逐笔成交
- 获取分时数据
- 获取市场状态
- 获取资金流向
- 获取资金分布
- 获取板块列表
- 获取板块成分股 / 指数成分股
  - 板块查询工作流
- 获取股票信息
- 搜索行情标的
- 搜索资讯
- 条件选股
- 筛选正股 V2（推荐用于复杂因子）
- 筛选窝轮 V2
- 获取股票所属板块

---

### 获取市场快照
当用户问 "报价"、"价格"、"行情" 时：
```bash
python skills/futuapi/scripts/quote/get_snapshot.py US.AAPL HK.00700 [--json]
```

### 获取 K 线
当用户问 "K线"、"蜡烛图"、"历史走势" 时：
```bash
# 实时 K 线（最近 N 根）
python skills/futuapi/scripts/quote/get_kline.py HK.00700 --ktype 1d --num 10

# 历史 K 线（日期范围）
python skills/futuapi/scripts/quote/get_kline.py HK.00700 --ktype 1d --start 2025-01-01 --end 2025-12-31
```
- `--ktype`: 1m, 3m, 5m, 15m, 30m, 60m, 1d, 1w, 1M, 1Q, 1Y
- `--rehab`: none(不复权), forward(前复权, 默认), backward(后复权)
- `--num`: 实时 K 线数量（默认 10）
- `--session`: 美股分时段历史K线，可选 NONE/RTH/ETH/ALL（仅美股历史K线，不支持 OVERNIGHT）
- `--json`: JSON 格式输出

### 获取买卖盘
当用户问 "买卖盘"、"摆盘"、"depth"、"碎股盘" 时：
```bash
python skills/futuapi/scripts/quote/get_orderbook.py HK.00700 --num 10 [--json]
# 碎股盘（仅支持 MY/SG 市场）
python skills/futuapi/scripts/quote/get_orderbook.py MY.1155 --type ODD [--json]
```
- `--type`: NORMAL=整股盘（默认），ODD=碎股盘
- 碎股盘仅支持 MY 与 SG 市场，其他市场传 ODD 会报错
- 返回新增 `order_book_type` 字段标识当前盘类型

### 获取逐笔成交
当用户问 "逐笔"、"成交明细"、"ticker" 时：
```bash
python skills/futuapi/scripts/quote/get_ticker.py HK.00700 --num 20 [--json]
```

### 获取分时数据
当用户问 "分时"、"intraday" 时：
```bash
python skills/futuapi/scripts/quote/get_rt_data.py HK.00700 [--json]
```

### 获取市场状态
当用户问 "市场状态"、"开盘了吗" 时：
```bash
python skills/futuapi/scripts/quote/get_market_state.py HK.00700 US.AAPL [--json]
```
- 支持的市场代码前缀：HK（港股）、US（美股）、SH/SZ（A股）、SG（新加坡）、MY（马来西亚）、JP（日本）

### 获取资金流向
当用户问 "资金流向"、"资金流入流出" 时：
```bash
python skills/futuapi/scripts/quote/get_capital_flow.py HK.00700 [--json]
```

### 获取资金分布
当用户问 "资金分布"、"大单小单"、"主力资金" 时：
```bash
python skills/futuapi/scripts/quote/get_capital_distribution.py HK.00700 [--json]
```

### 获取板块列表
当用户问 "板块列表"、"概念板块"、"行业板块" 时：
```bash
python skills/futuapi/scripts/quote/get_plate_list.py --market HK --type CONCEPT [--keyword 科技] [--limit 50] [--json]
```
- `--market`: HK, US, SH, SZ, SG, MY, JP（SG=新加坡、MY=马股、JP=日股，均仅支持正股板块）
- `--type`: ALL, INDUSTRY, REGION, CONCEPT
- `--keyword`/`-k`: 关键词过滤

### 获取板块成分股 / 指数成分股
当用户问 "板块股票"、"成分股"、"恒指成分股"、"指数成分股" 时：
```bash
python skills/futuapi/scripts/quote/get_plate_stock.py hsi [--limit 30] [--json]
python skills/futuapi/scripts/quote/get_plate_stock.py HK.BK1910 [--json]
python skills/futuapi/scripts/quote/get_plate_stock.py --list-aliases  # 列出所有别名
```
- 支持查询板块成分股和**指数成分股**（如恒生指数、恒生科技指数等）
- 内置别名：`hsi`(恒指), `hstech`(恒生科技), `hk_ai`(AI), `hk_chip`(芯片), `hk_ev`(新能源车), `us_ai`(美股AI), `us_chip`(半导体), `us_chinese`(中概股) 等

#### 板块查询工作流
1. 首次查询运行 `--list-aliases` 获取别名列表并缓存
2. 匹配用户请求与缓存别名
3. 匹配不到时用 `get_plate_list.py --keyword` 搜索
4. 用搜索到的板块代码调用 `get_plate_stock.py`

### 获取股票信息
当用户问 "股票信息"、"基本信息" 时：
```bash
python skills/futuapi/scripts/quote/get_stock_info.py US.AAPL,HK.00700 [--json]
```
- 底层使用 `get_market_snapshot`，返回包含实时行情的快照数据（含价格、市值、市盈率等）
- 每次最多 400 个标的

### 搜索行情标的
当用户问 "搜索股票"、"搜代码"、"search quote"、"找标的" 时：
```bash
python skills/futuapi/scripts/quote/get_search_quote.py keyword [--max-count 10] [--json]
```
- 按关键词搜索股票、ETF、板块等行情标的
- `max_count` 默认 10，最大 100
- 返回 `market`/`code`/`name`/`sec_type`/`is_watched`
- 限频：每 30 秒最多 10 次

示例：
```bash
python skills/futuapi/scripts/quote/get_search_quote.py aapl
python skills/futuapi/scripts/quote/get_search_quote.py 腾讯 --max-count 20 --json
```

### 搜索资讯
当用户问 "搜索资讯"、"搜新闻"、"搜公告"、"search news" 时：
```bash
python skills/futuapi/scripts/quote/get_search_news.py keyword [--max-count 10] [--news-sub-type ALL] [--json]
```
- 按关键词搜索新闻、公告、评级等资讯
- `--news-sub-type`：`ALL`（全部）/ `NEWS`（新闻）/ `NOTICE`（公告）/ `RATING`（评级）
- 返回 `title`/`news_sub_type`/`source`/`publish_time`/`view_count`/`related_securities`/`url`
- 限频：每 30 秒最多 10 次

示例：
```bash
python skills/futuapi/scripts/quote/get_search_news.py space
python skills/futuapi/scripts/quote/get_search_news.py 苹果 --news-sub-type NEWS --json
```

### 条件选股
当用户问 "选股"、"筛选"、"stock filter" 时：
```bash
python skills/futuapi/scripts/quote/get_stock_filter.py --market HK [条件] [--sort 字段] [--limit 20] [--json]
```
条件参数：
- 价格：`--min-price`, `--max-price`
- 市值（亿）：`--min-market-cap`, `--max-market-cap`
- PE：`--min-pe`, `--max-pe`
- PB：`--min-pb`, `--max-pb`
- 涨跌幅(%)：`--min-change-rate`, `--max-change-rate`
- 成交量：`--min-volume`
- 换手率(%)：`--min-turnover-rate`, `--max-turnover-rate`
- 排序：`--sort` (market_val/price/volume/turnover/turnover_rate/change_rate/pe/pb)
- `--asc`: 升序

示例：
```bash
# 港股市值前20
python skills/futuapi/scripts/quote/get_stock_filter.py --market HK --sort market_val --limit 20
# PE 在 10-30 之间
python skills/futuapi/scripts/quote/get_stock_filter.py --market US --min-pe 10 --max-pe 30
# 涨幅前10
python skills/futuapi/scripts/quote/get_stock_filter.py --market HK --sort change_rate --limit 10
```

### 筛选正股 V2（推荐用于复杂因子）
当用户希望基于多类因子（基本面 / 技术形态 / 筹码 / 热度 / 分析师评级 / 资金流 / 期权 IV/HV / 经纪商持仓）筛选正股时，优先使用 V2 接口 `get_stock_screen`：
```bash
python skills/futuapi/scripts/quote/get_stock_screen.py --config config.json [--page-from 0] [--page-count 200] [--json]
```
- 协议号 3252，因子覆盖更广（11 类共 244+）
- 数值统一传**原始值**（OpenD 负责倍率换算）：PRICE 传 10.0、MARKET_CAP 传 1e10；涨跌幅 5% 传 **5.0**（不是 0.05）
- **枚举名/单位/Term/筛选类型映射见 `docs/STOCK_SCREEN_FIELDS.md`**（按需查阅，避免猜枚举名）
- 返回 `(last_page, all_count, items)` 三元组，`items` 为 `list[dict]`，字段名取自 enum 名（如 `PRICE`/`MARKET_CAP`）
- `retrieves` 每项**单独声明**（一条 retrieve = 一个 name），不是 `fields` 数组
- 排序用 `set_sort`（单字段）或 `sorts`（多字段）：参数为 `direction` + `property_type` + `property_params={"name": ...}`，方向枚举 `ScrSortDir.ASC/DESC/ABS_ASC/ABS_DESC`
- 必须显式声明 `retrieves`，否则只返回 `stock_id`
- 港股 BMP 权限不支持；港股仅 Q1/ANNUAL，Q2/Q3/Q4 财务通常缺失
- `Term.SURPRISE_LATEST`(200~204) HK/US 当前数据通常与 `ANNUAL` 相同，慎用
- `add_kline_shape`/`add_retrieve_kline_shape` 的 `period` 必传（仅日 K=11 / 1 小时 K=21）

config.json 示例：
```json
{
  "filters": [
    {"type": "simple_field", "field": "MARKET", "values": ["HK"]},
    {"type": "simple_property", "name": "PRICE", "lower": 10.0},
    {"type": "simple_property", "name": "MARKET_CAP", "lower": 1e10},
    {"type": "cumulative_property", "name": "PRICE_CHANGE_PCT", "days": 5, "lower": 5.0}
  ],
  "retrieves": [
    {"type": "basic",  "name": "CODE"},
    {"type": "basic",  "name": "NAME"},
    {"type": "simple", "name": "PRICE"},
    {"type": "simple", "name": "MARKET_CAP"}
  ],
  "sort": {"direction": "DESC", "property_type": "simple",
           "property_params": {"name": "MARKET_CAP"}}
}
```

### 筛选窝轮 V2
当用户希望基于发行商、隐含波动率、杠杆等条件筛选窝轮/牛熊证/界内证时：
```bash
python skills/futuapi/scripts/quote/get_warrant_screen.py --market HK [--stock-owner HK.00700] [--warrant-type CALL] [--min-price 0.01 --max-price 5] [--config config.json] [--only-count] [--json]
```
- 协议号 3254；必传 `--market`：HK / SG / MY（其他不支持）
- 返回 `(last_page, all_count, DataFrame)` 三元组，DataFrame 共 43 列
- `add_interval_filter` 的 `min_val/max_val` 均为可选，**全部不传时该条件不生效**（不报错）
- 数值统一传**原始值**（OpenD 负责倍率换算）
- `WarrantType` 整数枚举：CALL=1, PUT=2, BULL=3, BEAR=4, IW=5（界内证 SDK 名为 `IW`，非 `INLINE`）
- `STOCK_OWNER` (5) 既可传 stock_id (int) 也可直接传证券代码 (str，如 `"HK.00700"`)
- 复杂条件用 `--config` JSON：`interval_filters` / `choice_filters` / `sorts`，`field_id` 支持枚举名（如 `"CURRENT_PRICE"`）或数字
- `--only-count` 时返回的 DataFrame 为空，仅 `all_count` 有效

WarrantField 常用 ID：4=ISSUER_ID, 5=STOCK_OWNER, 6=WARRANT_TYPE, 8=CURRENT_PRICE, 9=STREET_RATIO, 10=VOLUME, 16=LEVERAGE_RATIO, 19=STATUS, 23=EFFECTIVE_LEVERAGE。


### 获取股票所属板块
当用户问 "所属板块"、"属于哪些板块" 时：
```bash
python skills/futuapi/scripts/quote/get_owner_plate.py HK.00700 US.AAPL [--json]
```

---

**相关技能路由：** 相关：期权 → options.md；基本面/F10 → fundamentals.md；选股结果排序见正文。
