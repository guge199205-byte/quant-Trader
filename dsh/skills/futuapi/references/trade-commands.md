<!-- TOC: 交易命令（Trading） -->
# 交易命令（Trading）

账户、持仓、下单/撤单/改单、组合下单、订单查询、融资融券、最大可买卖数量。

## 交易命令

## 目录

- 获取账户列表
- 新加坡 / 马来西亚 / 日本市场交易（SG / MY / JP）
- 获取持仓与资金
- 下单
  - 预测市场硬性约束
- 组合下单（期权组合/策略 / 预测市场组合）
  - 预测市场组合硬性流程与约束
- 查询组合可交易信息
  - 美股交易时段确认
  - 模拟交易下单流程
  - 实盘下单流程
- 改单
- 撤单
- 查询今日订单
- 查询历史订单
- 查询历史成交

---

### 获取账户列表
当用户问 "我的账户"、"账户列表" 时：
```bash
python skills/futuapi/scripts/trade/get_accounts.py [--json]
```
脚本会遍历各 `SecurityFirm`，分别通过 **证券**（`OpenSecTradeContext`）与 **期货**（`OpenFutureTradeContext`）拉账户，按 `acc_id` 去重合并。返回字段 `ctx_type` 为 `SEC` 或 `FUTURE`。

> **提示**：实盘账户的 `uni_card_num` 后四位等于 app/桌面端上显示的账号数字。展示实盘账户信息时应**优先显示 `uni_card_num`**（而非 `acc_id`），因为用户在 app/桌面端看到的就是这个编号，更容易关联识别。模拟账户无需关注此字段。

> **账号拉取问题**：`create_trade_context()` 默认使用 `filter_trdmarket=TrdMarket.NONE`（不过滤市场），但如果手动创建 `OpenSecTradeContext` 时传了具体市场（如 `TrdMarket.US`、`TrdMarket.HK`），可能导致部分账号被过滤。将 `filter_trdmarket` 改为 `TrdMarket.NONE` 重新拉取即可。

JSON 输出包含 `trdmarket_auth` 字段，表示该账户拥有交易权限的市场列表（如 `["HK", "US", "HKCC", "SG", "MY", "JP"]`，期货/预测市场还可能含 `FUTURES`、`PREDICTION` 等）；`acc_role` 字段表示账户角色（如 `MASTER` 为主账户）。下单时应选择 `trdmarket_auth` 包含目标市场且 `acc_role` 不是 `MASTER` 的账户；**预测市场**选 `ctx_type=FUTURE` 且 `trdmarket_auth` 含 `PREDICTION` 的实盘账户。

### 新加坡 / 马来西亚 / 日本市场交易（SG / MY / JP）

| 市场 | 代码前缀 | 对应券商 | 示例代码 |
|------|----------|----------|----------|
| 新加坡 | `SG.` | `FUTUSG` | `SG.D05`（星展集团） |
| 马来西亚 | `MY.` | `FUTUMY` | `MY.1155`（马来亚银行） |
| 日本 | `JP.` | `FUTUJP` | `JP.7203`（丰田汽车） |

使用要点：
- 交易脚本会从 `--code` 前缀自动推断 `SG` / `MY` / `JP` 市场，通常无需手动传 `--market`
- 下单前用 `get_accounts.py --json` 确认账户 `trdmarket_auth` 包含目标市场，并匹配正确的 `--security-firm`
- 涉及 `--market` 参数的交易脚本现已支持 `SG` / `MY` / `JP`（如 `get_portfolio.py`、`get_orders.py`、`get_max_trd_qtys.py` 等）
- 日本账户使用 `FUTUJP` 券商标识；若账户存在多个 JP 子账户，下单前请结合 `get_accounts.py` 返回的 `jp_acc_type` 选择正确账户

