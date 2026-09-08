"""行情实验室后端：quantdb K线 + 策略回测（PyneCore 运行时）。

设计要点：
- **K线**：quantdb `1_kline_data/{daily_unadjusted,daily_forward,daily_backward}`，
  默认不复权（看盘口径），回测默认后复权（不复权会被除权跳空污染）。
- **回测**：PyneCore `ScriptRunner`（与 `pyne compile` 产物同一个运行时），
  策略模板在 `backend/services/lab_strategies/*.py`（Pyne 代码）。
  pynecore 未安装时只影响回测，K线/搜索照常可用（降级不阻塞）。
- **A股口径**：模板统一 initial_capital=100000 / 95% 仓位 / 双边 0.05% 费率 /
  1 tick 滑点；多头单向（A股现金账户不能做空）。
"""
from __future__ import annotations

import csv
import os
import tempfile
import threading
from pathlib import Path

# PyneCore 每次 import 策略模块都会把 <script>.toml 重写一遍（core/script.py 的
# _decorate）。批量回测是多进程、且每个回测都要清缓存重导入 → 并发写同一个 toml，
# 某个进程正好读到半截文件就报 "Invalid TOML: missing [script] section!"。
# 模板的输入值我们一律走 ScriptRunner(inputs=...) 传入，不需要它落盘。
os.environ.setdefault("PYNE_SAVE_SCRIPT_TOML", "0")

# PyneCore 的脚本模块缓存在 sys.modules 里，回测前必须清（见 _purge_script_module）。
# 清缓存是进程级操作，多个回测并发会互相踩 → 用锁把「清缓存 + 跑」串行化。
# 单次回测 0.1~2s，串行对交互式使用无感。
_RUN_LOCK = threading.Lock()

# ---------- 路径 ----------

_ROOT_CANDIDATES = (
    Path("/data/quantdb"),
    Path.home() / "projects/quantmind/data/quantdb",
    Path("/home/zbox/projects/quantmind/data/quantdb"),
)

ADJ_DIRS = {
    "unadjusted": "daily_unadjusted",
    "forward": "daily_forward",
    "backward": "daily_backward",
}

STRATEGY_DIR = Path(__file__).resolve().parent / "lab_strategies"

# 策略注册表：id → (文件名, 显示名, 说明, 参数)
STRATEGIES: dict[str, dict] = {
    "sma_cross": {
        "file": "sma_cross.py", "name": "双均线交叉",
        "desc": "快线上穿慢线买入，死叉离场。经典趋势跟随，参数少、抗过拟合。",
        "params": [{"k": "fast", "label": "快线", "default": 5},
                   {"k": "slow", "label": "慢线", "default": 20}]},
    "ema_trend": {
        "file": "ema_trend.py", "name": "EMA 趋势跟踪",
        "desc": "EMA 快慢线交叉，比 SMA 反应更快，适合中长线趋势。",
        "params": [{"k": "fast", "label": "快线", "default": 20},
                   {"k": "slow", "label": "慢线", "default": 60}]},
    "rsi_reversal": {
        "file": "rsi_reversal.py", "name": "RSI 反转",
        "desc": "RSI 从超卖区上穿买入，进入超买区离场。震荡市风格。",
        "params": [{"k": "length", "label": "RSI 周期", "default": 14},
                   {"k": "oversold", "label": "超卖线", "default": 30},
                   {"k": "overbought", "label": "超买线", "default": 70}]},
    "boll_breakout": {
        "file": "boll_breakout.py", "name": "布林带突破",
        "desc": "收盘上穿上轨买入，回落中轨离场。波动放大时入场。",
        "params": [{"k": "length", "label": "周期", "default": 20},
                   {"k": "mult", "label": "带宽倍数", "default": 2.0}]},
    "donchian": {
        "file": "donchian.py", "name": "唐奇安通道突破",
        "desc": "突破 N 日新高买入，跌破 M 日新低离场（海龟简化版）。",
        "params": [{"k": "entry_len", "label": "入场周期", "default": 20},
                   {"k": "exit_len", "label": "离场周期", "default": 10}]},
    "atr_trend": {
        "file": "atr_trend.py", "name": "ATR 通道趋势",
        "desc": "上穿 EMA+ATR 上轨买入，跌破 EMA 离场。波动率自适应止损。",
        "params": [{"k": "ma_len", "label": "均线周期", "default": 20},
                   {"k": "atr_len", "label": "ATR 周期", "default": 14},
                   {"k": "mult", "label": "通道倍数", "default": 1.5}]},
}


