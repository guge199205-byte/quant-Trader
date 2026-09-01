# 美股实盘执行链规划（US Live Execution via IBKR）

## 目标
把 Live 美股页左侧的"回放竞技场 agent"升级为 **IBKR 实盘分账交易 agent**：
每个模型独立 $10,000 虚拟美元账户，分析 IBKR 实盘持仓 → 决策 → 闸门 → IBKR 下单 → 记账。
与 A股（桥）/ 港股（Tiger）同构，三市场执行链齐平。

## 架构

```
cron（美股时段宽松窗口）
  │
  ▼
live_hourly_analysis_us.py
  │  ① IBKR 账户（持仓/现金，get_positions/get_cash）
  │  ② 历史K线特征（get_klines 日K：动量/波动/量比）
  │  ③ 每 agent 分析（LLM 直连或 dsh agent，模型对话日志 agent_data_us/）
  │  ④ 解析决策（parse_intraday_decision 复用）
  ▼
execute_us_decisions —— 闸门（US 规则）→ IbkrBridgeBroker.buy/sell → us_ledger 记账
  │
  ▼
logs/live_trade_us_*.jsonl + data/us_ledger.json + 模型对话日志
```

## 组件清单

| 文件 | 内容 |
|---|---|
| `scripts/us_ledger.py` | per-agent 账本（初始 **$10,000**，仿 hk_ledger） |
| `scripts/live_hourly_analysis_us.py` | 主循环（账户→分析→决策→执行→记账） |
| `configs/us_config.json` | US enabled agent 列表 + 模型（复用 3 个模型） |
| `configs/us_exec.json` | **{"enabled": false}** 执行开关（默认关，仿 hk_exec） |
| 前端 | Live 美股实况卡/成交 tab 已就绪（IBKR），无需改 |

## 闸门（US 规则，与 A股/港股差异）

| 规则 | US |
|---|---|
| T+0 | ✅ 当天买当天可卖（无 T+1） |
| 涨跌停 | 无 |
| 最小交易单位 | **1 股整数倍**（不是 100 股！美股 1 股起） |
| 虚拟现金红线 | ✅ per-agent $10,000（买入扣/卖出加） |
| 单票比例 | ≤ 剩余额度 20% |
| 账户现金兜底 | ✅ IBKR 真实现金（$5.01 现状会拦下大额买入——安全） |
| PDT 提示 | 账户 <$25k 有日内交易限制（提示词注明，系统不硬拦） |
| 下单类型 | 限价单（现价 ±1%），天然防滑点 |

## 记账

- us_ledger.json：per-agent {quota: 10000, virtual_cash, positions: {code: {volume, cost_price}}}
- 成交回报：v1 下单即记账（限价单保护）；Phase 2 加订单状态轮询（get_orders 核对成交价）

## 调度（美股时段）

- 交易时段：9:30-16:00 **America/New_York**（脚本内用 zoneinfo 精确判断交易日+时段，cron 只给宽松窗口）
- cron（JST）：`30 22-23,0-3 * * 2-6`（JST 22:30-03:30 ≈ 北京 21:30-02:30，夏令时覆盖）—— **装 cron 需用户批准**
- 分析频率：开盘首轮 + 每小时

## 前端（Phase 2）

- Live 美股净值图：us_ledger 线（仿 cn 的 liveEquity，per-agent 虚拟净值曲线）
- 模型对话 tab：US agent 日志（agent_data_us/）

## 实施阶段

| Phase | 内容 | 状态 |
|---|---|---|
| **P1** | us_ledger + live_hourly_analysis_us.py + 闸门 + 记账 + us_exec.json 开关 + 分析日志 | 🔨 本次 |
| **P2** | 前端 agent 分账净值线 + 成交回报轮询 | 下次 |
| **P3** | cron 安装（美股时段，需批准）+ 行情订阅激活后实时价验证 | 待批准 |

## 风险与前置

1. **行情订阅未激活** → 实时价 get_quote 返回 None（降级：用日K最后一根做价）——不阻塞建链
2. **$5.01 实盘账户** → 虚拟 $10,000 远超真实现金，账户兜底会拒大单 → 真金测试等模拟盘（DUT073560，约 1 个工作日开通）或小额实测
3. PDT：$25k 以下账户 5 个交易日内限 3 次日内交易（提示词注明，系统不硬拦）
4. 下单前 get_quote 取价失败 → 跳过该笔（不盲下）