### 获取持仓与资金
当用户问 "持仓"、"资金"、"我的股票" 时：
```bash
python skills/futuapi/scripts/trade/get_portfolio.py [--market HK] [--trd-env SIMULATE] [--acc-id 12345] [--ctx-type SEC|FUTURE] [--security-firm FUTUSECURITIES] [--json]
```
- `--market`: US, HK, HKCC, CN, SG, MY, JP
- `--trd-env`: REAL, SIMULATE（默认 SIMULATE）
- `--ctx-type`: `SEC`（证券，默认）或 `FUTURE`（期货/预测市场账户，与 `get_accounts` 的 `ctx_type` 一致）
- `--show-option-strategy-view`: 按期权策略视角查询持仓（透传 `position_list_query(show_option_strategy_view=True)`）
- `position_list_query` 返回新增字段：`combo_id`、`strategy_type`、`position_type`、`acc_id`、`jp_acc_type`

> 查询预测市场/期货账户持仓、订单、成交、撤改单、现金流水等时，需传 `--ctx-type FUTURE`（与 `get_accounts` 返回的 `ctx_type` 对齐）。带 `EC.` 代码的脚本（如 `get_max_trd_qtys`、`get_history_orders --code`）会自动切期货上下文。

> 持仓与资金的完整字段映射（与 APP 对齐）参见 `docs/FIELD_MAPPING.md`。**关键规则**：持仓盈亏用 `unrealized_pl` / `pl_ratio_avg_cost`（均价口径），禁止用 `cost_price` / `pl_val`（摊薄口径）。多币种汇总必须用 `accinfo_query(currency=目标币种)` 获取账户级数据。

### 下单
当用户问 "买入"、"卖出"、"下单"、"预测市场下单"、"预测合约" 时：
```bash
# 股票 / 期权等
python skills/futuapi/scripts/trade/place_order.py --code US.AAPL --side BUY --quantity 10 --price 150.0 [--order-type NORMAL] [--trd-env SIMULATE] [--time-in-force DAY] [--expire-time 2026-12-31] [--confirmed] [--security-firm FUTUSECURITIES] [--json]

# 预测市场（代码 EC.xxx，必须用实盘 + pred_side；amount 与 quantity 二选一，amount 优先）
python skills/futuapi/scripts/trade/place_order.py --code EC.xxx --side BUY --amount 100 --price 0.55 --pred-side YES --trd-env REAL --acc-id {acc_id} --confirmed [--security-firm FUTUSECURITIES] [--json]
```
- `--code`: 标的代码（必填）。股票等带市场前缀；**预测市场为 `EC.xxx`（无 US./HK. 前缀）**，脚本自动走 `OpenFutureTradeContext`
- `--side`: BUY/SELL（必填）
- `--quantity`: 数量；与 `--amount` 二选一。同时传时 **amount 优先**，qty 置 0
- `--amount`: 订单金额，**仅预测市场**；传 amount 时向 SDK 传 `qty=0`
- `--pred-side`: YES/NO，**预测市场必填**（无论用 quantity 还是 amount）
- `--price`: 价格（限价单必填，市价单不需要）；预测市场通常为 0.01~0.99
- `--order-type`: NORMAL(限价单) / MARKET(市价单)
- `--time-in-force`: 默认 DAY；传 `GTD` 时必须加 `--expire-time yyyy-MM-dd`
- `--expire-time`: 仅 `time_in_force=GTD` 时有效
- `--session`: 美股交易时段，可选 NONE/RTH/ETH/OVERNIGHT/ALL（仅对美股生效）
- `--confirmed`: 实盘下单必须传入此参数（代码硬约束，不传则返回订单摘要后退出）
- **下单前务必与用户确认代码、方向、数量/金额、价格（及预测市场 pred_side）**

#### 预测市场硬性约束
- 仅 `--trd-env REAL`；传 `SIMULATE` 会直接报错退出（模拟不支持预测市场）
- 必须使用期货账户上下文；账户 `trdmarket_auth` 需包含 **`PREDICTION`**，否则提示不支持交易预测市场
- 用 `get_accounts.py --json` 选户：`ctx_type` 为 `FUTURE`、`trd_env` 为 `REAL`、`acc_role` 非 `MASTER`、且 `trdmarket_auth` 含 `PREDICTION`
- 普通期货仍按「期货交易命令」直接生成 `OpenFutureTradeContext` 代码；**预测市场可通过本脚本下单**

