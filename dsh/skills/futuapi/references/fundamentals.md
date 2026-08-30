<!-- TOC: F10 基本面 / 研究 / 公司行动 / 简况 / 经纪商 / 卖空 -->
# F10 基本面 / 研究 / 公司行动 / 简况 / 经纪商 / 卖空

财务报表、营收拆分、分析师评级、晨星研报、估值、公司行动（分红/回购/拆股）、公司简况、高管、经营效率、十大经纪商、卖空。

> 股东/机构持仓见 `shareholders-institutions.md`；期权数据见 `options.md`；榜单/日历/宏观见 `rankings-calendar.md`。

## F10 基本面 / 研究 / 公司行动 / 股东 / 简况

下列 27 个接口覆盖牛牛客户端个股相关数据模块（财务、预测、公司行动、股东、公司简况、经纪商、卖空、期权数据）相关，脚本使用方法和使用限制可以查看对应脚本开头介绍，或者运行脚本加 [-h] 参数查看详情，如
```bash
python skills/futuapi/scripts/quote/get_financials_earnings_price_move.py -h
```
## 目录

- 财务 — 财报分析
  - 获取个股财报日前后价格涨跌幅表现（财务-财报分析-历史财报日涨跌幅&波动率）
  - 获取个股财报日前后股价历史（财务-财报分析-历史财报日数据明细）
- 财务 — 财报与主营
  - 获取财务报表（财务-关键指标/利润表/资产负债表/现金流量表）
  - 获取主营构成（财务-主营构成）
- 预测 — 分析师评级
  - 获取分析师综合评级与目标价（预测-分析师评级）
  - 获取评级汇总 / 机构-分析师详情（预测-分析师评级）
- 预测 — 晨星研报
  - 获取晨星研究报告（预测-晨星研报）
- 预测 — 公司估值
  - 获取估值详情（预测-公司估值）
  - 获取板块/指数成分股估值列表（预测-公司估值）
- 公司行动
  - 获取分红派息（公司行动-分红派息）
  - 获取回购（公司行动-回购）
  - 获取拆合股（公司行动-拆股并股）
- 股东
  - 获取持股统计（股东-持股统计）
  - 获取持股变动（股东-股东增减持）
  - 获取持股明细（股东-股东持股）
  - 获取机构持股（股东-机构持股）
  - 获取内部人持股列表（股东-内部人）
  - 获取内部人交易（股东-内部人）
- 简况
  - 获取公司详情（简况-公司概况）
  - 获取公司高管信息（简况-公司高管）
  - 获取公司高管背景（简况-公司高管）
  - 获取公司经营效率（简况-经营效率）
- 经纪商
  - 获取十大买卖经纪商（十大买卖经纪商）
- 卖空
  - 获取每日卖空（每日卖空）
  - 获取空头持仓（空头持仓）

---

### 财务 — 财报分析

#### 获取个股财报日前后价格涨跌幅表现（财务-财报分析-历史财报日涨跌幅&波动率）
当用户问"历史财报日涨跌幅"、"财报前后涨跌幅"、"财报日波动率"、"财报前后IV/HV"、"财报前后5日价格"时：
```bash
python skills/futuapi/scripts/quote/get_financials_earnings_price_move.py [--period-count N] [--json] code
```
**接口限制（市场）**：支持港股、美股正股

**参数说明**：
- code: 股票代码，如 HK.00700
- --period-count: 财报周期数量，默认 10，范围 1-50

#### 获取个股财报日前后股价历史（财务-财报分析-历史财报日数据明细）
当用户问"历史财报日数据明细"、"财报日股价历史"、"财报日逐日数据"、"IV Crush"、"财报前后隐波变化"、"财报预期波动率"、"财报日明细"、"每期财报明细" 、"下次/最新财报时间"时：
```bash
python skills/futuapi/scripts/quote/get_financials_earnings_price_history.py [--json] code
```
**接口限制（市场）**：支持港股、美股正股

**参数说明**：
- code: 股票代码，如 HK.00700

### 财务 — 财报与主营

