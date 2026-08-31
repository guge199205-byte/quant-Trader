# QuantMind 交易框架移植清单

2026-08-30 从 `/home/zbox/projects/quantmind` 移植交易框架与技能到本仓库（用户要求：
"通达信A股、富途证券港股美股、老虎证券港股美股、真实或模拟的交易框架 + skills 放到这个智能体"）。

## 一、Skills（29 个，已同步到 ~/.claude/skills/）

quantmind/skills/ 全部目录已同步（含更新覆盖 + 补齐缺失）：

| 类别 | 技能 |
|---|---|
| **交易执行** | tdx-live-trading（通达信实盘链路+监控）、simulation-trading（模拟盘 API）、futuapi（富途 OpenAPI）、tigeropen（老虎 SDK）、ibkr-cli（盈透） |
| **交易辅助** | install-futu-opend（富途 OpenD 安装）、tigeropen-cpp/csharp/go/java/rust/typescript（老虎多语言 SDK 参考） |
| **投研分析** | daily-review、market-analysis、stock-market-analysis、stock-picks、stock-research、trading-agents、smart-strategy-stock-picking |
| **数据/回测** | quantdb-sdk、quantdb-fields、backtest-center、ai-ide-strategy-writing、batch-inference-analysis、model-train-infer-backtest-report |
| **平台/情绪** | quantmind-operations、quantmind-deploy、news-sentiment-finbert、news-sentiment-research、rd-agent-factor-mining |

> 注：多数技能指向 **quantmind 后端 API（127.0.0.1:8000，容器当前在跑）**，在本机可直接使用；
> 离线环境下相关操作不可用（如模拟盘下单、通达信桥监控需 quantmind 容器 + Windows 桥）。

## 二、交易框架代码（brokers/，258 个文件）

| 目录 | 来源 | 说明 | 依赖 |
|---|---|---|---|
| `brokers/tdx-bridge/` | quantmind/bridge/windows | **通达信交易桥**完整源码（Windows 侧）：main.py + src/（tdx/security）+ watchdog 自愈 + setup 一键脚本；Linux 侧远程下单 http://Windows:8550（token 认证） | Python 3.10+，Windows 通达信客户端 TdxW.exe；token 与 Linux 侧 TDX_BRIDGE_TOKEN 一致 |
| `brokers/futu/` | quantmind/skills/futuapi | **富途 OpenAPI**：scripts/quote（行情）、scripts/trade（下单/撤单/账户/组合/加密币/期权）、scripts/subscribe（推送订阅）+ docs 5 篇 + references | futu-api SDK + 富途 OpenD 客户端（见 futu-opend-install） |
| `brokers/tiger/` | quantmind/skills/tigeropen | **老虎证券**：SKILL.md + references（quote/trade/push/option/mcp/cli/quickstart） | tigeropen SDK（pip）+ 老虎账号 API |
| `brokers/simulation/` | quantmind/skills/simulation-trading | **模拟盘 API 手册**：下单/持仓/账户/资金快照/组合/撤单（默认 100 万模拟资金） | quantmind 后端（127.0.0.1:8000） |
| `brokers/futu-opend-install/` | quantmind/skills/install-futu-opend | 富途 OpenD 客户端安装/配置手册 | — |
| `brokers/ibkr/` | quantmind/skills/ibkr-cli | 盈透 IB Gateway/TWS + ibkr-cli 操作指南 | IB Gateway + ibkr-cli |

## 三、与 Quant-Trader 自有交易体系的关系

| 体系 | 市场 | 模式 | 说明 |
|---|---|---|---|
| Quant-Trader 自有（tools/ + MCP） | 美股/A股/港股 | 模拟（历史回放） | LLM 智能体日线回放交易，成交价取自本地数据仓库 |
| quantmind 模拟盘（brokers/simulation） | A股 | 模拟（实时） | 100 万模拟资金，实时价成交，API 直连 |
| 通达信桥（brokers/tdx-bridge） | A股 | **真实/模拟** | Windows 通达信客户端内执行，Linux 远程下单 |
| 富途（brokers/futu） | 港股/美股 | 真实/模拟 | OpenD 网关 + futu-api SDK |
| 老虎（brokers/tiger） | 港股/美股 | 真实/模拟 | tigeropen SDK |
| 盈透（brokers/ibkr） | 全球 | 真实 | IB Gateway |

## 四、快速开始

```bash
# 模拟盘（quantmind 后端需在跑）
BASE=http://127.0.0.1:8000   # 见 brokers/simulation/SKILL.md 认证节
# 查账户 / 下单 / 查成交

# 富途（需 OpenD 已安装并登录）
cd brokers/futu/scripts && python3 trade/get_accounts.py

# 通达信桥（Windows 侧 setup.ps1 一键部署，Linux 侧）
curl -s -H "Authorization: Bearer $TDX_BRIDGE_TOKEN" http://<windows-ip>:8550/api/v1/account/query
```

## 五、注意事项

- 桥/客户端影响真实下单、持仓同步、L2 行情；日线行情/推理/信号/回测走本地数据，桥掉线不影响
- 通达信代码用**后缀格式**（600206.SH），前缀（SH600206）会 codestr error
- 股票代码标准化：quantmind 用前缀式（SH600036），Quant-Trader 用后缀式（600036.SH）——混用时注意转换
- 富途 OpenD 需在宿主机/Windows 安装（见 futu-opend-install），未安装时 scripts 会提示
