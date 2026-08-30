<!-- TOC: 榜单 / 日历 / 产业链 / 宏观 / 其他行情 -->
# 榜单 / 日历 / 产业链 / 宏观 / 其他行情

热门榜、领涨领跌、盘前盘后夜盘排行、财报/经济/派息日历、股息排行、产业链拓扑、宏观指标、FedWatch、热力图、涨跌分布。

## 目录

- 特色榜单
  - 获取热门榜
  - 获取领涨领跌榜
  - 获取区间涨跌幅排行
  - 获取美股盘前排行
  - 获取美股盘后排行
  - 获取美股夜盘排行
  - 获取卖空异动榜
- 财报/日历
  - 获取财报日历
  - 获取财报超预期排行
  - 获取经济事件日历
  - 获取派息日历
- 股息/特估
  - 获取股息排行
  - 获取破净高股息国央企排行
- 产业链
  - 获取产业链列表
  - 获取产业链详情
  - 获取板块关联产业链
  - 获取产业板块信息
  - 获取产业板块成分股
- 宏观数据
  - 获取宏观指标列表
  - 获取宏观指标历史数据
  - 获取 FedWatch 目标利率概率
  - 获取 FedWatch 点阵图
- 其他行情
  - 获取热力图数据
  - 获取涨跌分布
  - 获取评级变动

---

### 特色榜单

