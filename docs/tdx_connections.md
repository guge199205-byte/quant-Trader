# 通达信连接通道登记（2026-09-15）

本系统访问通达信的所有通道一览。**token/凭据只登记位置，值一律不入库**。

## 通道对比

| 通道 | 位置 | 认证 | 用途 | 状态 |
|---|---|---|---|---|
| **① 8550 桥（主）** | Windows 交易机 `192.168.31.13:8550`，自建 HTTP 桥包通达信客户端 | `TDX_BRIDGE_TOKEN`（`.env`）+ token 在 Windows 侧 `config.yaml` | 账户查询 / **下单** / 当日委托 / K 线 / 撤单 | 生产主链路；已知 flapping（2026-09-14/15 多次断线假活） |
| **② TdxAiData（数据备胎）** | Linux 本机 `/opt/tdx-aidata`（`libTdxAiData.so` + `tqServer.py` + `TdxAiData.ini`） | token 在 `TdxAiData.ini`（会员中心获取） | **只做数据**：K线/分时/分笔/订阅（实时行情源） | 运行中（2026-09-15 自检通过）；已知 Token Insufficient 突发限流（退避重试） |
| **③ TQ 直连（待验证）** | Windows 交易机，通达信客户端内建 `tqcenter` | 随客户端登录态（无独立 token） | 账户/持仓/委托查询 + 理论可交易（官方 TQ 量化） | **待验证**：材料在 NAS `03福瑞布通达信量化/tq-验证包/`，验证通过后可做 ① 的互备 |

## ① 8550 桥

- 客户端：`agent_tools/brokers/tdx_bridge.py`（`TdxBridgeBroker`）——所有 A 股下单/账户的汇聚点
- 健康检查：`GET /api/v1/health`（免鉴权）+ `POST /api/v1/account/query`（**行情通≠账户通**：health 通但账户查询超时=假活，判据同时看 `asset>0`）
- 哨兵：`scripts/premarket_win_probe.py`（盘前 09:18）+ `scripts/bridge_watch.py`（盘中每分钟，断线/恢复 QQ 推送）
- Windows 侧：`brokers/tdx-bridge/`（main.py/config.yaml/watchdog.ps1/deploy docs）
- 已知坑：断线重连需重登通达信交易端；断线时会拒单（延期单由 `replay_deferred.py` 恢复后重放）

## ② TdxAiData（Linux 数据备胎）

- 客户端：`agent_tools/datasources/tdx_aidata.py`（`TDX_AIDATA_DIR` 环境变量指向安装目录，默认 `/opt/tdx-aidata`）
- 现有方法：`get_klines / get_klines_batch / get_quote / get_minute_data / get_tick_data / subscribe / unsubscribe`
- 官方「薄封装」参考：NAS `260912tdxaidatademo/tdx_ai_api.py`（思路一致：不拷贝 DLL、运行时定位、`PROJECT_CWD` 防 `os.chdir` 副作用）；**未移植**的官方能力（按需再补）：除权除息、交易日历、证券/板块列表、财务
- 定位：桥行情故障时的 K 线备胎（如 2026-09-14 "K线返回结果不是对象"期间的降级源）
- 已知坑：`count` 参数模式不可用（用 start/end 区间）；突发 `Token Insufficient`（码 13）指数退避 10/20/40/60s；只读数据、不交易

## ③ TQ 直连（待验证，长期替代方向）

- 目标：桥断线时自动切到 TQ 查账户/持仓（甚至下单），消除「单点桥 flapping」风险
- 验证包：NAS `03福瑞布通达信量化/tq-验证包/`（README=步骤+对账清单，内含 `login_and_query.py` 与官方接口文档）
- 验证通过后：在 `tdx_bridge.py` 加第二 provider 或独立 broker 实现，账户查询双通道互备

## 统一行情入口（2026-09-15 加）

`scripts/live_quotes.py`——行情取数唯一收口，**混合策略**（用户 2026-09-15 定）：

| 策略 | 路径 | 说明 |
|---|---|---|
| `prefer="aidata"` | 决策/批量（整点轮、调仓轮、哨兵价格判断外、杠杆守护、延期重放、复盘） | AiData 优先（Linux 通道，**不依赖 Win**）→ 桥兜底 |
| `prefer="bridge"` | 高频实时（分钟哨兵） | 桥优先（最实时）→ 失败自动落 AiData——**桥断行情判断照常** |

- 输出统一 `[{date: "YYYY-MM-DD", open, high, low, close, volume, amount}]`（日期归一 `20260914`→`2026-09-14`，数值转 float）
- 对拍验证（2026-09-15）：AiData 与桥逐字段一致（600519/001312 的 close/volume/amount 全同）
- 已替换调用点：live_hourly_analysis(6)、live_llm_trade(5)、live_price_watch(1)、leverage_guard(3)、replay_deferred(2)、post_review(2)
- **未覆盖（备注）**：`agent_tools/**` 里的行情调用（risk.py 等）跑在 MCP 容器内——容器未挂载 AiData，需容器侧安装后才能接；本轮不动

## 变更记录

- 2026-09-15：建文档；安装 tdxclaw-skills（53 技能 → `~/.claude/skills` + `dsh/skills`，frontmatter name 规范化为目录 slug）；AiData 自检通过；TQ 验证包就绪；**live_quotes 统一行情入口上线（桥断不再停摆）**