#### 获取财务报表（财务-关键指标/利润表/资产负债表/现金流量表）
当用户问"财务报表"、"财报"、"利润表"、"资产负债表"、"现金流量表"、"关键指标"、"三大表"、"income statement"、"balance sheet"、"cash flow"、"营收多少"、"净利润多少"、"毛利率"、"ROE"、"EPS" 时：
```bash
python skills/futuapi/scripts/quote/get_financials_statements.py [--statement-type STATEMENT_TYPE] [--financial-type FINANCIAL_TYPE] [--currency-code CURRENCY_CODE] [--next-key KEY] [--num N] [--json] code
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- --statement-type: 财务报表类型（必填可选）：1=利润表(Income) 2=资产负债表(BalanceSheet) 3=现金流量表(CashFlow) 4=关键指标(MainIndex)；（默认：1=利润表）
- --financial-type: 财报类型：1=Q1单季报 2=Q2单季报 3=Q3单季报 4=Q4单季报 5=Q6累计报(Q1+Q2) 6=Q9累计报(Q1+Q2+Q3) 7=年报 9=单季报组合(Q1/Q2/Q3/Q4) 10=单季报+年报 11=累计季报(Q1/Q6/Q9/年报)；（默认：10=单季报+年报）
- --currency-code: 币种代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不填返回原始货币数据（默认：空=原始货币）
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

#### 获取主营构成（财务-主营构成）
当用户问"主营构成"、"主营业务"、"收入构成"、"营收拆分"、"产品收入占比"、"行业收入占比"、"地区收入占比"、"分业务收入"、"revenue breakdown"、"营收结构" 时：
```bash
python skills/futuapi/scripts/quote/get_financials_revenue_breakdown.py [--date DATE] [--financial-type FINANCIAL_TYPE] [--currency-code CURRENCY_CODE] [--json] code
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- --date: 筛选时间戳；从输出 screen_date_list 取 date 值可查历史；不填返回最新一期
- --financial-type: 财报类型：1=Q1单季报 2=Q2单季报 3=Q3单季报 4=Q4单季报 5=半年报 6=Q9累计报 7=年报 9=聚合季报
- --currency-code: 币种代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不填返回原始货币数据

**返回说明**：返回产品、行业、地区、业务各维度数据；`breakdown_list` 中每个分组含 `type`（维度类型）和 `item_list`；`screen_date_list` 仅在 `--date` 与 `--financial-type` 均未传时返回

### 预测 — 分析师评级

#### 获取分析师综合评级与目标价（预测-分析师评级）
当用户问"分析师评级"、"一致预期"、"目标价"、"综合评级"、"consensus"、"analyst rating"、"分析师看多还是看空"、"买入评级占比"、"平均目标价"、"最高/最低目标价"、"多少分析师覆盖" 时：
```bash
python skills/futuapi/scripts/quote/get_research_analyst_consensus.py [--json] code
```
**接口限制（市场）**：支持正股及 REIT

**参数说明**：
- code: 股票代码，如 HK.00700

#### 获取评级汇总 / 机构-分析师详情（预测-分析师评级）
当用户问"评级汇总"、"机构评级"、"哪些机构给出评级"、"评级列表"、"分析师评级明细"、"rating summary"、"某家机构对 XX 的评级记录"、"某分析师历史评级"、"机构目标价"、"分析师目标价" 时：
```bash
python skills/futuapi/scripts/quote/get_research_rating_summary.py [--rating-dimension-type RATING_DIMENSION_TYPE] [--uid UID] [--next-key NEXT_KEY] [--num NUM] [--json] code
```
**接口限制（市场）**：支持美股正股及 REIT

**参数说明**：
- code: 股票代码，如 US.AAPL
- --rating-dimension-type: 评级维度类型：1=机构维度（默认） 2=分析师维度
- --uid: 空=汇总列表；非空=指定机构/分析师的评级详情（如分析师 uid 须搭配 --rating-dimension-type 2）
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~20

### 预测 — 晨星研报