def _root() -> Path:
    for p in _ROOT_CANDIDATES:
        if (p / "1_kline_data").is_dir():
            return p
    raise FileNotFoundError("quantdb 未挂载（/data/quantdb 或 ~/projects/quantmind/data/quantdb）")


def _kline_glob(adj: str) -> str:
    d = ADJ_DIRS.get(adj)
    if not d:
        raise ValueError(f"未知复权口径: {adj}")
    return str(_root() / "1_kline_data" / d / "dt=*" / "*.parquet")


# ---------- 代码归一 / 名称 ----------

def normalize_code(symbol: str) -> str:
    """600309 / 600309.SH / SH600309 / 600309.SS → 600309.SH。"""
    s = str(symbol or "").strip().upper().replace(" ", "")
    if not s:
        return ""
    if s.endswith(".SS"):
        s = s[:-3] + ".SH"
    if "." in s:
        code, _, mkt = s.partition(".")
        if code.isdigit() and len(code) == 6:
            return f"{code}.{'SH' if mkt in ('SH', 'SS') else mkt}"
    if s.startswith(("SH", "SZ", "BJ")) and s[2:].isdigit():
        return f"{s[2:]}.{s[:2]}"
    if s.isdigit():
        if len(s) == 6:
            if s[0] in "65" or s.startswith(("11", "13")) or s[:2] in ("51", "56", "58"):
                return f"{s}.SH"
            if s.startswith(("88", "43", "83", "87", "92")):
                return f"{s}.BJ"
            return f"{s}.SZ"
    return s


_names_cache: dict | None = None


def _names() -> dict:
    """全市场代码→名称（instrument_detail，进程内缓存）。"""
    global _names_cache
    if _names_cache is not None:
        return _names_cache
    out: dict = {}
    for p in (_root() / "2_base_sector/instrument_detail/instrument_detail.parquet",
              _root() / "2_base_sector/instrument_detail.parquet"):
        if not p.is_file():
            continue
        try:
            import duckdb

            for sym, nm in duckdb.connect().execute(
                    "SELECT Symbol, Name FROM read_parquet(?)", [str(p)]).fetchall():
                if sym and nm:
                    out[str(sym)] = str(nm)
        except Exception:  # noqa: BLE001 名称缺失不影响行情
            pass
        break
    _names_cache = out
    return out


def stock_name(symbol: str) -> str:
    code = normalize_code(symbol)
    return _names().get(code, "") or code.split(".")[0]


def search_symbols(q: str = "", limit: int = 30) -> list[dict]:
    """代码/名称模糊搜索（代码前缀优先，其次名称包含）。"""
    names = _names()
    if not names:
        return []
    q = str(q or "").strip()
    hits = []
    for code, name in names.items():
        if q and not (code.startswith(q.upper()) or q in name):
            continue
        hits.append({"code": code, "name": name})
        if len(hits) >= limit * 4:
            break
    hits.sort(key=lambda x: (0 if x["code"].startswith(q.upper()) else 1, x["code"]))
    return hits[:limit]


# ---------- K线 ----------

def load_klines(symbol: str, adj: str = "unadjusted", limit: int = 600,
                start: str = "", end: str = "") -> list[dict]:
    """quantdb 日线 → [{date, open, high, low, close, volume}]（旧→新）。"""
    import duckdb

    code = normalize_code(symbol)
    if not code:
        return []
    where = [f"symbol = '{code}'"]
    if start:
        where.append(f"time >= '{start[:4]}-{start[4:6]}-{start[6:8]}'"
                     if len(start) == 8 else f"time >= '{start}'")
    if end:
        where.append(f"time <= '{end[:4]}-{end[4:6]}-{end[6:8]}'"
                     if len(end) == 8 else f"time <= '{end}'")
    sql = (f"SELECT time, open, high, low, close, volume "
           f"FROM read_parquet('{_kline_glob(adj)}') "
           f"WHERE {' AND '.join(where)} ORDER BY time")
    rows = duckdb.connect().execute(sql).fetchall()
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return [{"date": str(t)[:10], "open": float(o), "high": float(h), "low": float(l),
             "close": float(c), "volume": float(v or 0)}
            for t, o, h, l, c, v in rows
            if o == o and c == c]      # NaN 行（停牌/坏数据）剔除


