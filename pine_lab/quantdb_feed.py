"""quantdb → PyneCore 数据桥：A股日线喂给 Pine 运行时。

为什么不用 pyne 自带的 provider/CSV：`pyne run` 需要 `.ohlcv` + 同名 `.toml`
symbol info 文件；而 PyneCore 的 `ScriptRunner` 直接收 `Iterable[OHLCV]` +
`SymInfo`，可以零落盘把 quantdb 的 parquet 灌进去——1001 个策略 × N 只标的
如果每个都落 CSV，磁盘和 IO 都受不了。

口径（与系统其它部分一致）：
- 用 `daily_backward`（后复权）——回测不能吃除权跳空，前复权会引入未来信息
- 时间戳 = 当日 09:30 北京时间（TradingView 日线口径），毫秒
- volume 单位股；A股最小交易单位 100 股（`mincontract=100`）
"""
from __future__ import annotations

import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

CN_TZ = timezone(timedelta(hours=8))
QUANTDB_CANDIDATES = (
    Path("/home/zbox/projects/quantmind/data/quantdb/1_kline_data/daily_backward"),
    Path("/data/quantdb/1_kline_data/daily_backward"),
)


def quantdb_dir() -> Path:
    for p in QUANTDB_CANDIDATES:
        if p.is_dir():
            return p
    raise FileNotFoundError("找不到 quantdb daily_backward 分区目录")


def load_bars(symbol: str, start: str = "", end: str = "") -> list[OHLCV]:
    """quantdb 后复权日线 → [OHLCV]。symbol 带后缀（600309.SH）或不带都行。"""
    base = quantdb_dir()
    where = []
    if start:
        where.append(f"time >= '{start[:4]}-{start[4:6]}-{start[6:]}'"
                     if len(start) == 8 else f"time >= '{start}'")
    if end:
        where.append(f"time <= '{end[:4]}-{end[4:6]}-{end[6:]}'"
                     if len(end) == 8 else f"time <= '{end}'")
    sym = symbol if "." in symbol else None
    if sym:
        where.append(f"symbol = '{sym}'")
    else:
        where.append(f"symbol LIKE '{symbol}.%'")
    sql = (f"SELECT symbol, time, open, high, low, close, volume "
           f"FROM read_parquet('{base}/dt=*/data.parquet') "
           f"WHERE {' AND '.join(where)} ORDER BY time")
    rows = duckdb.connect().execute(sql).fetchall()   # 不用 .df()：省掉 pandas/numpy 依赖
    if not rows:
        return []
    out: list[OHLCV] = []
    for _sym, t, o, h, l, c, v in rows:
        if any(x != x for x in (o, h, l, c)):   # NaN 保护（停牌/坏行）
            continue
        day = t.date() if hasattr(t, "date") else t
        ts = int(datetime(day.year, day.month, day.day, 9, 30, tzinfo=CN_TZ).timestamp() * 1000)
        out.append(OHLCV(timestamp=ts, open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=float(v or 0)))
    return out


def syminfo_for(symbol: str, period: str = "1D", name: str = "") -> SymInfo:
    """A股股票 SymInfo。session 留空——日线粒度下 PyneCore 不依赖盘中时段。"""
    code = symbol.split(".")[0]
    return SymInfo(
        prefix="SSE" if symbol.endswith(".SH") else "SZSE",
        description=name or symbol,
        ticker=code,
        currency="CNY",
        basecurrency=None,
        period=period,
        type="stock",
        volumetype="base",
        mintick=0.01,
        pricescale=100,
        minmove=1,
        pointvalue=1.0,
        mincontract=100.0,        # A股 1 手 = 100 股
        opening_hours=[],
        session_starts=[],
        session_ends=[],
        timezone="Asia/Shanghai",
    )


def available_symbols(limit: int = 0) -> list[str]:
    """最新分区里的全部标的（调试/批量用）。"""
    base = quantdb_dir()
    parts = sorted(glob.glob(f"{base}/dt=*"))
    if not parts:
        return []
    sql = (f"SELECT DISTINCT symbol FROM read_parquet('{parts[-1]}/*.parquet') "
           f"ORDER BY symbol" + (f" LIMIT {limit}" if limit else ""))
    return [str(r[0]) for r in duckdb.connect().execute(sql).fetchall()]
