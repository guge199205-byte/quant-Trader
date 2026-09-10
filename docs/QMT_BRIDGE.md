# QMT 桥（迅投大 QMT）接入手册

> 2026-09-10 接入并打通，2026-09-11 接线下单。**当前：查询 + 下单已接线，本侧总闸默认关闭**
> （`config/qmt_bridge.json` 的 `allow_trading` 不显式设 true 就是只读）。
> QMT 机器 = `192.168.31.13`（与通达信桥 8550 同机）；账户 = QMT 模拟交易（40327478）。
> 首验结果：账号/50 持仓/182 委托/成交流水全通，退出码 0。

## 链路

```
zbox 192.168.31.68                              Windows 192.168.31.13
┌───────────────────────────────┐              ┌───────────────────────────────┐
│ scripts/qmt_probe.py          │              │ 大 QMT（交易端已登录）         │
│ agent_tools/brokers/          │              │ 策略编辑器正在运行：           │
│   qmt_bridge.py               │◄────────────►│  BIGQMT_REDIS_DRYRUN.py       │
│   (xtquant-big-convert 0.3.31)│              │  (内置 Python 3.6 + xtquant)  │
│        │                      │              │                               │
│        ▼                      │              └───────────────┬───────────────┘
│ 容器 quantmind-redis :6379 ───┼──────────────  db 5 ◄────────┘
│ （与 quantmind 共用此实例）    │
└───────────────────────────────┘
```

- **Redis 就是本机 quantmind 那个实例**（容器 `quantmind-redis`，`192.168.31.68:6379`，
  **db 5**，无密码）——QMT 侧一直在连它，不另起实例（2026-09-10 定的：共用现成的，
  zbox 侧零改动接上）。首次接入时误建过独立实例（6380），已拆。
- **行情不在这条链上**：行情走通达信桥 8550 / quantdb。两条链路刻意分开——
  2026-09-10 实录过「行情全通、账户通道整日掉线」，合链会一起挂。
- 老路（外部 miniQMT / xtquant 直连）已按 2025-07 程序化交易细则退役，**不要复活**。
- **两边双闸（独立，都要开才成交）**：
  1. zbox 侧 `qmt_bridge.py` 的 `allow_trading`（`config/qmt_bridge.json`，**默认 false**）；
  2. Windows 侧 `rpc_allow_order_methods`（2026-09-11 实录为 **true**，即那边已放开）。
  本侧 `_order_gate()` 会先查自己再 ping 对端，任一关闭都在发单前抛错，不把注定被拒的单子发出去。
- **★ 对端开关只信 ping，别信共享目录里的文件**：`/mnt/tdx-shared/qmt-bridge-kit/`
  是**脱敏模板/分发副本**，2026-09-11 实录它的 `bigqmt_signal_trader_local_config.py:26`
  仍写 `False`，而运行中的桥自述是 `True`——真正生效的是 QMT python 目录下那份（包要拷过去才跑）。
  改开关要改**运行的那份**并重载策略；查状态一律用 `GET /api/qmt/status` 或 `qmt_probe.py`。

## 共用 Redis 的两点注意（开真钱前要处理）

| 事项 | 现状 | 说明 |
|---|---|---|
| 淘汰策略 | `allkeys-lru`，512MB 上限 | 目前 used 104MB、`evicted_keys=0`（没淘汰过）。若日后内存吃紧可能把桥的 order_identity/rpc 键淘汰掉——**下单链路已接线，这条要盯**或拆独立实例 |
| 无密码 + 监听 0.0.0.0 | 既有状态（quantmind 也这么连） | ★ **风险已升级**：Windows 侧 `rpc_allow_order_methods=true`（2026-09-11 实录），意味着局域网内任何能连到 `192.168.31.68:6379` db5 的人都能直接发 RPC 下真单——不再只是「能伪造只读查询」。建议尽快加密码（三处同步改：quantmind、Windows 桥配置、BayMax 配置），或在防火墙上只放行 192.168.31.13 与本机 |

## zbox 侧（已完成）

| 组件 | 位置 / 命令 | 说明 |
|---|---|---|
| 客户端配置 | `config/qmt_bridge.json`（gitignored） | `account_id=40327478`；redis 指 6379/db5 |
| 客户端库 | `xtquant-big-convert[redis]==0.3.31`（/home/zbox/baymax/.venv） | Linux 侧不需要真机 xtquant |
| Broker 适配 | `agent_tools/brokers/qmt_bridge.py`（注册名 `qmt`） | 返回形状对齐 TdxBridgeBroker（available_volume=T+1 闸门依据）；buy/sell/cancel_order 已接线，默认关闭 |
| 只读体检 | `/home/zbox/baymax/.venv/bin/python scripts/qmt_probe.py` | ping→资产→持仓→委托→成交，失败给排查清单 |
| 单元测试 | `tests/test_qmt_bridge.py` + `tests/test_broker_config.py` | 状态码映射/形状/双闸（默认关 + 对端关）/校验先于 RPC/配置读写 |
| 总控卡片 | 总控 → 交易所设置 → **迅投 QMT** | 字段直编 `config/qmt_bridge.json`（与体检脚本同源，不是 brokers.json）；「测试连接」返回总资产/可用/持仓数 + 本侧下单闸状态 |
| 总控面板 | 总控首页 → 「⚡ 迅投 QMT（只读）」 | 总资产/可用/市值 + 持仓明细（按市值降序，卡内滚动）；桥不可达时降级为提示 |
| API 路由 | `GET /api/qmt/account`、`GET /api/qmt/orders`、`GET /api/qmt/status` | status 实时读回两处总闸（本侧 `allow_trading` + Windows 侧 `allow_order_methods`），界面据此分两行显示 |

