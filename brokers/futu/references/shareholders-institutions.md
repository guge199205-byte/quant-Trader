<!-- TOC: 股东 / 机构持仓 / ARK -->
# 股东 / 机构持仓 / ARK

持股统计、持股变动、持股明细、机构持股、内部人交易、ARK 基金持仓/交易/动态。

## 目录

- 股东
  - 获取持股统计（股东-持股统计）
  - 获取持股变动（股东-股东增减持）
  - 获取持股明细（股东-股东持股）
  - 获取机构持股（股东-机构持股）
  - 获取内部人持股列表（股东-内部人）
  - 获取内部人交易（股东-内部人）
- ARK 基金
  - 获取 ARK 基金持仓
  - 获取 ARK 主动交易聚合
  - 获取 ARK 个股交易动态
- 机构持仓
  - 获取机构列表
  - 获取机构概况
  - 获取机构持股列表
  - 获取机构持仓变动
  - 获取机构持仓行业分布

---

### 股东

#### 获取持股统计（股东-持股统计）
当用户问"持股统计"、"股权结构汇总"、"持股比例汇总"、"主要股东"、"各类股东占比"、"shareholder overview"、"ownership overview"、"流通股东比例"、"机构/个人/内部人占比" 时：
```bash
python skills/futuapi/scripts/quote/get_shareholders_overview.py [--period-id PERIOD_ID] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金；period_id 为 0 或不传时，同一次响应中额外返回可用报告期列表（holding_period 子表）

**参数说明**：
- code: 股票代码，如 HK.00700
- --period-id: 报告期 ID；传 0 或不传则返回最新数据，并额外返回可用报告期列表

#### 获取持股变动（股东-股东增减持）
当用户问"持股变动"、"股东增减持"、"增持"、"减持"、"新进"、"清仓"、"建仓"、"holding changes"、"谁在加仓"、"谁在减仓"、"最近增持" 时：
```bash
python skills/futuapi/scripts/quote/get_shareholders_holding_changes.py [--next-key NEXT_KEY] [--num NUM] [--sort-type SORT_TYPE] [--sort-column SORT_COLUMN] [--filter-type FILTER_TYPE] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金；支持分页，默认每页 10 条，最多 50 条

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --sort-type: 排序方向：1=降序（默认）2=升序
- --sort-column: 排序字段（Qot_Common.SortField）：62=持股变动数（默认）63=持股日期 64=变动比例 65=变动金额 66=持股比例
- --filter-type: 筛选类型：0=全部（默认）1=增持 2=减持 3=建仓 4=清仓

#### 获取持股明细（股东-股东持股）
当用户问"持股明细"、"股东持股"、"十大股东"、"前十大股东"、"大股东名单"、"谁持有 XX"、"持有人明细"、"holder detail"、"持股明细列表"、"流通股东明细" 时：
```bash
python skills/futuapi/scripts/quote/get_shareholders_holder_detail.py [--request-type REQUEST_TYPE] [--next-key NEXT_KEY] [--num NUM] [--sort-column SORT_COLUMN] [--sort-type SORT_TYPE] [--period-id PERIOD_ID] [--holder-id HOLDER_ID] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金；支持分页，默认每页 10 条；分页标识为字符串类型

**参数说明**：
- code: 股票代码，如 HK.00700
- --request-type: 请求类型：0=默认，1000=全部，1=其他机构，2=传统投资经理，3=对冲基金，4=风险资本/私募，5=企业年金，6=基金会基金，7=保险公司，8=银行/投资银行，9=家族办公室/信托，10=主权财富基金，11=REIT，12=结构化融资经理，13=联合养老金，14=政府养老金，15=捐赠基金，100=个人，200=ADS，300=上市公司，400=未公开上市公司，500=国有股
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --sort-column: 排序列（Qot_Common.SortField）：61=持股股数（默认）62=持股变动数
- --sort-type: 排序方式：1=降序（默认），2=升序
- --period-id: 报告期 ID，0=最新
- --holder-id: 持有人对象 ID，0=不过滤；可取自 GetShareholdersOverview/GetShareholdersHoldingChanges/本协议/GetInsiderHolderList/GetInsiderTradeList返回的 holder_id

#### 获取机构持股（股东-机构持股）
当用户问"机构持股"、"机构股东"、"institutional holdings"、"institutional investors"、"机构持股变化"、"机构持股比例"、"机构持仓"、"基金持仓"、"13F" 时：
```bash
python skills/futuapi/scripts/quote/get_shareholders_institutional.py [--next-key NEXT_KEY] [--num NUM] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

#### 获取内部人持股列表（股东-内部人）
当用户问"内部人持股"、"高管持股"、"董事持股"、"大股东持股"、"insider holder"、"insider ownership"、"内部人名单"、"美股内部人"、"公司高管买了多少股" 时：
```bash
python skills/futuapi/scripts/quote/get_insider_holder_list.py [--next-key NEXT_KEY] [--num NUM] [--json] code
```
**接口限制（市场）**：支持美股正股及基金；首页额外返回内部人统计摘要（总人数/增持数/减持数），续页无此摘要

**参数说明**：
- code: 股票代码，如 US.AAPL
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~20

