#!/usr/bin/env python3
"""为 A股/港股 agent 生成模拟成交记录（演示用），让前端右侧"成交记录"面板有数据。

用法: python scripts/simulate_demo_trades.py
- 在现有 position.jsonl 末尾追加 buy/sell 记录，持仓/CASH 逐日一致更新
- 价格取自真实数据: A股 daily_prices_sse_50.csv 最近收盘、港股 HK_stock/merged.jsonl 当日成交价
- 运行前自动备份原文件为 position.jsonl.bak-<ts>
"""
import csv
import json
import shutil
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 交易日计划: (日期, action, symbol, 股数)
CN_PLAN = [
    ("2026-08-25", "buy", "600036.SH", 600),
    ("2026-08-26", "buy", "601899.SH", 800),
    ("2026-08-26", "buy", "600900.SH", 500),
    ("2026-08-27", "sell", "600036.SH", 200),
    ("2026-08-27", "buy", "601318.SH", 300),
    ("2026-08-28", "buy", "688981.SH", 100),
]
HK_PLAN = [
    ("2026-08-25", "buy", "00700.HK", 100),
    ("2026-08-26", "buy", "01810.HK", 1000),
    ("2026-08-26", "buy", "09988.HK", 100),
    ("2026-08-27", "sell", "00700.HK", 50),
    ("2026-08-28", "buy", "00700.HK", 50),
]


def load_cn_prices():
    """A股: 每只股票最近收盘价（csv 最新 2025-10-31，历史真实值）。"""
    prices = {}
    with open(ROOT / "data/A_stock/daily_prices_sse_50.csv") as f:
        for row in csv.DictReader(f):
            prices.setdefault(row["ts_code"], []).append(float(row["close"]))
    return {sym: closes[-1] for sym, closes in prices.items()}


def load_hk_prices():
    """港股: 当日成交价（merged.jsonl, 1. buy price / 4. sell price）。"""
    prices = {}
    for line in (ROOT / "data/HK_stock/merged.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        sym = d["Meta Data"]["2. Symbol"]
        prices[sym] = {
            date: (float(v["1. buy price"]), float(v["4. sell price"]))
            for date, v in d["Time Series (Daily)"].items()
        }
    return prices


def run(market, plan, prices, pos_file):
    records = [
        json.loads(line)
        for line in pos_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    last = records[-1]
    pos = deepcopy(last["positions"])
    cash = pos["CASH"]
    nid = max(r["id"] for r in records) + 1
    added = []
    for date, action, sym, shares in plan:
        if any(r.get("this_action", {}).get("symbol") == sym and r["date"] == date for r in records):
            print(f"  [skip] {market} {date} {sym} 已存在")
            continue
        table = prices[sym]
        if isinstance(table, dict):  # 港股: {日期: (买价, 卖价)}
            buy_px, sell_px = table[date]
        else:  # A股: 单收盘价
            buy_px = sell_px = table
        price = buy_px if action == "buy" else sell_px * 1.02
        pos[sym] = pos.get(sym, 0) + (shares if action == "buy" else -shares)
        cash += -shares * price if action == "buy" else shares * price
        final_positions = {**pos, "CASH": round(cash, 2)}
        rec = {
            "date": date,
            "id": nid,
            "this_action": {"action": action, "symbol": sym, "amount": shares},
            "positions": final_positions,
        }
        nid += 1
        added.append(rec)
    if added:
        backup = pos_file.with_name(f"position.jsonl.bak-{int(time.time())}")
        shutil.copy2(pos_file, backup)
        lines = [json.dumps(r, ensure_ascii=False) for r in records + added]
        pos_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {market}: +{len(added)} 条成交 -> {pos_file} (备份 {backup.name})")
        final_pos = added[-1]["positions"]
        holdings = {s: v for s, v in final_pos.items() if s != "CASH" and v}
        print(f"    CASH {final_pos['CASH']:.2f}  持仓 {', '.join(f'{s}:{v}' for s, v in holdings.items())}")
    else:
        print(f"  {market}: 无新增")


def main():
    cn_file = ROOT / "data/agent_data_astock/deepseek-v4-flash/position/position.jsonl"
    hk_file = ROOT / "data/agent_data_hk/deepseek-v4-flash/position/position.jsonl"
    print("A股:")
    run("cn", CN_PLAN, load_cn_prices(), cn_file)
    print("港股:")
    run("hk", HK_PLAN, load_hk_prices(), hk_file)


if __name__ == "__main__":
    main()