#### 获取晨星研究报告（预测-晨星研报）
当用户问"晨星研报"、"晨星报告"、"Morningstar"、"晨星星级"、"公允价值"、"fair value"、"护城河"、"经济护城河"、"economic moat"、"多空观点"、"bull case"、"bear case"、"分析师观点"、"晨星评分" 时：
```bash
python skills/futuapi/scripts/quote/get_research_morningstar_report.py [--json] code
```
**接口限制（市场）**：支持正股及 REIT

**参数说明**：
- code: 股票代码，如 HK.00700

### 预测 — 公司估值

#### 获取估值详情（预测-公司估值）
当用户问"估值详情"、"公司估值"、"PE"、"PB"、"PS"、"市盈率"、"市净率"、"市销率"、"历史估值"、"估值分位"、"估值分布"、"估值趋势"、"相对板块估值"、"相对市场估值"、"利润增速估值" 时：
```bash
python skills/futuapi/scripts/quote/get_valuation_detail.py [--valuation-type VALUATION_TYPE] [--interval-type INTERVAL_TYPE] [--json] code
```
**接口限制（市场）**：支持正股、基金及指数；PB 估值类型无盈利增速模块；指数无排名、均值、中位数字段

**参数说明**：
- code: 股票或指数代码，如 HK.00700
- --valuation-type: 估值类型：1=PE, 2=PB, 3=PS（默认不传，服务端推荐）
- --interval-type: 时间周期（有效值 1-10）：1=3月 2=6月 3=1年 4=3年 5=从2019年起 6=5年 7=10年 8=2年 9=20年 10=30年（默认：3=1年）

#### 获取板块/指数成分股估值列表（预测-公司估值）
当用户问"板块估值"、"指数估值"、"成分股估值"、"板块内估值排名"、"行业估值比较"、"指数成分股估值"、"哪些成分股估值最便宜"、"哪些成分股估值最贵" 时：
```bash
python skills/futuapi/scripts/quote/get_valuation_plate_stock_list.py [--valuation-type VALUATION_TYPE] [--next-key NEXT_KEY] [--num NUM] [--sort-type SORT_TYPE] [--sort-id SORT_ID] [--filter-security FILTER_SECURITY] [--json] code
```
**接口限制（市场）**：支持板块和指数；不支持个股；指数作为入参时，首次请求额外返回所属板块列表（plate_list）

**参数说明**：
- code: 板块或指数代码，如 HK.800000
- --valuation-type: 估值类型：1=市盈率(PE), 2=市净率(PB), 3=市销率(PS)（默认：1=市盈率(PE)）
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --sort-type: 排序方向：1=Desc(降序), 2=Asc(升序)（默认：2=升序）
- --sort-id: 排序列（Qot_Common.SortField）：51=市值（默认）52=估值 53=预测估值 54=历史分位
- --filter-security: 仅对指数有效：按行业/板块筛选成分股（如 HK.LIST23363）；不传则不筛选

### 公司行动

#### 获取分红派息（公司行动-分红派息）
当用户问"分红"、"派息"、"股息"、"分红派息"、"dividend"、"除权除息日"、"登记日"、"派息日"、"分配方案"、"分红历史"、"每股派息" 时：
```bash
python skills/futuapi/scripts/quote/get_corporate_actions_dividends.py [--json] code
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700

#### 获取回购（公司行动-回购）
当用户问"回购"、"股票回购"、"公司回购"、"buyback"、"回购记录"、"回购历史"、"回购金额"、"港股回购"、"A 股回购" 时：
```bash
python skills/futuapi/scripts/quote/get_corporate_actions_buybacks.py [--next-key NEXT_KEY] [--num NUM] [--json] code
```
**接口限制（市场）**：支持港股、A股正股及基金；港股和A股各返回独立数据表，字段结构不同

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

#### 获取拆合股（公司行动-拆股并股）
当用户问"拆股"、"并股"、"拆合股"、"股票拆分"、"合股"、"stock split"、"reverse split"、"拆股历史"、"拆股比例"、"拆股日期" 时：
```bash
python skills/futuapi/scripts/quote/get_corporate_actions_stock_splits.py [--next-key KEY] [--num N] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

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

### 简况

