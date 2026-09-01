# baymax 盘中自动执行改造（2026-09-01 合并版）

## 状态：已合并进宿主 scripts/（三处同源）
- 宿主 `scripts/live_hourly_analysis.py`（cron 实际运行的版本，858+ 行）
- 本目录 `configs/patches/live_hourly_analysis.py`（备份/容器重建源）
- 容器 `/app/scripts/live_hourly_analysis.py`（baked，docker cp 同步）

容器重建（`docker compose up --force-recreate`）后按 README 末尾恢复方法重新 docker cp。

## 能力（live_hourly_analysis.py）
1. 提示词要求 LLM 输出结构化 JSON 决策块（hold/sell/buy/**watch**）
2. parse_intraday_decision / execute_intraday_decision：闸门 + 桥下单 + 分账记账
   - sell：只卖可卖量（T+1）、跌停不接、100 股整数倍
   - buy：仅持仓加仓、涨停不追、单票 ≤ 剩余额度 20%、
     **子账户虚拟现金不透支（分账额度红线）**、账户现金兜底
3. run_analysis：每 agent 每小时**只执行一轮**（多模式多轮会叠加买入突破分账额度）
4. **watch 条件位**（跌破止损/到位止盈）→ `live_price_watch.py` 分钟级哨兵执行
5. main 支持 --execute；开关 `configs/intraday_exec.json = {"enabled": true}`

## 能力（live_llm_trade.py，09:35 主路径）
- 修复卖出不记账 bug（record_sell + save_ledger）
- 买入同样加子账户虚拟现金红线
- 配置开关 intraday_exec_enabled()

## 新文件：live_price_watch.py（分钟级价格哨兵）
- 规则文件 `data/live_watch.json`（每 agent 每小时整组刷新，最新分析说了算）
- cron `* 10-12,14-16 * * 1-5`（JST；北京 9:30-11:30/13:00-15:00 每分钟）
- 触发即走同一套卖出闸门 → record_sell → `logs/live_watch_YYYYMMDD.jsonl`
- T+1 不可卖/跌停保留规则；非持仓/不足 1 手作废

## 恢复方法（容器重建后）
docker cp scripts/live_hourly_analysis.py baymax-api:/app/scripts/
docker cp scripts/live_price_watch.py baymax-api:/app/scripts/
（宿主 scripts/ 为唯一真源；本目录仅备份）
