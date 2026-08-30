<!-- TOC: 脚本目录（Script Index） -->
# 脚本目录（Script Index）

scripts/{quote,trade,subscribe} 完整脚本清单。

```
├── SKILL.md
└── scripts/
    ├── common.py                     # 公共工具与配置
    ├── quote/                        # 行情脚本
    │   ├── collect.py                # 一键汇总某标的全景（行情+财报+评级+估值+期权）
    │   ├── get_snapshot.py                        # 市场快照（无需订阅）
    │   ├── get_kline.py                           # K 线数据（实时/历史）
    │   ├── get_stock_quote.py         # 已订阅股票的实时报价
    │   ├── get_orderbook.py                       # 买卖盘/摆盘
    │   ├── get_ticker.py                          # 逐笔成交
    │   ├── get_broker_queue.py        # 经纪买卖队列
    │   ├── get_rt_data.py                         # 分时数据
    │   ├── get_rehab.py               # 复权因子
    │   ├── get_market_state.py                    # 市场状态
    │   ├── get_global_state.py        # OpenD 全局状态
    │   ├── get_trading_days.py        # 交易日列表
    │   ├── get_capital_flow.py                    # 资金流向
    │   ├── get_capital_distribution.py            # 资金分布
    │   ├── get_plate_list.py                      # 板块列表
    │   ├── get_plate_stock.py                     # 板块成分股
    │   ├── get_stock_info.py                      # 股票基本信息
    │   ├── get_search_quote.py                    # 搜索行情标的
    │   ├── get_search_news.py                     # 搜索资讯
    │   ├── get_stock_filter.py                    # 条件选股（V1，旧）
    │   ├── get_stock_screen.py                    # 筛选正股 V2（新，因子覆盖更广）
    │   ├── get_owner_plate.py                     # 股票所属板块
    │   ├── get_referencestock_list.py # 正股关联的窝轮/期货
    │   ├── get_warrant.py             # 窝轮/牛熊证列表
    │   ├── get_warrant_screen.py     # 筛选窝轮 V2（HK/SG/MY，43 列）
    │   ├── get_option_expiration_date.py          # 期权到期日
    │   ├── get_option_chain.py                    # 期权链
    │   ├── get_option_screen.py                   # 筛选期权（混合 underlying + option 因子）
    │   ├── resolve_option_code.py     # 解析期权简写代码
    │   ├── get_future_info.py         # 期货合约信息
    │   ├── get_ipo_list.py            # IPO 信息列表
    │   ├── get_history_kl_quota.py    # 历史 K 线额度
    │   ├── get_user_info.py           # 用户行情权限信息
    │   ├── get_user_security.py       # 自选股列表
    │   ├── get_user_security_group.py # 自选股分组列表
    │   ├── modify_user_security.py    # 添加/删除自选股
    │   ├── get_price_reminder.py      # 到价提醒列表
    │   ├── set_price_reminder.py      # 设置到价提醒
	│   ├── get_financials_earnings_price_move.py          # 历史财报日涨跌幅&波动率
    │   ├── get_financials_earnings_price_history.py       # 历史财报日数据明细
    │   ├── get_financials_statements.py                   # 财务报表（利润/资产负债/现金流/关键指标）
    │   ├── get_financials_revenue_breakdown.py            # 主营构成（产品/行业/地区/业务）
    │   ├── get_research_analyst_consensus.py              # 分析师综合评级与目标价
    │   ├── get_research_rating_summary.py                 # 评级汇总 / 机构-分析师详情
    │   ├── get_research_morningstar_report.py             # 晨星研究报告
    │   ├── get_valuation_detail.py                        # 估值详情（PE/PB/PS 趋势/分布）
    │   ├── get_valuation_plate_stock_list.py              # 板块/指数成分股估值列表
    │   ├── get_corporate_actions_dividends.py             # 分红派息
    │   ├── get_corporate_actions_buybacks.py              # 回购
    │   ├── get_corporate_actions_stock_splits.py          # 拆合股
    │   ├── get_shareholders_overview.py                   # 持股统计
    │   ├── get_shareholders_holding_changes.py            # 持股变动（增持/减持/新进/清仓）
    │   ├── get_shareholders_holder_detail.py              # 持股明细
    │   ├── get_shareholders_institutional.py              # 机构持股历史
    │   ├── get_insider_holder_list.py                     # 内部人持股列表（仅美股）
    │   ├── get_insider_trade_list.py                      # 内部人交易（仅美股）
    │   ├── get_company_profile.py                         # 公司详情/概况
    │   ├── get_company_executives.py                      # 公司高管信息
    │   ├── get_company_executive_background.py            # 公司高管背景
    │   ├── get_company_operational_efficiency.py          # 公司经营效率（员工数/人均营收/利润）
    │   ├── get_top_ten_buy_sell_brokers.py                # 十大买卖经纪商（仅港股）
    │   ├── get_daily_short_volume.py                      # 每日卖空
    │   ├── get_short_interest.py                          # 空头持仓
    │   ├── get_option_volatility.py                       # 期权波动率分析
    │   ├── get_option_exercise_probability.py             # 期权行权概率
    │   ├── get_option_strategy.py                         # 期权策略组合腿列表
    │   ├── get_option_strategy_spread.py                  # 期权策略有效价差
    │   ├── get_option_quote.py                            # 期权快照行情
    │   ├── get_option_strategy_analysis.py                # 期权策略损益分析
    │   ├── get_option_market_statistic.py                 # 期权市场统计（成交量/持仓量时间序列）
    │   ├── get_option_underlying_his_statistic.py         # 期权标的历史统计（P/C比率时间序列）
    │   ├── get_option_underlying_overview.py              # 批量标的最新数据（IV/HV多周期快照）
    │   ├── get_option_underlying_his_volatility.py        # 期权标的历史波动率（IV/HV时间序列）
    │   ├── get_option_underlying_rank.py                  # 期权标的排行（13种排序+筛选）
    │   ├── get_option_rank.py                             # 期权合约排行（10种排序+筛选）
    │   ├── get_option_event.py                            # 期权异动列表（25+种筛选因子）
    │   ├── get_option_event_alert.py                      # 获取期权异动告警设置
    │   ├── set_option_event_alert.py                      # 修改期权异动告警条件
    │   ├── get_option_zero_dte_screener.py                # 末日期权标的列表（0DTE筛选）
    │   ├── get_option_zero_dte_contract.py                # 末日期权合约列表（0DTE合约详情）
    │   ├── get_option_earnings_screener.py                # 财报期权标的列表（IV Crush/预期波动）
    │   ├── get_option_seller_screener.py                  # 期权卖方策略列表（CC/CSP筛选）
    │   ├── get_indicator_list.py                          # 指标列表（全部可用指标）
    │   ├── get_indicator_calc_result.py                   # 指标计算结果（K线+指标参数→推送结果）
    │   ├── get_hot_list.py                                # 热门榜（量比/涨跌/换手等排序）
    │   ├── get_top_movers_rank.py                         # 领涨领跌榜
    │   ├── get_period_change_rank.py                      # 区间涨跌幅排行
    │   ├── get_us_pre_market_rank.py                      # 美股盘前排行
    │   ├── get_us_after_hours_rank.py                     # 美股盘后排行
    │   ├── get_us_overnight_rank.py                       # 美股夜盘排行
    │   ├── get_short_selling_rank.py                      # 卖空异动榜
    │   ├── get_earnings_calendar.py                       # 财报日历
    │   ├── get_earnings_beat_rank.py                      # 财报超预期排行
    │   ├── get_economic_calendar.py                       # 经济事件日历
    │   ├── get_dividend_calendar.py                       # 派息日历
    │   ├── get_dividend_rank.py                           # 股息排行
    │   ├── get_high_dividend_soe_rank.py                  # 破净高股息国央企排行（港股）
    │   ├── get_ark_fund_holding.py                        # ARK 基金持仓
    │   ├── get_ark_active_transaction.py                  # ARK 主动交易聚合
    │   ├── get_ark_stock_dynamic.py                       # ARK 个股交易动态
    │   ├── get_industrial_chain_list.py                   # 产业链列表
    │   ├── get_industrial_chain_detail.py                 # 产业链详情
    │   ├── get_industrial_chain_by_plate.py               # 板块关联产业链
    │   ├── get_industrial_plate_info.py                   # 产业板块信息
    │   ├── get_industrial_plate_stock.py                  # 产业板块成分股
    │   ├── get_institution_list.py                        # 机构列表
    │   ├── get_institution_profile.py                     # 机构概况
    │   ├── get_institution_holding_list.py                # 机构持股列表
    │   ├── get_institution_holding_change.py              # 机构持仓变动
    │   ├── get_institution_distribution.py                # 机构持仓行业分布
    │   ├── get_macro_indicator_list.py                    # 宏观指标列表
    │   ├── get_macro_indicator_history.py                 # 宏观指标历史数据
    │   ├── get_fed_watch_target_rate.py                   # FedWatch 目标利率概率
    │   ├── get_fed_watch_dot_plot.py                      # FedWatch 点阵图
    │   ├── get_heat_map_data.py                           # 热力图数据
    │   ├── get_rise_fall_distribution.py                  # 涨跌分布
    │   ├── get_rating_change.py                           # 评级变动
    │   ├── get_event_contract_category.py                 # 预测市场分类列表
    │   ├── filter_competition.py                          # 预测市场赛事筛选
    │   ├── get_event_contract_series_list.py              # 预测市场 Series 列表
    │   ├── get_event_contract_event_list.py               # 预测市场 Event 列表
    │   ├── get_event_contract.py                          # 预测市场 Contract 列表
    │   ├── get_event_contract_milestone_list.py           # 预测市场里程碑列表
    │   ├── get_valid_combo_list.py                        # 可 Combo 事件列表（含 mvc）
    │   ├── request_combo_quotes.py                        # Combo 询价
    │   ├── get_event_contract_snapshot.py                 # 预测市场快照
    │   ├── get_event_contract_order_book.py               # 预测市场摆盘（需订阅）
    │   ├── get_event_contract_kline.py                    # 预测市场 K 线（需订阅）
    │   ├── get_event_contract_ticker.py                   # 预测市场逐笔（需订阅）
    │   └── request_history_event_contract_kline.py        # 预测市场历史 K 线（无需订阅）
    ├── trade/                        # 交易脚本
    │   ├── get_accounts.py           # 账户列表
    │   ├── get_portfolio.py          # 持仓与资金
    │   ├── get_all_portfolios.py      # 所有账户持仓资金
    │   ├── place_order.py            # 下单
    │   ├── place_combo_order.py      # 组合下单
    │   ├── modify_order.py            # 改单
    │   ├── cancel_order.py           # 撤单
    │   ├── get_orders.py             # 今日订单
    │   ├── get_history_orders.py      # 历史订单
    │   ├── get_order_fill_list.py     # 今日成交
    │   ├── get_history_order_fill_list.py # 历史成交
    │   ├── get_acc_cash_flow.py       # 现金流水
    │   ├── get_order_fee.py           # 订单费用
    │   ├── get_margin_ratio.py        # 融资融券比率
    │   ├── get_max_trd_qtys.py        # 最大可买卖数量
    │   ├── comboorder_tradinginfo_query.py # 组合可交易信息查询
    │   ├── get_crypto_accounts.py     # 加密货币账户列表
    │   ├── get_crypto_portfolio.py    # 加密货币持仓与资金
    │   ├── place_crypto_order.py      # 加密货币下单
    │   ├── cancel_crypto_order.py     # 加密货币撤单/全撤
    │   ├── get_crypto_orders.py       # 加密货币订单查询
    │   ├── get_crypto_cash_flow.py    # 加密货币资金流水
    │   ├── get_crypto_max_trd_qtys.py # 加密货币最大可买卖数量（仅现金账户）
    │   └── get_crypto_order_fee.py    # 加密货币订单费用查询
    └── subscribe/                    # 订阅脚本
        ├── subscribe.py              # 订阅行情
        ├── unsubscribe.py            # 取消订阅
        ├── unsubscribe_all.py         # 取消全部订阅
        ├── query_subscription.py     # 查询订阅状态
        ├── push_quote.py             # 接收报价推送
        ├── push_kline.py              # 接收 K 线推送
        ├── push_broker.py             # 接收经纪队列推送
        ├── push_orderbook.py          # 接收买卖盘推送
        ├── push_ticker.py             # 接收逐笔成交推送
        ├── push_rt_data.py            # 接收分时数据推送
        ├── push_option_event.py       # 接收期权异动推送
        ├── subscribe_event_contract.py    # 订阅预测市场
        ├── unsubscribe_event_contract.py  # 取消订阅预测市场
        ├── unsubscribe_all_event_contract.py  # 取消所有预测市场订阅
        ├── push_event_contract_orderbook.py   # 接收预测市场摆盘推送
        ├── push_event_contract_kline.py       # 接收预测市场 K 线推送
        └── push_event_contract_ticker.py      # 接收预测市场逐笔推送
```

---

**相关技能路由：** 相关：脚本路径查找规则见 SKILL.md「脚本路径查找规则」。