### 组合下单（期权组合/策略 / 预测市场组合）
当用户问 "组合下单"、"期权组合下单"、"策略单下单"、"预测市场组合下单" 时：

**组合期权：**
```bash
python skills/futuapi/scripts/trade/place_combo_order.py \
  '[{"code":"US.AAPL260529C302500","trd_side":"BUY","qty_ratio":1},{"code":"US.AAPL","trd_side":"SELL","qty_ratio":100}]' \
  --price 9.9 --quantity 1 [--order-type NORMAL] [--trd-env SIMULATE] [--confirmed] [--security-firm FUTUSECURITIES] [--json]
```
- 组合腿 JSON 字段：`code`、`trd_side`（BUY/SELL）、`qty_ratio`、`position_id`（可选，仅日本券商平仓场景）
- **`--price` 定价**：优先取自同组合 `get_option_strategy_analysis.py` 返回的 `bid1`/`ask1`（买入参考 ask1，卖出参考 bid1）；**禁止**对各腿 `get_snapshot.py` 后手动推算组合价
- `--quote-id`：组合期权**忽略**（即使传入也静默不传给 SDK）
- `--price` 与 `--quantity` 必填；每条腿实际数量 = `quantity * qty_ratio`
- `--time-in-force` 默认 `DAY`；当传 `GTD` 时可加 `--expire-time yyyy-MM-dd`
- `--confirmed`：实盘组合下单必须传入（不传仅预览）

**预测市场组合（全部腿必须为 `EC.`）：**
```bash
# 1) Agent 直接生成 Python：get_valid_combo_list → 取 mvc
# 2) Agent 直接生成 Python：request_combo_quotes(combo_leg_list, mvc) → quote_id + bid/ask
# 3) 再调用本脚本下单（价与 quote_id 均来自步骤 2）
python skills/futuapi/scripts/trade/place_combo_order.py \
  '[{"code":"EC.xxx","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"},{"code":"EC.yyy","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"}]' \
  --price {ask_or_bid} --quantity 1 --quote-id {quote_id} --trd-env REAL --acc-id {acc_id} --confirmed [--security-firm FUTUINC]
```

#### 预测市场组合硬性流程与约束
1. **腿合法性**：全部腿 `code` 必须以 `EC.` 开头；若 EC. 与非 EC. 混用 → 脚本报「组合不合法」
2. **方向一致**：全部腿 `trd_side` 必须相同；不一致则报错。用**第一条腿**方向定价：`BUY` → `ask_price`，`SELL` → `bid_price`
3. **每条腿必填** `pred_side`：`YES` / `NO`
4. **询价链（暂无独立 skill，由 Agent 生成 Python 执行）**：
   - `OpenQuoteContext.get_valid_combo_list()` → 取得 **`mvc`（必填）** 及可选 combo 列表
   - `OpenQuoteContext.request_combo_quotes(combo_leg_list, mvc)` → **`quote_id`**、`bid_price`、`ask_price`；若返回 `should_retry=True` 可短间隔重试
   - **`--price` 必须**取自该次询价的 ask/bid（禁止用别处价格）；**`--quote-id` 必填**
5. **交易上下文**：脚本自动用 `OpenFutureTradeContext`；仅 `--trd-env REAL`；账户 `trdmarket_auth` 须含 **`PREDICTION`**；模拟直接报错
6. 实盘执行前与用户确认腿、方向、pred_side、数量、价格与 quote_id

询价参考代码（Agent 可直接改写执行）：
```python
from futu import *
import time
qot_ctx = OpenQuoteContext(security_firm=SecurityFirm.FUTUINC)
ret, combo_df, mvc, _ = qot_ctx.get_valid_combo_list()  # mvc 必填下游
# 构造 ComboLeg：code/trd_side/qty_ratio/pred_side，且全部腿 trd_side 一致
ret, quote = qot_ctx.request_combo_quotes(combo_leg_list, mvc)
# quote['quote_id'], quote['ask_price'], quote['bid_price']；注意 should_retry
qot_ctx.close()
```
- **实盘执行前务必与用户确认组合腿、方向、数量与价格**

