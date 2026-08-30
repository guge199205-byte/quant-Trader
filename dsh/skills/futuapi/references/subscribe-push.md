<!-- TOC: 订阅与推送（Subscribe / Push） -->
# 订阅与推送（Subscribe / Push）

订阅/退订/查询、报价/K线/盘口/逐笔/分时推送。预测市场订阅/推送见 `prediction-market.md`。

## 订阅管理命令

### 订阅行情
当用户需要订阅实时数据时：
```bash
python skills/futuapi/scripts/subscribe/subscribe.py HK.00700 --types QUOTE ORDER_BOOK [--json]
```
- `--types`: 订阅类型列表（必填）
- `--no-first-push`: 不立即推送缓存数据
- `--push`: 开启推送回调
- `--extended-time`: 美股盘前盘后数据
- `--session`: 美股交易时段，可选 NONE/RTH/ETH/ALL（仅用于美股 K 线/分时/逐笔，不支持 OVERNIGHT）

**可用订阅类型**：QUOTE, ORDER_BOOK, ORDER_BOOK_ODD, TICKER, RT_DATA, BROKER, K_1M, K_5M, K_15M, K_30M, K_60M, K_DAY, K_WEEK, K_MON

> `ORDER_BOOK_ODD` 为碎股盘订阅类型，仅支持 MY/SG 市场。

### 取消订阅
```bash
# 取消指定订阅
python skills/futuapi/scripts/subscribe/unsubscribe.py HK.00700 --types QUOTE ORDER_BOOK [--json]

# 取消所有订阅
python skills/futuapi/scripts/subscribe/unsubscribe.py --all [--json]
```
- **注意**：订阅后至少 1 分钟才能取消

### 查询订阅状态
当用户问 "已订阅什么"、"订阅状态" 时：
```bash
python skills/futuapi/scripts/subscribe/query_subscription.py [--current] [--json]
```
- `--current`: 只查询当前连接（默认查询所有连接）

---

## 推送接收命令

### 接收报价推送
当用户需要实时报价推送时：
```bash
python skills/futuapi/scripts/subscribe/push_quote.py HK.00700 US.AAPL --duration 60 [--json]
```
- `--duration`: 持续接收时间（秒，默认 60）
- 按 Ctrl+C 可提前停止

### 接收 K 线推送
当用户需要实时 K 线推送时：
```bash
python skills/futuapi/scripts/subscribe/push_kline.py HK.00700 --ktype K_1M --duration 300 [--json]
```
- `--ktype`: K_1M, K_5M, K_15M, K_30M, K_60M, K_DAY, K_WEEK, K_MON（默认: K_1M）
- `--duration`: 持续接收时间（秒，默认 300）
- `--session`: 美股交易时段，可选 NONE/RTH/ETH/ALL（仅美股，不支持 OVERNIGHT）

---

---

**相关技能路由：** 相关：预测市场订阅/推送 → prediction-market.md；订阅额度见 docs/API_LIMITS.md。