#### 获取内部人交易（股东-内部人）
当用户问"内部人交易"、"内部人买卖"、"高管交易"、"董事交易"、"insider trading"、"insider trade"、"insider buying"、"insider selling"、"Form 4"、"高管在买还是在卖" 时：
```bash
python skills/futuapi/scripts/quote/get_insider_trade_list.py [--holder-id HOLDER_ID] [--next-key NEXT_KEY] [--num NUM] [--json] code
```
**接口限制（市场）**：支持美股正股及基金

**参数说明**：
- code: 股票代码，如 US.AAPL
- --holder-id: 持有人对象 ID，不传则查询全部内部人（可选）；可取自 GetInsiderHolderList或本协议返回的 holder_id
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50



---

### ARK 基金

#### 获取 ARK 基金持仓
当用户问"ARK持仓"、"ARK基金"、"ark fund holding"、"方舟基金"时：
```bash
python skills/futuapi/scripts/quote/get_ark_fund_holding.py [--holding-type POSITION] [--cycle ONE_DAY] [--sort-field SHARES] [--sort-dir 0] [--count 20] [--json]
```

**参数说明**：
- --holding-type: 持仓类型（POSITION/INCREASE/DECREASE/NEW/SOLD_OUT）
- --cycle: 周期（ONE_DAY/FIVE_DAY/TEN_DAY/THIRTY_DAY/SIXTY_DAY）
- --sort-field: 排序字段（SHARES/WEIGHT_CHANGE/SHARES_CHANGE/MARKET_VALUE/WEIGHT）
- --sort-dir: 排序方向（0=降序，1=升序）
- --count: 每页数量
- 自动分页获取全部数据

#### 获取 ARK 主动交易聚合
当用户问"ARK交易"、"ARK买卖"、"ark active transaction"、"方舟买入"、"方舟卖出"时：
```bash
python skills/futuapi/scripts/quote/get_ark_active_transaction.py [--holding-type INCREASE] [--cycle ONE_DAY] [--sort-field CHANGE_AMOUNT] [--sort-dir 0] [--count 20] [--json]
```

**参数说明**：
- --holding-type: 持仓类型（INCREASE/DECREASE/NEW/SOLD_OUT）
- --cycle: 周期（同上）
- --sort-field: 排序字段（CHANGE_AMOUNT/CHANGE_SHARES）
- 自动分页

#### 获取 ARK 个股交易动态
当用户问"ARK个股"、"ARK持有"、"ark stock dynamic"、"方舟持有什么"时：
```bash
python skills/futuapi/scripts/quote/get_ark_stock_dynamic.py --code US.TSLA [--json]
```

**参数说明**：
- --code: 股票代码（如 US.TSLA），必填



---

### 机构持仓

#### 获取机构列表
当用户问"机构列表"、"机构排行"、"institution list"、"基金公司"时：
```bash
python skills/futuapi/scripts/quote/get_institution_list.py --market US [--sort-field POSITION_VALUE] [--sort-dir 0] [--count 20] [--name 桥水] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --sort-field: 排序字段（POSITION_VALUE/POSITION_VALUE_CHANGE/POSITION_COUNT/POSITION_COUNT_CHANGE）
- --name: 机构名模糊搜索
- 自动分页

#### 获取机构概况
当用户问"机构概况"、"机构信息"、"institution profile"时：
```bash
python skills/futuapi/scripts/quote/get_institution_profile.py --market US --institution-id 123 [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --institution-id: 机构 ID（必填）

#### 获取机构持股列表
当用户问"机构持股"、"机构重仓"、"institution holding"、"持仓列表"时：
```bash
python skills/futuapi/scripts/quote/get_institution_holding_list.py --market US --institution-id 123 [--change-type INCREASE] [--sort-field HOLDING_VALUE] [--sort-dir 0] [--count 20] [--keyword TSLA] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --institution-id: 机构 ID（必填）
- --change-type: 变动类型筛选（NEW/SOLD_OUT/INCREASE/DECREASE）
- --sort-field: 排序字段（HOLDING_VALUE/HOLDING_PCT/LAST_HOLDING_PCT/CHANGE_SHARES/CHANGE_PCT/PORTFOLIO_PCT/INDUSTRY/HOLDING_DATE）
- --keyword: 搜索关键词
- 自动分页

#### 获取机构持仓变动
当用户问"机构变动"、"机构建仓"、"机构增仓"、"institution holding change"时：
```bash
python skills/futuapi/scripts/quote/get_institution_holding_change.py --market US --institution-id 123 [--change-type NEW] [--sort-field CHANGE_PCT] [--sort-dir 0] [--count 20] [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --institution-id: 机构 ID（必填）
- --change-type: 变动类型（NEW/SOLD_OUT/INCREASE/DECREASE）
- --sort-field: 排序字段（CHANGE_PCT/CHANGE_SHARES/HOLDING_DATE）
- 自动分页

#### 获取机构持仓行业分布
当用户问"机构行业分布"、"持仓分布"、"institution distribution"时：
```bash
python skills/futuapi/scripts/quote/get_institution_distribution.py --market US --institution-id 123 [--json]
```

**参数说明**：
- --market: 市场（HK/US），必填
- --institution-id: 机构 ID（必填）

---

**相关技能路由：** 相关：基本面 → fundamentals.md；机构榜单 → rankings-calendar.md。
