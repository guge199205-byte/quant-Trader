# 智能体系统维护与优化计划（P0–P2）

> 背景：三天从零到实盘跑通后，工程债与能力红利并存。本计划按 ROI 排序，
> 目标是"业务改动保持配置化红利，工程侧不再踩手工部署/容器重建的雷"。

## P0 · 立刻做（约半天，维护体验质变）

| # | 任务 | 交付物 | DONE 标准 |
|---|---|---|---|
| 1 | **一键部署脚本** | `scripts/deploy.sh`（--ui/--api/--all） | 一条命令完成 前端三件套+资源 200 校验 / backend py_compile+容器热更+重启 |
| 2 | **容器编排自愈与文档** | `scripts/fix_mounts.sh` + `docs/COMPOSE_OPS.md` | BayMax-Trader 挂载 symlink 可一键修复；compose 事实源=仓库 docker-compose.yml（`docker compose -p baymax`），重建流程成文 |
| 3 | **核心逻辑最小测试** | `tests/test_core.py`（unittest，零新依赖） | 覆盖：风险档位判定表 / 预算接线常量 / 假设状态判定(vs基准) / 分歧检测 / JSON 块抽取——回归风险压到纯函数层 |

## P1 · 一两周

| # | 任务 | 说明 | DONE 标准 |
|---|---|---|---|
| 4 | **系统运行日报 agent** | 复用 post_review 管线，北京 17:00：决策/成交/仲裁/预算/复盘完成度/健康告警汇总 | 每日 1 份 `logs/daily_report/{date}.md` 进对话流 |
| 5 | **live_hourly_analysis 拆分（市场包前置）** | 按 数据采集/提示词组装/执行闸门 抽模块（market_state 已独立） | 主文件 <1000 行，行为不变（回归测试兜底） |
| 6 | **数据与日志治理** | review/night_pool/debates 30 天滚动清理；analysis_jobs 超龄清理；假设库 deprecated 自动剔除 | 清理脚本 cron 化，日志目录体量可控 |
| 7 | **前端残留清理** | ModelChat.css/Live.css 历史 override 归并；容器内旧 hash 资产清理 | 样式表单一事实源 |

## P2 · 复刻前

| # | 任务 | 说明 | DONE 标准 |
|---|---|---|---|
| 8 | **市场包化** | broker/时段/规则/工作法收拢 `market/<cn|hk|us>`；三市场入口统一 | HK 用富途 broker 跑通 1 日全链路 |
| 9 | **安全审计** | key 泄露扫描（.env 之外）+ 日志脱敏抽查 + compose env 最小化 | 扫描零命中，文档注明 |
| 10 | **发布流程** | feature 分支 + PR 合入 main；deploy 走 tag | 个人/团队两档流程成文 |

## 依赖与验收原则

P1-5 依赖 P0-3（测试兜底拆分）；P2-8 依赖 P1-5 与 A股两周运行数据。
验收统一：**可回看产物 + 不改变交易语义**（拆分类改动以回归测试为准）。