# ---------- 回测 ----------

def _require_pyne():
    try:
        from pynecore.core.script_runner import ScriptRunner   # noqa: F401
        from pynecore.core.syminfo import SymInfo              # noqa: F401
        from pynecore.types.ohlcv import OHLCV                 # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PyneCore 运行时未安装：pip install 'pynesys-pynecore[cli]'（回测功能不可用）"
        ) from exc


def _syminfo(symbol: str, name: str):
    from pynecore.core.syminfo import SymInfo

    code = normalize_code(symbol)
    return SymInfo(
        prefix="SSE" if code.endswith(".SH") else ("BSE" if code.endswith(".BJ") else "SZSE"),
        description=name or code, ticker=code.split(".")[0], currency="CNY",
        basecurrency=None, period="1D", type="stock", volumetype="base",
        mintick=0.01, pricescale=100, minmove=1, pointvalue=1.0, mincontract=100.0,
        opening_hours=[], session_starts=[], session_ends=[], timezone="Asia/Shanghai",
    )


def _bars_ohlcv(bars: list[dict]):
    from datetime import datetime, timedelta, timezone

    from pynecore.types.ohlcv import OHLCV

    cn = timezone(timedelta(hours=8))
    out = []
    for b in bars:
        y, m, d = (int(x) for x in b["date"].split("-"))
        ts = int(datetime(y, m, d, 9, 30, tzinfo=cn).timestamp() * 1000)
        out.append(OHLCV(timestamp=ts, open=b["open"], high=b["high"], low=b["low"],
                         close=b["close"], volume=b["volume"]))
    return out


def _parse_strat_csv(path: Path) -> dict:
    """TradingView 口径统计 CSV → 扁平 dict（取 All CNY / All % 两列）。"""
    out: dict = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip():
                continue
            key = row[0].strip()
            if key == "Metric":        # 表头行
                continue
            vals = row[1:]
            out[key] = {
                "value": _num(vals[0]) if len(vals) > 0 else None,
                "pct": _num(vals[1]) if len(vals) > 1 else None,
            }
    return out


def _num(s) -> float | None:
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def _fix_infinite_pf(stats: dict) -> None:
    """无亏损交易时 PyneCore 的 Profit factor 写 0（真值应为 ∞）→ 归为缺失。

    不处理的话，全胜标的会以「盈亏比 0」混进均值，把策略排名算反。
    """
    pf = stats.get("Profit factor")
    loss = (stats.get("Gross loss") or {}).get("value")
    if pf and pf.get("value") == 0 and not loss:
        pf["value"] = None


def _parse_trades_csv(path: Path) -> list[dict]:
    """逐笔 CSV → 开/平配对记录。"""
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = str(r.get("Type") or "")
            if not t:
                continue
            rows.append({
                "no": int(_num(r.get("Trade #")) or 0),
                "type": t,
                "signal": str(r.get("Signal") or ""),
                "time": str(r.get("Date/Time") or "")[:19].replace("T", " "),
                "price": _num(r.get("Price CNY")),
                "qty": _num(r.get("Contracts")),
                "profit": _num(r.get("Profit CNY")),
                "profit_pct": _num(r.get("Profit %")),
            })
    trades = []
    cur: dict | None = None
    for r in rows:
        if r["type"].startswith("Entry"):
            cur = {"entry_time": r["time"], "entry_price": r["price"],
                   "qty": r["qty"], "signal": r["signal"],
                   "exit_time": None, "exit_price": None,
                   "profit": None, "profit_pct": None}
            trades.append(cur)
        elif r["type"].startswith("Exit") and cur is not None:
            cur["exit_time"] = r["time"]
            cur["exit_price"] = r["price"]
            cur["profit"] = r["profit"]
            cur["profit_pct"] = r["profit_pct"]
            cur = None
    return trades