### 查询组合可交易信息
当用户问 "组合保证金变化"、"组合购买力变化"、"组合可交易信息" 时：
```bash
python skills/futuapi/scripts/trade/comboorder_tradinginfo_query.py \
  '[{"code":"US.AAPL260529C302500","trd_side":"BUY","qty_ratio":1},{"code":"US.AAPL","trd_side":"SELL","qty_ratio":100}]' \
  --price 100 --quantity 1 [--order-type NORMAL] [--order-id 123456789] [--trd-env SIMULATE] [--security-firm FUTUSECURITIES] [--json]
```
- 返回关键字段：`nlv_change`、`initial_margin_change`、`maintenance_margin_change`、`option_bp`、`max_withdraw_change`、`bp_decrease`
- `--price` 应优先取自 `get_option_strategy_analysis.py` 的 `bid1`/`ask1`，勿用单腿快照自行计算
- `--order-id` 仅改单场景需要，不传则查询新下单场景

#### 美股交易时段确认

当用户下单代码为**美股**（`US.` 开头）且未明确指定交易时段时，**必须用 AskUserQuestion 让用户选择交易时段**后再下单：

```
问题: "请选择美股交易时段："
  header: "交易时段"
  选项:
    - "仅盘中" : 仅在常规交易时段成交（美东 9:30-16:00）
    - "允许盘前盘后" : 允许在盘前（4:00-9:30）和盘后（16:00-20:00）时段成交，注意：盘前盘后不支持市价单
```

- 用户选择"仅盘中"：正常下单，不加 `--fill-outside-rth`
- 用户选择"允许盘前盘后"：下单命令加上 `--fill-outside-rth` 参数
- 如果用户在对话中已明确提到"盘前"、"盘后"、"盘前盘后"、"extended hours"、"pre-market"、"after-hours" 等关键词，直接加 `--fill-outside-rth`，无需再次确认
- 如果用户明确说"盘中"、"regular hours"，则不加 `--fill-outside-rth`，无需再次确认
- **注意**：盘前盘后时段不支持市价单（`--order-type MARKET`），如果用户选择盘前盘后且使用市价单，需提示改用限价单

#### 模拟交易下单流程

模拟交易（`--trd-env SIMULATE`，默认）直接执行下单命令即可：
```bash
python skills/futuapi/scripts/trade/place_order.py --code {code} --side {side} --quantity {qty} --price {price} --trd-env SIMULATE
```

#### 实盘下单流程

当用户要求实盘（`--trd-env REAL`）下单时，**必须执行以下流程**：

0. **确认券商标识（首次）**：
   如果尚未确定用户的 `security_firm`，先检查环境变量 `FUTU_SECURITY_FIRM` 是否已设置。若未设置，运行 `get_accounts.py --json` 查看返回的实盘账户的 `security_firm` 字段来确定。后续交易命令均带上 `--security-firm {firm}` 参数。详见「券商自动探测」章节。

1. **查询账户列表并选择有权限的账户**：
   先运行 `get_accounts.py --json` 获取所有账户，根据股票代码确定目标交易市场（如 HK.00700 → HK），筛选出 `trd_env` 为 `REAL` 且 `trdmarket_auth` 包含该市场 **且 `acc_role` 不是 `MASTER`** 的账户。主账户（MASTER）不允许下单，必须排除。
   - 如果只有 1 个符合条件的账户，直接使用
   - 如果有多个符合条件的账户，用 AskUserQuestion 让用户选择：
     ```
     问题: "请选择交易账户："
       header: "账户选择"
       选项:（列出所有符合条件的账户）
         - "账户 {acc_id} ({card_num})" : 角色: {acc_role}, 交易市场权限: {trdmarket_auth}
     ```
   - 如果没有符合条件的账户，提示用户当前无支持该市场的实盘账户（注意：MASTER 角色的账户不能用于下单）

