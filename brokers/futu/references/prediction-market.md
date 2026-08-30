<!-- TOC: 预测市场命令（Prediction Market / Event Contract） -->
# 预测市场命令（Prediction Market / Event Contract）

## 预测市场命令（Prediction Market）

预测市场是针对未来事件（选举、经济数据、赛事等）的 YES/NO 二元预测合约，合约代码格式 `EC.xxx`（如 `EC.KXODIMATCH-26JUL140600INDENG-IND`）。完整调用链：

```
分类(get_event_contract_category) → 赛事筛选(filter_competition) → Series → Event → Contract → 快照/盘口/K线/逐笔
```

**硬约束（查询前需订阅）**：`get_event_contract_order_book` / `get_event_contract_kline` / `get_event_contract_ticker` 查询前**必须先订阅对应类型**（`SubType.ORDER_BOOK` / `K_DAY` 等 / `TICKER`），否则返回错误。这些脚本默认自动订阅（`--no-auto-subscribe` 跳过）。`request_history_event_contract_kline`（历史 K 线）与 `get_event_contract_snapshot` 均无需订阅。

**K 线类型限制**：预测市场 K 线仅支持 `K_1M`/`K_5M`/`K_60M`/`K_DAY`，其余报错。

**分页**：`event_list` / `get_event_contract` / `milestone_list` / `valid_combo_list` 默认只取第一页，需续拉时将上次返回的 `next_page` 作为 `--next-page` 传入。

**SDK 说明**：预测市场需要支持该功能的 SDK 版本。若当前 futu-api 不支持，脚本会打印明确的升级提示并退出（不会崩溃）。

## 目录

- 获取预测市场分类
- 赛事筛选
- 获取预测市场 Series 列表
- 获取预测市场 Event 列表
- 获取预测市场 Contract 列表
- 获取预测市场里程碑列表
- 获取可 Combo 事件列表
- Combo 询价
- 获取预测市场快照
- 获取预测市场摆盘
- 获取预测市场 K 线
- 获取预测市场逐笔
- 拉取预测市场历史 K 线
- 订阅预测市场
- 取消订阅预测市场
- 接收预测市场推送

---

### 获取预测市场分类
当用户问"预测市场分类"、"预测市场有哪些分类"、"预测合约分类"时：
```bash
python skills/futuapi/scripts/quote/get_event_contract_category.py [--category Sports] [--json]
```

### 赛事筛选
当用户问"赛事筛选"、"可用赛事"、"玩法全集"时：
```bash
python skills/futuapi/scripts/quote/filter_competition.py --category Sports [--tag Baseball] [--json]
```
- `competition` 列表中的赛事名称可作为 `get_event_contract_milestone_list` 的 `--competition` 入参

### 获取预测市场 Series 列表
当用户问"预测市场 Series"、"series 列表"时：
```bash
python skills/futuapi/scripts/quote/get_event_contract_series_list.py --category Sports [--tag Football] [--json]
```

### 获取预测市场 Event 列表
当用户问"预测市场 Event"、"event 列表"时：
```bash
python skills/futuapi/scripts/quote/get_event_contract_event_list.py EC.KXUFCVICROUND.SERIES [--count 20] [--status EVENT_ACTIVE] [--next-page KEY] [--json]
```

### 获取预测市场 Contract 列表
当用户问"预测市场 Contract"、"合约列表"、"EC 合约代码"时：
```bash
python skills/futuapi/scripts/quote/get_event_contract.py EC.KXUFCVICROUND-26JUL11SAIPIM.EVENT [--count 20] [--next-page KEY] [--json]
```
- 返回的 `contract_code`（`EC.xxx`）可作为快照/盘口/K线/逐笔等接口的 `code`

### 获取预测市场里程碑列表
当用户问"预测市场里程碑"、"赛事时间节点"时：
```bash
python skills/futuapi/scripts/quote/get_event_contract_milestone_list.py [--category Sports] [--competition "FIFA World Cup"] [--related-event EC.xxx] [--count 20] [--json]
```

### 获取可 Combo 事件列表
当用户问"可 Combo 事件"、"组合事件"、"可组合合约"时：
```bash
python skills/futuapi/scripts/quote/get_valid_combo_list.py [--category Sports] [--count 20] [--json]
```
- 返回的 `mvc` 必须透传给 `request_combo_quotes` 进行 Combo 询价

