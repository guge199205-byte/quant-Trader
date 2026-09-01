# 券商接入新手指南（Broker Onboarding）

> 四券商分步接入 + 踩坑记录（2026-09-01 实测整理）。**铁律：模拟盘先行，
> 全部链路在模拟盘验证通过前，绝不接实盘执行。**

## 总览

| 券商 | 市场 | 客户端 | 难度 | 模拟盘 |
|---|---|---|---|---|
| 通达信桥 | A股实盘 | Windows 通达信 + 桥服务 | 🔴 最难（需 Windows 机） | ❌ 无（用模拟资金分账） |
| 富途 OpenD | 港股/美股 | FutuOpenD 网关 | 🟡 中 | ✅ SIM 模拟 |
| 老虎证券 | 港股/美股 | 免网关（API 直连） | 🟡 中 | ✅ 模拟账户（推荐先开） |
| 盈透 IBKR | 全球 | IB Gateway | 🔴 难（2FA 每日） | ✅ Paper 模拟 |

**通用前提**：Docker 主栈已跑（`docker compose up -d`）、`.env` 已填（参照 `.env.example`）、
交易所设置页（总控 → 交易所设置）可访问。

---

## ① 老虎证券（推荐第一个接 —— 最快、有模拟盘）

1. **开通 Open API**：老虎官网 → 开放平台 → 创建应用（拿到 Tiger ID + RSA 私钥对）
   - 私钥保存好：页面刷新前必须复制（PKCS#1 格式给 Python）
2. **开模拟账户**：老虎 App → 我的 → 模拟交易 → 开通（立即生效，送虚拟资金）
3. **找账户号**（⚠️ 坑：账户号不显示在设置页，要调 API）：
   ```bash
   docker exec baymax-api python -c "
   from tigeropen.tiger_open_config import TigerOpenClientConfig
   from tigeropen.trade.trade_client import TradeClient
   c = TigerOpenClientConfig(); c.tiger_id='你的ID'; c.private_key='''你的私钥'''
   c.is_paper = True
   print(TradeClient(c).get_managed_accounts())"
   # 输出里 account_type=PAPER 的就是模拟账户号（如 21209554641705138）
   # account_type=STANDARD 的是实盘账户号
   ```
4. **填配置**：交易所设置 → 老虎证券：`tiger_id` / `rsa_private_key` / `account`（模拟账户号）/ `real_account`（实盘账户号）→ 保存 → **测试连接**（应显示"模拟盘，可用现金 $xxx"）
5. **验证**：`/api/tiger/account?env=SIMULATE`（模拟）/ `?env=REAL`（实盘）；`/api/tiger/transactions` 历史成交

**坑（实测）**：
- tigeropen **3.7.1**：配置类用 `tiger_open_config.TigerOpenClientConfig` 属性赋值
  （`config.tiger_id = ...`），不是旧版 `ClientConfig(tiger_id=...)`
- `tigeropen.common.util.sign_util` 已改名 `signature_utils`（不需要 import）
- 资产在 `PortfolioAccount.summary.cash`（不是 to_dict）
- 模拟/实盘按**账户号格式**自动识别（`AccountUtil.is_paper_account`）
- 代码格式：港股 `00700`（纯代码），不是 `HK.00700`

---

## ② 盈透证券 IBKR（有模拟盘，但 2FA 每日登录）

### 安装（二选一）
- **官方 GUI（推荐有显示器的机器）**：
  ```bash
  cd /tmp && curl -L -o ibgw.sh "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
  sh ibgw.sh -q -dir ~/Jts
  cd ~/Jts && DISPLAY=:1 ./ibgateway &   # :1 换成你的显示器号
  ```
- **Docker 版**（无显示器）：`ghcr.io/gnzsnz/ib-gateway`（内置 IBC 自动填凭据 + VNC）

### 登录与 2FA
- 用户名/密码就是 IBKR 账号；**登录对话框底部有 Paper 勾选**（模拟盘，端口 4002；
  实盘 4001）
- 2FA：IBKey 动态码 → 在登录窗口输入（**每天重启都要输一次**，IBKR 强制每日重启）
- ⚠️ 模拟盘账户需先在 IBKR 客户门户申请：设置 → 交易配置 → Paper Trading
  （约 1 个工作日开通，模拟用户名通常带后缀如 `xqpfn-1495`）

### API 开启（每次登录后确认）
1. Configure → Settings → API → **Enable ActiveX and Socket Clients**
2. **Trusted IP addresses**：`127.0.0.1` + **容器 IP**（`docker inspect baymax-api` 的 IP，
   如 172.22.0.3）+ 网桥网关（172.22.0.1）—— 漏了连接会被重置