Redis 日常（注意它同时伺候 quantmind，重启会影响两边）：

```bash
docker exec quantmind-redis redis-cli -n 5 dbsize          # 桥的键都在 db5
docker exec quantmind-redis redis-cli client list | grep 192.168.31.13   # 桥的连接
docker inspect --format '{{.State.Health.Status}}' quantmind-redis
```

## Windows 侧（已完成，备忘）

开箱包在共享 `\\192.168.31.13\PYPlugins\qmt-bridge-kit\`（zbox 侧挂载点 `/mnt/tdx-shared`，
fstab 有 entry，Windows 关机后 `sudo mount /mnt/tdx-shared` 恢复）。

1. 全包拷到 QMT 的 python 目录 → 改 `bigqmt_signal_trader_local_config.py`
   （账号 / Redis 地址 / db）→ QMT **策略编辑器**加载运行 `BIGQMT_REDIS_DRYRUN.py`。
2. ★ 必须走策略编辑器：`python.exe` 直接跑不会注入交易接口，日志以 finished 结尾，
   看着像跑起来了其实什么都没监听。QMT 需处于实盘（交易端已登录）模式。
3. **QMT 重启后需要重新运行该策略。**

成功标志（QMT 输出面板）：

```
[bigqmt_shell] local rpc config loaded transport=redis keys=[...]
[bigqmt_shell] local account config loaded=True
[bigqmt_rpc] started channel=bigqmt:rpc:req:<账号>
[bigqmt_signal_trader] init ok
```

## 验证

```bash
/home/zbox/baymax/.venv/bin/python scripts/qmt_probe.py
```

期望：`✅ ping` + 账户三行（总资产/可用/市值）+ 持仓列表，exit 0。
未配置账号时 exit 2 并提示填哪里；ping 不通 exit 1 并给排查清单。

页面上：总控 → 交易所设置 → 「迅投 QMT」卡片 → **测试连接**，
应显示「QMT 桥已连接（总资产 ¥…，可用 ¥…，持仓 N 只；本侧下单闸关闭（只读））」；
本侧总闸开启时该行改为「本侧下单闸已开启（Windows 侧 已放开/未放开/读取失败）」。
（前端依赖 `requirements.lock.txt` 里的 `xtquant-big-convert`——改依赖后 api 镜像要重建，
只拷代码不够。）

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| `redis rpc timeout: ping` | QMT 侧策略没在运行（含 QMT 重启后忘重跑） | QMT 策略编辑器重新运行 BIGQMT_REDIS_DRYRUN.py |
| 同上但策略在跑 | Redis 地址/端口/db 不一致（Windows 配置 vs `config/qmt_bridge.json`） | 两处对齐（当前应为 192.168.31.68:6379/db5） |
| 资产全 0、持仓空 | account_type 与服务端不符（信用账户填了 STOCK） | Windows 配置改 `BIGQMT_ACCOUNT_TYPE = "CREDIT"` |
| 连不上 Redis | quantmind-redis 容器没起 | `docker start quantmind-redis`（会短暂影响 quantmind） |
| ping 通但账户查询报错 | QMT 未登录交易 / 账号号码不对 | 看 QMT 交易界面是否已登录 |

QMT 侧日志：`<QMT python 目录>\logs\bigqmt_*.log`（保留 7 天）。

## 阶段边界与下一步

- **现在**：查询已验收（2026-09-10 首验）；下单已接线但**本侧默认关闭**（2026-09-11）。
- **已顺带做**：QMT 账户可作账户通道的**第二观测点**（与 TDX 桥互为独立佐证——
  一死一活时立刻能分辨是桥的问题还是券商侧的问题）；`GET /api/qmt/status` 实时回报两处总闸，
  详情卡分两行显示（「Windows 侧下单闸」/「本系统接线」）。
- **开真钱前的清单**（未做，按序）：
  0. **先人工定性账户 40327478 是模拟还是真实**：本手册开头写「QMT 模拟交易」，
     工具包 README 写「大 QMT 真实下单」，两处口径不一致——若是真实账户，第 3 步
     的小额验证就是真钱成交，性质完全不同。
  1. 处理上面「共用 Redis 注意」两条——**无密码这件已从「只读风险」升级为「可下真单风险」**；
  2. **补上 TDX 那条链有、QMT 这条链没有的三件防护**（2026-09-11 核对）：
     TDX 走 `plan_id`（本机生成）+ 桥侧幂等（当日同代码同方向已成交则跳过）
     + 卖前可用持仓校验（`bridge-windows/src/executor/plan_executor.py`）；
     QMT 这条目前只有「双闸 + 入参校验」，经 `risk.py` 时再加 RiskGateway 的仓位/预算检查。
     走真钱前要想清楚：重复提交谁拦、超卖谁兜（对端会拒超卖，但不保证不重复成交）。
  3. 小额验证：本侧临时开 `allow_trading`，用最小手数（100 股）跑一笔买入→查询委托→撤单/卖出，
     确认 `order_sys_id` 回填与 `get_orders()` 终态（53/54/56）都对得上；
  4. 验收通过、人工确认后，才把 `config/qmt_bridge.json` 的 `allow_trading` 置 true。
- 下单链路已过与 TDX 桥同款的闸门（预算→熔断→风控→标的边界，见 `agent_tools/risk.py`）。
  但**现有实盘脚本仍写死 `TdxBridgeBroker`**（如 `scripts/live_llm_trade.py`），且 QMT 通道
  不供行情（`get_quote`/`get_klines` 抛错）——真要切 QMT 执行，得做成「账户/执行走 QMT、
  行情走 TDX/quantdb」的混合通道，不是换个类名那么省事。