#### 获取公司详情（简况-公司概况）
当用户问"公司概况"、"公司详情"、"公司介绍"、"公司简介"、"company profile"、"公司资料"、"主营业务是什么"、"公司官网"、"总部地址"、"上市地" 时：
```bash
python skills/futuapi/scripts/quote/get_company_profile.py [--json] code
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700

#### 获取公司高管信息（简况-公司高管）
当用户问"公司高管"、"董事及高管"、"高管名单"、"管理层"、"董事会"、"executives"、"board members"、"CEO 是谁"、"CFO 是谁"、"高管薪酬"、"高管持股数"、"高管性别/年龄" 时：
```bash
python skills/futuapi/scripts/quote/get_company_executives.py [--json] code
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700

#### 获取公司高管背景（简况-公司高管）
当用户问"高管背景"、"高管简历"、"高管履历"、"CEO 背景"、"executive background"、"高管从业经历"、"XX 是谁" 时：
**注意**：`leader_name` 在 Git Bash 下直接传中文可能乱码，建议改用 Unicode 转义序列（如 `张三` → `\u5f20\u4e09`），脚本会自动解码为正确字符。
```bash
python skills/futuapi/scripts/quote/get_company_executive_background.py [--json] code leader_name
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- leader_name: 高管姓名，使用 get_company_executives.py 返回的 leader_name 字段值；支持直接传中文（如 "张三"）或 Unicode 转义序列（如 "\u5f20\u4e09"），两种方式等价

#### 获取公司经营效率（简况-经营效率）
当用户问"经营效率"、"员工数"、"雇员人数"、"人均营收"、"人均利润"、"operational efficiency"、"员工效率"、"人均薪酬" 时：
```bash
python skills/futuapi/scripts/quote/get_company_operational_efficiency.py [--next-key NEXT_KEY] [--num NUM] [--currency-code CURRENCY_CODE] [--json] code
```
**接口限制（市场）**：支持正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50
- --currency-code: 货币代码（ISO 4217），如 CNY、USD、HKD、SGD、JPY、CAD、AUD；不传返回默认货币

### 经纪商

#### 获取十大买卖经纪商（十大买卖经纪商）
当用户问"十大买卖经纪商"、"十大净买入经纪"、"十大净卖出经纪"、"大单经纪"、"经纪队列排名"、"broker ranking"、"高盛在买还是在卖"、"港股经纪动向"、"席位资金" 时：
```bash
python skills/futuapi/scripts/quote/get_top_ten_buy_sell_brokers.py [--days-before DAYS_BEFORE] [--json] code
```
**接口限制（市场）**：支持港股正股及基金；days_before=0 返回实时数据（含均价/总量/总额），days_before>0 仅含净量和经纪商名称

**参数说明**：
- code: 股票代码，如 HK.00700
- --days-before: 距当前交易日天数，0=实时，>0=历史第 N 个交易日（默认不填=实时）

### 卖空

#### 获取每日卖空（每日卖空）
当用户问"每日卖空"、"卖空数据"、"卖空量"、"卖空比例"、"short volume"、"daily short"、"当日卖空额"、"卖空占比"、"sell short" 时：
```bash
python skills/futuapi/scripts/quote/get_daily_short_volume.py [--next-key NEXT_KEY] [--num NUM] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

#### 获取空头持仓（空头持仓）
当用户问"空头持仓"、"short interest"、"空头持仓量"、"空头比例"、"short ratio"、"回补天数"、"days to cover"、"做空比例"、"浮动流通空头占比" 时：
```bash
python skills/futuapi/scripts/quote/get_short_interest.py [--next-key NEXT_KEY] [--num MAX_COUNT] [--json] code
```
**接口限制（市场）**：支持港股、美股正股及基金；单次最多返回 50 条，默认 10 条

**参数说明**：
- code: 股票代码，如 HK.00700
- --next-key: 分页标识，首次不传，续拉填上次返回的 next_key；"-1" 表示无更多数据
- --num: 每页返回数量，默认 10，范围 1~50

---

**相关技能路由：** 相关：股东/机构/ARK → shareholders-institutions.md；估值分位榜单 → rankings-calendar.md；财报点评卡片 → `references/analysis-frameworks.md`。