3. 市场数据：客户门户 → 设置 → 市场数据订阅 → **US 延迟数据（非专业免费）** +
   签 API 确认表；否则实时价报 Error 10089/354

### 验证
- 交易所设置 → 盈透证券：`gateway_host=127.0.0.1`（宿主）或局域网 IP（容器）、
  `gateway_port=4001`（实盘）/`4002`（模拟）、`client_id=1` → 测试连接
- 宿主脚本强制 127.0.0.1（容器用局域网 IP）

**坑（实测）**：
- 只读/API 状态切换后连接会**僵死**（端口通但不响应）→ 重启 Gateway
- 日K日期格式：IBKR 要 `yyyymmdd hh:mm:ss`（`2026-09-01` 会被拒，Error 10314）
- 港股代码：`0700.HK` → `700`（去前导零）+ 交易所 **SEHK**（SMART 找不到，Error 200）
- 无行情订阅时 get_quote 返回 None（优雅降级，不炸）
- Gateway 界面中文乱码（缺字体）→ 登录框右上角语言切换选 English

---

## ③ 富途 OpenD（模拟盘，HK/US）

1. **安装 FutuOpenD**（macOS/Windows/Linux 均支持）：富途官网下载 → 启动 → **扫码登录**
2. **交易所设置 → 富途证券**：`opend_host`（默认 127.0.0.1）/ `opend_port`（默认 11111）/
   `trade_pwd_md5`（交易密码 MD5，只写不回显）/ `trade_env`（SIMULATE/REAL）
3. **验证**：测试连接（应显示"FutuOpenD 已连接（模拟 HK$xxx / 实盘 $xxx）"）；
   `/api/futu/account-both` 双账户；`/api/futu/place` 下单（env=SIMULATE 安全）
4. 快照走行情连接（OpenQuoteContext），交易走 OpenSecTradeContext
   （本 SDK 版本 OpenSecTradeContext **不暴露** get_market_snapshot）

**坑（实测）**：多行持仓合并、'N/A' 字符串、qty 参数名是 quantity；美股需
`market=US`（TrdMarket.US）

---

## ④ 通达信桥（A股实盘，最难 —— 需 Windows 交易机）

1. **Windows 机**安装通达信客户端（需 L2 权限账号）→ 登录
2. **部署桥服务**：`bridge/windows/`（Python + 通达信插件目录）→ 跑起来监听 8550
3. **网络打通**：Linux 服务器能访问 `http://<Windows-IP>:8550`
4. **交易所设置 → 通达信桥**：填桥地址 + token → 测试连接（应显示客户端已连）
5. 桥的能力：实时五档（Buyp/Buyv/Sellp/Sellv）、L2 扩展日线（exday）、
   快照、K线、账户/持仓/委托、下单/撤单

**坑（实测）**：桥对不同方法返回形状不一致（exday 包 Value，快照平铺）；
L2 逐笔（tick）桥没有（要 TdxAiData 云端）；1m 分钟K桥返回空（客户端无缓存）

---

## 市场 → 交易所映射（执行通道选择）

总控 → 交易所设置 → **市场 → 交易所**选择器：
- A股固定通达信桥；港股可选 老虎/IBKR/富途；美股可选 IBKR/老虎
- 保存到 `config/broker_market.json`（gitignored，运行时配置）
- HK/US 执行链按映射实例化券商（tiger/ibkr 通用接口；futu 执行 v2 待接）

## 安全铁律（新手必读）

1. **执行开关默认全关**：`configs/intraday_exec.json` / `hk_exec.json` / `us_exec.json`
   都是 `{"enabled": false}` —— 克隆即零执行，显式开启才下单
2. **--execute 才真下单**：所有脚本默认 dry-run；`--force` 不开启执行
3. **闸门系统全程在**：虚拟现金红线/杠杆约束/T+1/涨跌停/在途单防重复
   —— 模型有决策权，系统有否决权
4. **模拟盘先行**：老虎模拟 → IBKR Paper → 富途 SIM → 全链路 dry-run 通过 → 才碰实盘

## 新手推荐路径（Checklist）

- [ ] Docker 主栈跑起（面板可见）
- [ ] 老虎：开通 → 模拟账户 → 测试连接（30 分钟）
- [ ] IBKR：Gateway 装好 → Paper 申请 → 登录 → API 开启 → 测试（1-2 小时）
- [ ] 富途：OpenD → 扫码 → 测试（20 分钟）
- [ ] 通达信桥：Windows 部署（半天，需交易机）
- [ ] 交易所设置页全部"测试连接"绿
- [ ] 映射选择器配好（模拟盘优先）
- [ ] 执行开关保持关闭，先跑 dry-run 观察一周
