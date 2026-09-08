#!/usr/bin/env python3
"""单策略单标的回测：quantdb 日线 → PyneCore ScriptRunner → 统计/逐笔 CSV。

用法：
  .venv/bin/python run_one.py smoke_sma.py 600309.SH
  .venv/bin/python run_one.py 633_DIY自定义策略构建器\\[ZP\\]v1.py 000001.SZ --out /tmp/out

产物：{out}/{script}_{symbol}_strat.csv（TradingView 口径统计）
      {out}/{script}_{symbol}_trade.csv（逐笔）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pynecore.core.script_runner import ScriptRunner  # noqa: E402

from quantdb_feed import load_bars, syminfo_for  # noqa: E402


def run_one(script: Path, symbol: str, out_dir: Path, start: str = "",
            end: str = "", inputs: dict | None = None) -> dict:
    bars = load_bars(symbol, start=start, end=end)
    if len(bars) < 30:
        return {"ok": False, "err": f"bar 不足（{len(bars)} 根）"}
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{script.stem}_{symbol.replace('.', '_')}"
    t0 = time.time()
    try:
        runner = ScriptRunner(
            script_path=script,
            ohlcv_iter=iter(bars),
            syminfo=syminfo_for(symbol),
            plot_path=None,
            strat_path=out_dir / f"{stem}_strat.csv",
            trade_path=out_dir / f"{stem}_trade.csv",
            inputs=inputs or None,
        )
        for _item in runner.run_iter():   # 策略产出 3 元组 (candle, plot, trades)，指标 2 元组
            pass
    except Exception as exc:  # noqa: BLE001 单策略失败不能拖垮批量
        return {"ok": False, "err": f"{type(exc).__name__}: {str(exc)[:200]}",
                "bars": len(bars), "secs": round(time.time() - t0, 2)}
    strat = out_dir / f"{stem}_strat.csv"
    trade = out_dir / f"{stem}_trade.csv"
    return {"ok": True, "bars": len(bars), "secs": round(time.time() - t0, 2),
            "strat_csv": str(strat) if strat.exists() else "",
            "trade_csv": str(trade) if trade.exists() else "",
            "trades": max(0, len(trade.read_text(encoding="utf-8").splitlines()) - 1)
            if trade.exists() else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("symbol")
    ap.add_argument("--out", default="/tmp/pine_out")
    ap.add_argument("--from", dest="start", default="")
    ap.add_argument("--to", dest="end", default="")
    a = ap.parse_args()
    r = run_one(Path(a.script).resolve(), a.symbol, Path(a.out), a.start, a.end)
    print(r)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
