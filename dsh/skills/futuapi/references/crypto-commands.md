<!-- TOC: 加密货币命令（Crypto） -->
# 加密货币命令（Crypto）

BTC/ETH 等加密货币行情与交易。需 `futu-api >= 10.5.6508`（`OpenCryptoTradeContext`）。

## 加密货币（Crypto）

## 目录

- 支持范围
- 代码命名规范
- 加密货币行情
- 加密货币交易命令
  - 查询加密货币账户
  - 查询加密货币持仓与资金
  - 加密货币下单
  - 撤销加密货币订单
  - 查询加密货币订单 / 成交
  - 查询加密货币资金流水
  - 查询加密货币最大可买卖数量
  - 查询加密货币订单费用
- 加密货币下单响应规则

---

### 支持范围

| 项目 | 说明 |
|------|------|
| 券商 | FUTUSECURITIES（富途证券 香港）、FUTUINC（富途 美国）、FUTUSG（富途 新加坡） |
| 交易品种 | 现货币对（BTC/USD、ETH/USD 等） |
| 交易类型 | 仅现金买入（不支持融资融券、不支持模拟交易） |
| 订单类型 | FUTUHK/FUTUINC：限价单 + 市价单；FUTUSG：仅限价单 |
| 交易时间 | 7×24，无交易时段与有效期限概念；限价单发 GTC，市价单发 IOC |
| 改单 | 不支持；只支持撤单或全撤 |
| 数量 | 支持小数（如 `0.000136`） |

### 代码命名规范

| 场景 | 格式 | 示例 |
|------|------|------|
| 币种 / 指数 | `CC.{Base currency}` | `CC.BTC`、`CC.ETH`、`CC.SOL` |
| 币对（下单、行情订阅、成交查询） | `CC.{Base}{Quote}` | `CC.BTCUSD`、`CC.ETHUSD`、`CC.BTCHKD` |
| 持仓接口返回的 code | 仅 Base currency | `CC.BTC` |

> 不要在 code 里带 `/`（如 `CC.BTC/USD` 为错）。

### 加密货币行情

币种/指数行情（BTC、ETH 等）统一使用全球行情；币对行情根据创建行情连接时的 `security_firm` 取对应上游（HK→Hashkey、US→Coinbase、SG→DDEX）。

```bash
# 订阅加密货币行情（CC.BTCUSD / CC.BTC 均可）
python skills/futuapi/scripts/subscribe/subscribe.py CC.BTCUSD --types QUOTE ORDER_BOOK

# 加密货币 K 线（支持更多周期：1m/3m/5m/10m/15m/30m/60m/120m/180m/240m/1d/1w/1M/1Q/1Y）
python skills/futuapi/scripts/quote/get_kline.py CC.BTCUSD --ktype 1m --num 10

# 加密货币快照（可传币种或币对）
python skills/futuapi/scripts/quote/get_snapshot.py CC.BTC CC.BTCUSD

# 加密货币市场状态（MORNING = 交易中，覆盖 EST 00:00-24:00）
python skills/futuapi/scripts/quote/get_market_state.py CC.BTCUSD

# 资金流向 / 资金分布（code 可以是币种或币对）
python skills/futuapi/scripts/quote/get_capital_flow.py CC.BTC
python skills/futuapi/scripts/quote/get_capital_distribution.py CC.BTC
```

**摆盘说明**：可交易币对支持 1/5/10/20/40 档摆盘；指数不返回摆盘数据。加密货币行情推送频率与客户端一致，且没有经纪队列数据。

### 加密货币交易命令

加密货币交易有独立的脚本，均基于 `OpenCryptoTradeContext`。

#### 查询加密货币账户

```bash
python skills/futuapi/scripts/trade/get_crypto_accounts.py [--json]
```
- 自动遍历 FUTUSECURITIES / FUTUINC / FUTUSG 三个券商
- 返回 `acc_id`、`uni_card_num`、`security_firm`、`trdmarket_auth`（含 `CRYPTO`）

#### 查询加密货币持仓与资金

```bash
python skills/futuapi/scripts/trade/get_crypto_portfolio.py --acc-id 12345 --security-firm FUTUINC [--json]
```
- 资金字段新增：`crypto_mv`（加密货币市值）、`exposure_level`（持仓限额状态枚举）、`exposure_limit`、`used_limit`、`remaining_limit`
- 持仓 `code` 返回币种（如 `CC.BTC`），新增 `currency` 字段（默认 USD）
- `exposure_level` 枚举：`NORMAL` / `NEAR_LIMIT` / `RESTRICTED` / `SAFE` / `MODERATE` / `WARNING` / `MARGIN_CALL`

#### 加密货币下单

