# Trade Agent 生产化改造（2026Q3 实施）✅ 已完成

> 验证：31 个 pytest 全过 · 5 个 systemd 服务 active · 自动拉起实测通过
> · 鉴权 401/200 实测 · 备份/告警 cron 已装

> 目标：从"能跑"到"生产级"——7×24 无人值守、出事早知道、数据不丢、接实盘有门。

## P1 稳健化
- [x] systemd user 守护：API / MCP×3 / dsh 自动拉起
- [x] API 鉴权：X-API-Token（可配置，局域网内前端共享）
- [x] 手续费/滑点模型（模拟盘贴近真实成本）
- [x] 每日备份脚本（data + 配置，保留 7 天）

## P2 可观测
- [x] /api/metrics 指标端点（交易/风控拦截/服务状态）
- [x] 告警脚本（服务掉线/风控熔断 → webhook/文件，可接 push）

## P3 数据层
- [x] SQLite 交易汇总表（API 写入，查询加速）

## P4 测试
- [x] pytest 核心套件（风控/价格/交易/broker/记忆）

## P5 实盘通道
- [x] TDX 下单代码移植（quantmind broker_client 协议）
- [x] 富途 OpenD / 老虎 SDK / 盈透 ib_insync 三个券商适配器（移植自 quantmind 模拟交易生态）
- [x] 审批门（approval_required=true 时拒绝，未配置账户拒绝）
- [x] 三重防护实测 + 6 个 broker 测试（共 37 个 pytest 全过）

## 总控台（agent 跟踪）
- [x] /api/overview 聚合端点（三市场 × agent 状态）
- [x] monitor.html 总控页（服务健康/agent 运行/风控/记忆/净值）