### Combo 询价
当用户问"Combo 询价"、"组合报价"、"预测市场组合价"时：
```bash
python skills/futuapi/scripts/quote/request_combo_quotes.py '[{"code":"EC.xxx-FRA","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"},{"code":"EC.xxx-ENG","trd_side":"BUY","qty_ratio":1,"pred_side":"YES"}]' --mvc KALSHI.KXMVECROSSCATEGORY-R [--json]
```
- 每条腿字段：`code`（必填）/ `trd_side`（BUY/SELL/SELL_SHORT/BUY_BACK，必填）/ `qty_ratio`（必填）/ `pred_side`（YES/NO，必填）
- 至少 2 条腿，可来自不同 event；`mvc` 从 `get_valid_combo_list` 透传
- `quote_id` 有时效性，下单需尽快用 `place_combo_order.py` 传 `quote_id`

### 获取预测市场快照
当用户问"预测市场快照"、"EC 行情"、"YES NO 报价"时（无需订阅）：
```bash
python skills/futuapi/scripts/quote/get_event_contract_snapshot.py EC.KXODIMATCH-26JUL140600INDENG-IND [--json]
```
- 快照只返回买卖一档，多档深度盘口用 `get_event_contract_order_book`

### 获取预测市场摆盘
当用户问"预测市场摆盘"、"EC 盘口"、"YES NO 盘口"时（需订阅 ORDER_BOOK，脚本自动订阅）：
```bash
python skills/futuapi/scripts/quote/get_event_contract_order_book.py EC.KXODIMATCH-26JUL140600INDENG-IND [--num 5] [--json]
```

### 获取预测市场 K 线
当用户问"预测市场 K 线"、"EC K 线"时（需订阅对应 K 线类型，脚本自动订阅）：
```bash
python skills/futuapi/scripts/quote/get_event_contract_kline.py EC.KXODIMATCH-26JUL140600INDENG-IND --ktype K_DAY --pre-side YES [--kline-source ORDER_BOOK_YES] [--max-count 10] [--json]
```

### 获取预测市场逐笔
当用户问"预测市场逐笔"、"EC 成交"时（需订阅 TICKER，脚本自动订阅）：
```bash
python skills/futuapi/scripts/quote/get_event_contract_ticker.py EC.KXODIMATCH-26JUL140600INDENG-IND [--count 30] [--json]
```

### 拉取预测市场历史 K 线
当用户问"预测市场历史 K 线"、"EC 历史 K 线"时（无需订阅，脚本直接拉取历史 K 线）：
```bash
python skills/futuapi/scripts/quote/request_history_event_contract_kline.py EC.KXNFLAFCCHAMP-27-CIN --start 2026-07-05 --end 2026-07-09 --pre-side YES --ktype K_DAY [--max-count 10] [--json]
```

### 订阅预测市场
当用户问"订阅预测市场"、"订阅 EC"时：
```bash
python skills/futuapi/scripts/subscribe/subscribe_event_contract.py EC.KXODIMATCH-26JUL140600INDENG-IND --types ORDER_BOOK TICKER K_DAY [--kline-source ORDER_BOOK_YES] [--json]
```
- 接收推送需先用对应推送脚本（`push_event_contract_*`）`set_handler`，或脚本中自行注册 `EventContract*HandlerBase`

### 取消订阅预测市场
```bash
python skills/futuapi/scripts/subscribe/unsubscribe_event_contract.py EC.xxx --types TICKER [--json]
python skills/futuapi/scripts/subscribe/unsubscribe_all_event_contract.py [--json]
```

### 接收预测市场推送
当用户问"预测市场推送"、"EC 实时推送"时：
```bash
python skills/futuapi/scripts/subscribe/push_event_contract_orderbook.py EC.xxx --duration 60 [--json]
python skills/futuapi/scripts/subscribe/push_event_contract_kline.py EC.xxx --ktype K_DAY [--duration 300] [--json]
python skills/futuapi/scripts/subscribe/push_event_contract_ticker.py EC.xxx --duration 60 [--json]
```

---

**相关技能路由：** 相关：期权/策略 → options.md；组合下单 → trade-commands.md；EC 推送见正文订阅小节。