def _purge_script_module(script_path: Path) -> None:
    """回测前清掉脚本模块缓存（同进程内多次回测必须隔离）。

    PyneCore 把 Script 对象（含 SimPosition 持仓、成交统计）挂在脚本模块的
    `main` 函数上，而 `import_script` 走 `import_module(stem)` 命中 sys.modules 缓存——
    不清缓存的话第 N 次回测会继承上次的持仓与统计（实测第 2 次成交数翻倍、
    净值起点从 10 万掉到 6.9 万）。脚本模板之间无相互 import，按 stem 前缀清理即可。
    """
    import sys

    stem = script_path.stem
    for key in [k for k in sys.modules if k == stem or k.startswith(stem + ".")]:
        del sys.modules[key]


def _run_strategy_on_bars(strategy_id: str, bars: list[dict], symbol: str, adj: str,
                          overrides: dict | None = None, want_trades: bool = True) -> dict:
    """核心回测：K线已在手 → 跑一次 PyneCore，返回 {stats, trades, equity, meta}。

    与 run_backtest 分离是为了批量回测：同一标的的 K 线只从 quantdb 读一次，
    6 个策略复用（读盘是瓶颈，6× 重复读会白烧 6 倍 IO）。
    """
    spec = STRATEGIES.get(strategy_id)
    if not spec:
        raise ValueError(f"未知策略: {strategy_id}")
    _require_pyne()
    from pynecore.core.script_runner import ScriptRunner

    script = STRATEGY_DIR / spec["file"]
    if not script.is_file():
        raise FileNotFoundError(f"策略文件缺失: {script}")

    code = normalize_code(symbol)
    with tempfile.TemporaryDirectory(prefix="lab_bt_") as td:
        tdir = Path(td)
        strat_csv = tdir / "strat.csv"
        trade_csv = tdir / "trades.csv"
        with _RUN_LOCK:
            _purge_script_module(script)
            # 历史遗留的 <策略>.toml 会在 import 时覆盖 input 默认值（PyneCore 的
            # input 读 _old_input_values）→ 参数来源必须只有 .py 与本次 inputs 两条路，
            # 每次回测前把残留清掉。
            script.with_suffix(".toml").unlink(missing_ok=True)
            runner = ScriptRunner(
                script_path=script, ohlcv_iter=iter(_bars_ohlcv(bars)),
                syminfo=_syminfo(symbol, stock_name(symbol)),
                plot_path=None, strat_path=strat_csv, trade_path=trade_csv,
                inputs=overrides or None,
            )
            for _ in runner.run_iter():
                pass
        stats = _parse_strat_csv(strat_csv)
        _fix_infinite_pf(stats)
        trades = _parse_trades_csv(trade_csv) if want_trades else []

    eq = list(getattr(runner, "equity_curve", None) or []) if want_trades else []
    equity = [{"date": b["date"], "value": round(float(v), 2)}
              for b, v in zip(bars[-len(eq):], eq)] if eq else []
    return {
        "stats": stats,
        "trades": trades,
        "equity": equity,
        "meta": {"strategy": strategy_id, "name": spec["name"], "symbol": code,
                 "name_cn": stock_name(symbol), "adj": adj,
                 "start": bars[0]["date"], "end": bars[-1]["date"], "bars": len(bars),
                 "params": overrides or {}},
    }


def run_backtest(strategy_id: str, symbol: str, adj: str = "backward",
                 start: str = "", end: str = "", params: dict | None = None) -> dict:
    """单策略 × 单标的回测。返回 {stats, trades, equity, meta}。"""
    if strategy_id not in STRATEGIES:
        raise ValueError(f"未知策略: {strategy_id}")
    bars = load_klines(symbol, adj=adj, limit=0, start=start, end=end)
    if len(bars) < 60:
        raise ValueError(f"K线不足（{len(bars)} 根，至少 60 根）")
    overrides = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    return _run_strategy_on_bars(strategy_id, bars, symbol, adj, overrides)


def list_strategies() -> list[dict]:
    return [{"id": k, "name": v["name"], "desc": v["desc"], "params": v["params"]}
            for k, v in STRATEGIES.items()]