```bash
# 限价买入 0.000136 BTC，价格 72873.22 USD
python skills/futuapi/scripts/trade/place_crypto_order.py \
    --code CC.BTCUSD --side BUY --quantity 0.000136 --price 72873.22 \
    --order-type NORMAL --security-firm FUTUINC --acc-id 12345 --confirmed

# 市价买入（FUTUHK/FUTUINC 支持，FUTUSG 不支持）
python skills/futuapi/scripts/trade/place_crypto_order.py \
    --code CC.BTCUSD --side BUY --quantity 0.000136 \
    --order-type MARKET --security-firm FUTUINC --acc-id 12345 --confirmed
```

关键点：
- **仅实盘**：加密货币不支持模拟交易，脚本内部固定使用 `TrdEnv.REAL`
- **必须 `--confirmed`**：不带 `--confirmed` 只打印订单预览
- **数量支持小数**：与其他市场不同，加密货币数量可为浮点数
- **时效**：限价单自动 GTC，市价单自动 IOC，用户无需传 `--session` 或有效期
- **不支持的参数**：`session`、有效期限、`fill-outside-rth`
- 首次下单前应用 AskUserQuestion 明确展示代码/方向/数量/价格进行二次确认

#### 撤销加密货币订单

```bash
# 撤单
python skills/futuapi/scripts/trade/cancel_crypto_order.py \
    --order-id 12345678 --security-firm FUTUINC --acc-id 12345

# 全撤
python skills/futuapi/scripts/trade/cancel_crypto_order.py \
    --all --security-firm FUTUINC --acc-id 12345
```

**不支持改单**：需修改订单请撤单后重新下单。

#### 查询加密货币订单 / 成交

```bash
# 当日/未完成订单
python skills/futuapi/scripts/trade/get_crypto_orders.py \
    --security-firm FUTUINC --acc-id 12345

# 历史订单（支持 --code / --start / --end，默认近 90 天）
python skills/futuapi/scripts/trade/get_crypto_orders.py --history \
    --code CC.BTCUSD --start 2026-01-01 --end 2026-03-01 \
    --security-firm FUTUINC --acc-id 12345
```

> **注意**：`history_order_list_query` **不支持** `refresh_cache` 参数，脚本只对当日订单 (`order_list_query`) 传 `refresh_cache=True`，历史订单不传。手写代码集成时同样不要给 `history_order_list_query` 传 `refresh_cache`。

#### 查询加密货币资金流水

```bash
python skills/futuapi/scripts/trade/get_crypto_cash_flow.py \
    --start 2026-01-01 --end 2026-04-29 \
    --security-firm FUTUINC --acc-id 12345
```

- 加密货币账户必须传 `--start` 和 `--end`（按 create_time 联日查询），不接受 `clearing_date`
- 返回新增 `create_time`，`settlement_date` 固定为 `N/A`

#### 查询加密货币最大可买卖数量

```bash
python skills/futuapi/scripts/trade/get_crypto_max_trd_qtys.py \
    --code CC.BTCUSD --price 72873.22 \
    --security-firm FUTUINC --acc-id 12345 [--json]
```

- **仅现金账户**：加密货币不支持融资融券，返回字段只有 `max_cash_buy` 和 `max_position_sell`，**没有** `max_cash_and_margin_buy`
- 数量为浮点数（与币对小数精度一致）
- `code` 必须为币对（如 `CC.BTCUSD`），不接受币种 `CC.BTC`
- 仅实盘（`TrdEnv.REAL`）

#### 查询加密货币订单费用

```bash
python skills/futuapi/scripts/trade/get_crypto_order_fee.py 12345678 87654321 \
    --security-firm FUTUINC --acc-id 12345 [--json]
```

- 接口限制：每 30 秒内最多 10 次，每次最多查询 20 个 `order_id`
- 仅实盘（`TrdEnv.REAL`），基于 `OpenCryptoTradeContext`
- 券商仅支持 `FUTUSECURITIES` / `FUTUINC` / `FUTUSG`
- 一般用法：先用 `get_crypto_orders.py --history --json` 拿到 `order_id`，再传入本脚本查询费用明细

### 加密货币下单响应规则

1. **实盘确认**：下单前必须用 AskUserQuestion 让用户二次确认
2. **券商判定**：根据用户提到的地区或账号探测 `security_firm`：
   - 香港 / FUTUHK → `FUTUSECURITIES`
   - 美国 / moomoo US → `FUTUINC`
   - 新加坡 / moomoo SG → `FUTUSG`
3. **账户探测**：如果未知 `acc_id`，先运行 `get_crypto_accounts.py --json`
4. **禁止的操作**：不要尝试对加密货币订单调用 `modify_order` 的 `NORMAL`/`DISABLE`/`ENABLE`/`DELETE`，脚本只提供 `CANCEL`
5. **模拟交易请求**：用户要求加密货币模拟交易时，明确告知"加密货币不支持模拟交易，仅实盘" 并询问是否继续

---

---

**相关技能路由：** 相关：行情 → quote-commands.md；需 SDK>=10.5.6508；加密仅实盘不支持模拟。
