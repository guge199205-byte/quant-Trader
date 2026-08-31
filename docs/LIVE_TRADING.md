# 实盘下单流程（通达信桥）

> 从历史市场分析报告选股 → 通达信桥（<tdx-bridge-ip>:8550，Windows 交易机）→ 真实下单。

## 链路

```
quantmind data/reports/stock_picks/{date}_picks.json   ← 结构化候选（side/score）
        ↓  scripts/select_from_reports.py（归一化 600519.SH）
候选股票列表
        ↓  scripts/live_trade_picks.py
桥 health → account/query（资金/持仓）
        ↓  行情：TdxAiData 实时源批量（前复权日K，失败逐票回退桥）
涨跌停/停牌过滤 → 100 股整数倍、单票 ≤ 20% 资金、限价=现价×1.01
        ↓
POST /api/v1/plans/execute → 通达信客户端确认/受理
        ↓
logs/live_trade_YYYYMMDD.jsonl（全量记录）+ orders/query 委托验证
```

## 行情源（2026-08-31 起全部通达信）

| 场景 | 来源 | 说明 |
|---|---|---|
| 实盘实时行情 | TdxAiData（agent_tools/datasources/tdx_aidata.py） | 官方接口，不依赖客户端，日K/5m/分笔/订阅，批量请求 |
| 实盘行情回退 | 8550 桥 tdx/call | TdxAiData 不可用/限流时逐票回退 |
| 本地数据刷新 | scripts/update_prices.py A股段 | TdxAiData 批量 → 桥回退（原腾讯 fqkline 已移除） |

TdxAiData 实测约束：
- 测试版接口有突发限流（服务端返回 "Token Insufficient"，与积分余额无关）：
  放行 2-3 次请求后冷却数分钟 → 模块内指数退避重试（10s/20s/40s/60s）
- count 参数模式不可用（必失败且带坏同进程连接）→ 一律 start/end 区间模式
- 一次调用支持多只股票（stock_list）→ 批量拉取省配额

## 用法

```bash
# 演练（不下单，全链路只读）
python scripts/live_trade_picks.py

# 实盘（只买 side≥BUY 的候选，前 5 只，单票 20% 资金）
python scripts/live_trade_picks.py --execute --top 5 --per-stock-pct 0.2

# 只选股看结果
python scripts/select_from_reports.py --json --min-side BUY
```

## 桥接口速查（Broker: agent_tools/brokers/tdx_bridge.py）

| 操作 | 桥端点 | 说明 |
|---|---|---|
| 下单 | `POST /api/v1/plans/execute` | orders[] 内 stock_code 必须 `600519.SH` 格式 |
| 账户 | `POST /api/v1/account/query` | asset{cash,balance,...} + positions[] |
| 委托 | `POST /api/v1/orders/query` | **仅当日**，无历史接口 |
| 撤单 | `POST /api/v1/orders/cancel` | 当日可撤委托 |
| 行情 | `POST /api/v1/tdx/call` get_market_data | 日K/周K 前复权；分钟线不支持 |
| 健康 | `GET /api/v1/health` | 免鉴权 |

认证：`Authorization: Bearer <TDX_BRIDGE_TOKEN>`（.env，64-hex）
桥状态码：0=REJECTED 1=SUBMITTED 2=PARTIAL_FILL 3=FILLED 4=PARTIAL_CANCELLED 5=CANCELLED

## 关键约束（实测）

- 桥机（Windows）必须在线：`<tdx-bridge-ip>:8550`，ping 不通先开机
- 通达信客户端下单默认**弹确认框**（Value=="1" → needs_confirm），"2"=submitted
- 股票代码必须后缀格式（`600519.SH`，`SH600519` 会被拒）
- 桥限流 60 req/min
- 实盘 T+1：当日买入 available_volume=0，无法当日卖出
- 费用：佣金万 2.5（最低 5 元双边）+ 印花税万 5（卖出）+ 过户费万 0.1（双边）

## 安全

- `live_trade_picks.py` 默认 **dry-run**，`--execute` 才真下单
- 只处理 side≥BUY（BUY/ADD）的候选；HOLD/SELL 跳过
- 每笔请求/回报全量落 `logs/live_trade_*.jsonl`
- 实盘前先跑 `--dry-run` 核对资金/价格/单量
