# QMT 桥（迅投大 QMT）接入手册

> 2026-09-10 接入并打通。**阶段一：只读**（查账户/持仓/委托/成交；下单未接线）。
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
- **两边双闸**：zbox 侧 `qmt_bridge.py` 的 buy/sell/cancel 直接抛错（`READONLY_MSG`）；
  Windows 侧 `rpc_allow_order_methods=False`。任一为关都下不出去单。

## 共用 Redis 的两点注意（不是阻塞项，但要知道）

| 事项 | 现状 | 说明 |
|---|---|---|
| 淘汰策略 | `allkeys-lru`，512MB 上限 | 目前 used 104MB、`evicted_keys=0`（没淘汰过）。若日后内存吃紧可能把桥的 order_identity/rpc 键淘汰掉——接执行前建议盯一眼或拆独立实例 |
| 无密码 + 监听 0.0.0.0 | 既有状态（quantmind 也这么连） | 局域网内谁都能连。当前 `rpc_allow_order_methods=False`，最多能伪造只读查询；接执行前必须处理（加密码要三处同步改：quantmind、Windows 桥配置、BayMax 配置） |

## zbox 侧（已完成）

| 组件 | 位置 / 命令 | 说明 |
|---|---|---|
| 客户端配置 | `config/qmt_bridge.json`（gitignored） | `account_id=40327478`；redis 指 6379/db5 |
| 客户端库 | `xtquant-big-convert[redis]==0.3.31`（/home/zbox/baymax/.venv） | Linux 侧不需要真机 xtquant |
| Broker 适配 | `agent_tools/brokers/qmt_bridge.py`（注册名 `qmt`） | 返回形状对齐 TdxBridgeBroker（available_volume=T+1 闸门依据） |
| 只读体检 | `/home/zbox/baymax/.venv/bin/python scripts/qmt_probe.py` | ping→资产→持仓→委托→成交，失败给排查清单 |
| 单元测试 | `tests/test_qmt_bridge.py` + `tests/test_broker_config.py` | 状态码映射/形状/只读闸门/配置读写 |
| 总控卡片 | 总控 → 交易所设置 → **迅投 QMT** | 字段直编 `config/qmt_bridge.json`（与体检脚本同源，不是 brokers.json）；「测试连接」返回总资产/可用/持仓数 |
| 总控面板 | 总控首页 → 「⚡ 迅投 QMT（只读）」 | 总资产/可用/市值 + 持仓明细（按市值降序，卡内滚动）；桥不可达时降级为提示 |
| API 路由 | `GET /api/qmt/account`、`GET /api/qmt/orders` | 只读；account 可作账户通道第二观测点（与 TDX 桥互为独立佐证） |

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
应显示「QMT 桥已连接（总资产 ¥…，可用 ¥…，持仓 N 只；阶段一只读）」。
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

- **现在（阶段一）**：只读接入，验收通过（2026-09-10 首验）。
- **可顺带做**：把 QMT 账户查询加进盘前健检/日报，作为账户通道的**第二观测点**
  （与 TDX 桥互为独立佐证——一死一活时立刻能分辨是桥的问题还是券商侧的问题）。
- **接执行**（未排期，需同时满足）：
  1. 先处理上面「共用 Redis 注意」两条（淘汰策略 + 无密码）；
  2. Windows 侧 `rpc_allow_order_methods: True`；
  3. zbox 侧实现 `qmt_bridge.py` 的 buy/sell/cancel_order（当前抛 `READONLY_MSG`），
     并过一遍与 TDX 桥同款的下单闸门链（预算→熔断→标的边界）。