2. **用 AskUserQuestion 进行二次确认**，明确展示订单详情：
   ```
   问题: "确认实盘下单？这将使用真实资金。"
     header: "实盘确认"
     选项:
       - "确认下单" : 账户: {acc_id}, 代码: {code}, 方向: {BUY/SELL}, 数量: {qty}, 价格: {price}
       - "取消" : 不执行下单
   ```
   用户选择"确认下单"后才能继续，选择"取消"则终止。

3. **执行下单命令**，带上 `--acc-id`：
   ```bash
   python skills/futuapi/scripts/trade/place_order.py --code {code} --side {side} --quantity {qty} --price {price} --trd-env REAL --acc-id {acc_id} --security-firm {firm}
   ```

   > **注意**：如果 API 返回 `unlock needed` 或类似解锁错误，提示用户需先在 **OpenD GUI 界面手动解锁交易密码**（菜单或界面中的"解锁交易"按钮），解锁后重新执行下单。

### 改单
当用户问 "改单"、"修改订单"、"修改价格"、"修改数量" 时：
```bash
python skills/futuapi/scripts/trade/modify_order.py --order-id 12345678 [--price 410] [--quantity 200] [--market HK] [--trd-env SIMULATE] [--acc-id 12345] [--security-firm FUTUSECURITIES] [--json]
```
- `--order-id`: 订单 ID（必填）
- `--price`: 修改后的价格（可选，不传则保持原价）
- `--quantity`: 修改后的总数量，非增量（可选，不传则保持原数量）
- 至少提供 `--price` 或 `--quantity` 之一
- 缺失参数会自动查询原订单补全（如只改价格，数量自动取原订单值）
- A 股通市场不支持改单
- 用户未给出订单 ID 时，先用 `get_orders.py` 查询

### 撤单
当用户问 "撤单"、"取消订单" 时：
```bash
python skills/futuapi/scripts/trade/cancel_order.py --order-id 12345678 [--acc-id 12345] [--market HK] [--trd-env SIMULATE] [--security-firm FUTUSECURITIES] [--json]
```
- 用户未给出订单 ID 时，先用 `get_orders.py` 查询

### 查询今日订单
当用户问 "订单"、"我的委托" 时：
```bash
python skills/futuapi/scripts/trade/get_orders.py [--market HK] [--trd-env SIMULATE] [--acc-id 12345] [--security-firm FUTUSECURITIES] [--json]
```

### 查询历史订单
当用户问 "历史订单"、"过去的委托" 时：
- **注意**：当用户要求查看"全部订单"/"所有订单"/"all orders"时，必须在查询**之前**主动提醒："该接口默认仅返回最近 90 天的订单，如需查看更早的历史订单，可以指定起止日期。"
```bash
python skills/futuapi/scripts/trade/get_history_orders.py [--acc-id 12345] [--market HK] [--trd-env SIMULATE] [--start 2026-01-01] [--end 2026-03-01] [--code US.AAPL] [--status FILLED_ALL CANCELLED_ALL] [--limit 200] [--security-firm FUTUSECURITIES] [--json]
```

### 查询历史成交
当用户问 "历史成交"、"成交记录"、"过去的成交" 时：
- **注意**：当用户要求查看"全部成交"/"所有成交"/"all deals"时，必须在查询**之前**主动提醒："该接口默认仅返回最近 90 天的成交记录，如需查看更早的历史成交，可以指定起止日期。"
```bash
python skills/futuapi/scripts/trade/get_history_order_fill_list.py [--acc-id 12345] [--market HK] [--trd-env SIMULATE] [--start 2026-01-01] [--end 2026-03-01] [--security-firm FUTUSECURITIES] [--json]
```

---

---

**相关技能路由：** 相关：组合期权摆盘价（--price 来源）→ options.md（硬约束）；持仓字段映射 → docs/FIELD_MAPPING.md；持仓诊断清单 → `references/analysis-frameworks.md`。