#### 获取热门榜
当用户问"热门榜"、"热股排行"、"hot list"、"热门股票排行"时：
```bash
python skills/futuapi/scripts/quote/get_hot_list.py --market US [--sort-field VOLUME_RATIO] [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --sort-field: 排序字段（VOLUME_RATIO/PRICE_CHANGE/PRICE_CHANGE_RATE/TURNOVER/VOLUME/AMPLITUDE/PRICE），默认 VOLUME_RATIO
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量 [1,35]，默认 10
- --offset: 起始偏移
- --config: JSON 筛选配置文件（HotListFilter，支持 price/volume/turnover 等筛选）

#### 获取领涨领跌榜
当用户问"领涨榜"、"领跌榜"、"涨跌排行"、"top movers"、"gainers"、"losers"时：
```bash
python skills/futuapi/scripts/quote/get_top_movers_rank.py --market US [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- --market: 市场（HK/US/MY/SG/JP），必填
- --sort-dir: 排序方向（0=降序=领涨，1=升序=领跌）
- --count: 返回数量 [1,35]，默认 10
- --config: JSON 筛选配置文件（SimpleRankFilter，含 PriceFilter）

#### 获取区间涨跌幅排行
当用户问"区间涨跌幅"、"周涨幅排行"、"月涨幅排行"、"period change rank"时：
```bash
python skills/futuapi/scripts/quote/get_period_change_rank.py --market US --period ONE_WEEK [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- --market: 市场（HK/US/MY/SG/JP），必填
- --period: 周期（ONE_WEEK/TWO_WEEKS/ONE_MONTH/TWO_MONTHS/THREE_MONTHS/SIX_MONTHS/ONE_YEAR/TWO_YEARS/THREE_YEARS/FIVE_YEARS/TEN_YEARS/YTD），必填
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量 [1,35]，默认 10
- --config: JSON 筛选配置文件（PeriodChangeRankFilter）

#### 获取美股盘前排行
当用户问"盘前排行"、"盘前涨幅"、"pre market rank"、"美股盘前"时：
```bash
python skills/futuapi/scripts/quote/get_us_pre_market_rank.py [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量 [1,35]，默认 10
- --config: JSON 筛选配置文件（SimpleRankFilter）

#### 获取美股盘后排行
当用户问"盘后排行"、"盘后涨幅"、"after hours rank"、"美股盘后"时：
```bash
python skills/futuapi/scripts/quote/get_us_after_hours_rank.py [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- 参数同盘前排行

#### 获取美股夜盘排行
当用户问"夜盘排行"、"overnight rank"、"美股夜盘"时：
```bash
python skills/futuapi/scripts/quote/get_us_overnight_rank.py [--sort-dir 0] [--count 10] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- 参数同盘前排行

#### 获取卖空异动榜
当用户问"卖空异动"、"卖空排行"、"short selling rank"、"做空排行"时：
```bash
python skills/futuapi/scripts/quote/get_short_selling_rank.py [--market US] [--sort-field SHORT_NUMBER_CHANGE] [--sort-dir 0] [--count 10] [--offset 0] [--plates US.BK2024,US.BK2025] [--json]
```

**参数说明**：
- --market: 市场（HK/US），默认 US
- --sort-field: 排序字段（SHORT_NUMBER_CHANGE/SHORT_RATIO_CHANGE/SHORT_NUMBER/SHORT_RATIO/VOLUME/POSITION_VOLUME/POSITION_RATIO/DAYS_TO_COVER/WEEK_AVG_VOLUME/WEEK_AVG_SHORT_NUMBER/WEEK_AVG_SHORT_RATIO/MONTH_AVG_VOLUME/MONTH_AVG_SHORT_NUMBER/MONTH_AVG_SHORT_RATIO）
- --count: 返回数量 [1,35]，默认 10
- --plates: 行业板块代码，逗号分隔（如 US.BK2024）

### 财报/日历

#### 获取财报日历
当用户问"财报日历"、"earnings calendar"、"财报发布日"、"业绩公告日程"时：
```bash
python skills/futuapi/scripts/quote/get_earnings_calendar.py --market US [--sort-type MARKET_CAP] [--begin-date 2026-06-23] [--end-date 2026-06-30] [--config filters.json] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --sort-type: 排序类型（MARKET_CAP/EARNINGS_TIME/NAME/CODE），默认 MARKET_CAP
- --begin-date/--end-date: 日期范围
- --config: JSON 筛选配置文件（EarningsCalendarFilter）

#### 获取财报超预期排行
当用户问"财报超预期"、"earnings beat"、"业绩超预期"、"EPS beat"时：
```bash
python skills/futuapi/scripts/quote/get_earnings_beat_rank.py --market US [--beat-type REVENUE] [--count 10] [--term Q] [--sort-field SURPRISE_PCT] [--config filters.json] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --beat-type: 超预期类型（REVENUE/EPS），默认 REVENUE
- --count: 返回数量 [1,35]，默认 10
- --term: 财报周期（Q=季度/H=半年/A=年度）
- --sort-field: 排序字段（SURPRISE_PCT/ACTUAL/CONSENSUS/MARKET_CAP）
- --config: JSON 筛选配置文件（EarningsBeatRankFilter）

#### 获取经济事件日历
当用户问"经济日历"、"economic calendar"、"经济事件"、"宏观事件日程"时：
```bash
python skills/futuapi/scripts/quote/get_economic_calendar.py --begin-date 2026-06-23 [--end-date 2026-06-30] [--markets US,HK] [--importance HIGH] [--count 50] [--json]
```

**参数说明**：
- --begin-date: 开始日期 yyyy-MM-dd，必填
- --end-date: 结束日期
- --markets: 市场列表（HK/US/SH/SG/JP/AU/MY/CA），逗号分隔
- --importance: 重要性（ALL/LOW/MEDIUM/HIGH）
- --count: 每页数量，默认 50

#### 获取派息日历
当用户问"派息日历"、"dividend calendar"、"分红日程"、"除息日"时：
```bash
python skills/futuapi/scripts/quote/get_dividend_calendar.py --market US [--date 2026-06-23] [--offset 0] [--count 10] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --date: 日期 yyyy-MM-dd
- --offset: 起始偏移
- --count: 返回数量

### 股息/特估

#### 获取股息排行
当用户问"股息排行"、"高股息"、"dividend rank"、"股息率排名"时：
```bash
python skills/futuapi/scripts/quote/get_dividend_rank.py --market US --rank-type HIGH_YIELD [--count 50] [--sort-field DIVIDEND_YIELD_TTM] [--config filters.json] [--json]
```

**参数说明**：
- --market: 市场（HK/US/MY/SG/JP），必填
- --rank-type: 排行类型（HIGH_YIELD/DIVIDEND_GROWTH），必填
- --count: 返回数量 [1,300]
- --sort-field: 排序字段（DIVIDEND_YIELD_TTM/AVG_DIVIDEND_YIELD_5Y/DISTRIBUTION_FREQUENCY/DIVIDEND_GROW_YEAR/DIVIDENDS_TTM/PAYOUT_RATIO_LFY/PRICE/MARKET_CAP/CHANGE_RATE/CHANGE_AMOUNT）
- --config: JSON 筛选配置文件（DividendRankFilter）

#### 获取破净高股息国央企排行
当用户问"破净高股息"、"国央企排行"、"high dividend SOE"、"央企高股息"时：
```bash
python skills/futuapi/scripts/quote/get_high_dividend_soe_rank.py [--sort-field DIVIDEND_YIELD_TTM] [--sort-dir 0] [--count 20] [--offset 0] [--config filters.json] [--json]
```

**参数说明**：
- --sort-field: 排序字段（MARKET_CAP/DIVIDEND_YIELD_TTM/PB/PE_TTM/PRICE/CHANGE_RATIO）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 返回数量
- --config: JSON 筛选配置文件（HighDividendSOERankFilter）
- 仅港股



---

### 产业链

#### 获取产业链列表
当用户问"产业链"、"产业链列表"、"industrial chain"、"产业链搜索"时：
```bash
python skills/futuapi/scripts/quote/get_industrial_chain_list.py --market HK [--keyword 芯片] [--count 20] [--json]
```

**参数说明**：
- --market: 市场（HK/US/CN/JP/SG/MY），必填
- --keyword: 搜索关键字
- --count: 每页数量 [1,50]
- 自动分页

#### 获取产业链详情
当用户问"产业链详情"、"产业链上下游"、"industrial chain detail"时：
```bash
python skills/futuapi/scripts/quote/get_industrial_chain_detail.py --chain-id 123 [--json]
```

**参数说明**：
- --chain-id: 产业链 ID（必填，来自 get_industrial_chain_list）

#### 获取板块关联产业链
当用户问"板块产业链"、"板块关联"、"industrial chain by plate"时：
```bash
python skills/futuapi/scripts/quote/get_industrial_chain_by_plate.py --plate-id 123 [--json]
```

**参数说明**：
- --plate-id: 产业板块 ID（必填）

#### 获取产业板块信息
当用户问"产业板块信息"、"板块简介"、"industrial plate info"时：
```bash
python skills/futuapi/scripts/quote/get_industrial_plate_info.py --plate-id 123 [--json]
```

**参数说明**：
- --plate-id: 产业板块 ID（必填）

#### 获取产业板块成分股
当用户问"产业板块成分股"、"板块成分"、"industrial plate stock"时：
```bash
python skills/futuapi/scripts/quote/get_industrial_plate_stock.py --plate-id 123 [--chain-id 456] [--markets HK,US] [--sort-field MARKET_VAL] [--ascend] [--count 50] [--json]
```

**参数说明**：
- --chain-id/--plate-id: 二选一，plate-id 优先
- --markets: 市场筛选（HK/US/CN/JP/SG/MY），逗号分隔
- --sort-field: 排序字段（CODE/CHANGE_RATE/TURNOVER/VOLUME/MARKET_VAL）
- --ascend: 升序
- 自动分页



---

### 宏观数据

#### 获取宏观指标列表
当用户问"宏观指标"、"宏观数据列表"、"macro indicator list"、"经济指标"时：
```bash
python skills/futuapi/scripts/quote/get_macro_indicator_list.py --region US [--json]
```

**参数说明**：
- --region: 国家/地区（HK/US/JP/SG/AU/CA/MY/CN），必填

#### 获取宏观指标历史数据
当用户问"宏观历史数据"、"指标历史"、"macro indicator history"、"CPI历史"、"GDP历史"时：
```bash
python skills/futuapi/scripts/quote/get_macro_indicator_history.py --indicator-id 123 [--time 2026-06-01] [--max-count 100] [--json]
```

**参数说明**：
- --indicator-id: 宏观指标 ID（必填，来自 get_macro_indicator_list）
- --time: 时间节点 yyyy-MM-dd（往前拉取）
- --max-count: 拉取条数，默认 100，上限 1000

#### 获取 FedWatch 目标利率概率
当用户问"FedWatch"、"联储利率预期"、"fed watch"、"利率概率"、"CME FedWatch"时：
```bash
python skills/futuapi/scripts/quote/get_fed_watch_target_rate.py [--json]
```

**参数说明**：
- 无参数

#### 获取 FedWatch 点阵图
当用户问"点阵图"、"FedWatch 点阵"、"dot plot"、"联储点阵图"时：
```bash
python skills/futuapi/scripts/quote/get_fed_watch_dot_plot.py [--json]
```

**参数说明**：
- 无参数

### 其他行情

#### 获取热力图数据
当用户问"热力图"、"heat map"、"板块热力图"、"行业热力图"时：
```bash
python skills/futuapi/scripts/quote/get_heat_map_data.py --market US [--sort-field CHANGE_RATE] [--ascend] [--count 30] [--plate-type INDUSTRY] [--json]
```

**参数说明**：
- --market: 市场（HK/US/CN），必填
- --sort-field: 排序字段（CHANGE_RATE/MARKET_VAL/TURNOVER/HOT）
- --plate-type: 板块类型（INDUSTRY/CONCEPT/THEME）
- 自动分页

#### 获取涨跌分布
当用户问"涨跌分布"、"rise fall distribution"、"涨跌家数"时：
```bash
python skills/futuapi/scripts/quote/get_rise_fall_distribution.py [--security HK.BK1001] [--market HK] [--json]
```

**参数说明**：
- --security: 板块代码（优先）
- --market: 市场（HK/US/CN），security 未传时使用
- 二选一

#### 获取评级变动
当用户问"评级变动"、"分析师评级变动"、"rating change"、"评级上调"、"评级下调"时：
```bash
python skills/futuapi/scripts/quote/get_rating_change.py --market US [--change-type UPGRADE] [--count 10] [--json]
```

**参数说明**：
- --market: 市场（仅 US），必填
- --change-type: 评级变动类型（UPGRADE/DOWNGRADE/NEW_RATING）
- --count: 每页数量 [1,20]
- 自动分页

---

---

**相关技能路由：** 相关：板块/成分股 → quote-commands.md；宏观指标历史见正文；产业链上下游联动。
