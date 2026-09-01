# baymax 盘中自动执行改造补丁（2026-09-01）

## 改动文件（容器 /app/scripts/ 内，非挂载，重建容器需重新应用）
1. live_hourly_analysis.py
   - 提示词要求 LLM 输出结构化 JSON 决策块
   - 新增 parse_intraday_decision / execute_intraday_decision（闸门+桥下单+分账记账）
   - run_analysis 分析后解析决策并执行；main 支持 --execute
   - 配置开关 intraday_exec_enabled()：configs/intraday_exec.json {"enabled": true} 时外部调度自动执行
2. live_llm_trade.py
   - 修复卖出不记账 bug（record_sell + save_ledger）
   - 配置开关 intraday_exec_enabled()

## 恢复方法（容器重建后）
cp /app/configs/patches/live_hourly_analysis.py /app/scripts/live_hourly_analysis.py
cp /app/configs/patches/live_llm_trade.py /app/scripts/live_llm_trade.py

## 开关配置（已挂载，持久化）
configs/intraday_exec.json = {"enabled": true}
