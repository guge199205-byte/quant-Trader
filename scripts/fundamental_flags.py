"""基本面/长期趋势劣化清单（fundamental_flags）：quantdb → 买入硬拦数据底座。

用户口径（2026-09-11）：「财务状况不好的、基于kuantdb、也需要、财务状况一直
不好的、也需要排除、长期下跌趋势，不管牛市，熊市、都不好的」。

两条判据（都基于 quantdb 本地数据，不依赖网络）：
  - 财务一直不好：**连续 N 年（默认 3）归母净利润为负**，或**净资产为负**
    （资不抵债）。只用已披露报表（m_anntime ≤ asof）——防未来函数。
  - 长期下跌趋势：**相对全市场**的中位收益落后（近 1 年 < -25% 且近 2 年 < -35%）
    且现价在年线（MA250）下方。为什么用相对而非绝对：实测单看"价格在年线下
    且年线下行"会命中 58% 的股票——那是市场 beta（大盘熊了一年），不是个股
    长期差；用户要的是"不管牛市熊市都不好"的票，即**涨得少、跌得多**。

本模块只负责**算 + 落盘**（data/fundamental_flags.json，每日一次，cron）；
消费在 risk_list（kind "weak"，粘性条目——存续状态不该被短事件提前解除）。
为什么不让 risk_list 每次 refresh 现算：全市场 5600 只 × 600 交易日 + 11k 个
财报 parquet，一轮 ~15s，而 risk_list 每交易日要重建 6 次。

用法：
  python scripts/fundamental_flags.py build     # 重建缓存
  python scripts/fundamental_flags.py show      # 打印当前清单
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

QUANTDB = Path(os.environ.get("QUANTDB_DIR", "/home/zbox/projects/quantmind/data/quantdb"))
OUT = ROOT / "data" / "fundamental_flags.json"

# 趋势判据用**后复权**日线：未复权价会把送转/分红当跌幅（10 送 10 → 价格腰斩，
# 假跌 50%），把正常公司误判成"长期下跌"。后复权序列的历史值不随新的除权变化，
# 是收益计算的标准口径。注意：**面值退市判据（risk_list.price_items）必须用未复权**，
# 它看的是实际成交价——两处口径不能混。
KLINE_REL = "1_kline_data/daily_backward"
# 后复权日线停更超过此天数 → 趋势判据停用（宁缺勿错：拿一个月前的价格判"当前
# 是否跌破年线"，比不判更危险）。10 天覆盖春节/国庆长假。
KLINE_STALE_DAYS = 10

BJ = timezone(timedelta(hours=8))

# 计算参数（阈值在 configs/live_symbols.json 的 risk 段，与风险清单共用一套配置）
LOOKBACK_DAYS = 900      # 日线回看自然日（≈600 交易日，够 MA250 + 近 2 年收益）
MA_WINDOW = 250          # 年线
MA_SLOPE_LAG = 60        # 年线斜率回看（交易日）


# ---------------------------------------------------------------- 纯函数（可测）

def loss_streak(rows: list) -> int:
    """年报序列 → 最近连续亏损年数。rows: [(报告期 'YYYY1231'|int, 归母净利润)]。

    只看年报（1231），倒序数到第一份盈利为止。缺值当断点（不猜）。
    """
    ann = [(str(t), v) for t, v in rows if str(t).endswith("1231")]
    ann.sort()
    n = 0
    for _, v in reversed(ann):
        if v is not None and v < 0:
            n += 1
        else:
            break
    return n


def latest_value(rows: list):
    """[(报告期, 值)] → 报告期最新的一条的值（缺值往前找）。空/全缺 → None。"""
    for _, v in sorted(((str(t), v) for t, v in rows), reverse=True):
        if v is not None:
            return v
    return None


def latest_span(rows: list) -> str:
    """**最近这段**连续亏损的年报区间文本，如 '2023-2025'（只用于理由文案）。

    必须与 loss_streak 同步走：取全部亏损年份的首尾会写出「连续4年亏损
    （2016-2025）」这种自相矛盾的文案（2016 那次是十年前的旧账）。
    """
    ann = sorted((str(t), v) for t, v in rows if str(t).endswith("1231"))
    run: list = []
    for t, v in reversed(ann):
        if v is not None and v < 0:
            run.append(t[:4])
            continue
        break
    if not run:
        return ""
    return f"{run[-1]}-{run[0]}" if len(run) >= 2 else run[0]


def trend_stats(closes: list):
    """收盘价序列（升序）→ {close, ma250, ma250_prev, ret250, ret500}；不够长 → None。"""
    n = len(closes)
    if n < MA_WINDOW + MA_SLOPE_LAG:
        return None
    cur = closes[-1]
    ma250 = sum(closes[-MA_WINDOW:]) / MA_WINDOW
    ma_prev = sum(closes[-MA_WINDOW - MA_SLOPE_LAG:-MA_SLOPE_LAG]) / MA_WINDOW
    return {
        "close": cur,
        "ma250": ma250,
        "ma250_prev": ma_prev,
        "ret250": cur / closes[-MA_WINDOW] - 1 if n >= MA_WINDOW else None,
        "ret500": cur / closes[-500] - 1 if n >= 500 else None,
    }


def fin_flags(fin: dict, conf: dict) -> dict:
    """{symbol: {"loss_years","span","net_assets"}} → {code6: 理由}（只含命中）。"""
    out: dict = {}
    years = int(conf.get("loss_years", 3))
    eq_min = float(conf.get("net_assets_min_yi", 0.0)) * 1e8
    for sym, f in (fin or {}).items():
        code = str(sym).split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        parts = []
        if f.get("loss_years", 0) >= years:
            span = f"（{f['span']}）" if f.get("span") else ""
            parts.append(f"连续{f['loss_years']}年亏损{span}")
        eq = f.get("net_assets")
        if eq is not None and eq < eq_min:
            parts.append(f"净资产{eq / 1e8:.1f}亿（资不抵债）")
        if parts:
            out[code] = "；".join(parts)
    return out


def trend_flags(stats: dict, conf: dict) -> dict:
    """{symbol: trend_stats} → {code6: 理由}（相对全市场中位收益判据，只含命中）。

    相对基准取**全市场同期收益中位数**（等权口径，比指数更贴近"大多数票"）：
    涨得比中位数少、跌得比中位数多，且两年窗口都成立，才算"不管牛熊都不好"。
    """
    out: dict = {}
    rel250_max = float(conf.get("trend_rel250_max", -0.25))
    rel500_max = float(conf.get("trend_rel500_max", -0.35))
    rows = {s: st for s, st in (stats or {}).items()
            if st and st.get("ret250") is not None and st.get("ret500") is not None}
    if not rows:
        return out
    med250 = _median([st["ret250"] for st in rows.values()])
    med500 = _median([st["ret500"] for st in rows.values()])
    for sym, st in rows.items():
        code = str(sym).split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        rel250 = st["ret250"] - med250
        rel500 = st["ret500"] - med500
        if st["close"] >= st["ma250"] or rel250 >= rel250_max or rel500 >= rel500_max:
            continue
        out[code] = (f"长期下跌：近1年跑输大盘{abs(rel250):.0%}、"
                     f"近2年跑输{abs(rel500):.0%}，且现价位于年线下方")
    return out


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---------------------------------------------------------------- 数据读取（quantdb）

def _con():
    import duckdb

    c = duckdb.connect()
    c.execute("PRAGMA threads=4")
    return c


def read_income(asof: date) -> dict:
    """{symbol: [(报告期, 归母净利润)]}，只用 m_anntime ≤ asof 的报表（防未来函数）。"""
    try:
        df = _con().execute(
            f"SELECT Symbol, m_timetag, net_profit_excl_min_int_inc AS np "
            f"FROM read_parquet('{QUANTDB}/3_financial_data/income/*.parquet') "
            f"WHERE m_anntime <= '{asof:%Y%m%d}'").df()
    except Exception as exc:  # noqa: BLE001 数据缺失不阻塞（fail-open）
        print(f"⚠️ 读 income 失败：{str(exc)[:120]}", file=sys.stderr)
        return {}
    return {s: list(zip(g["m_timetag"], g["np"])) for s, g in df.groupby("Symbol")}


def read_balance(asof: date) -> dict:
    """{symbol: [(报告期, 归母净资产)]}，口径同 read_income。"""
    try:
        df = _con().execute(
            f"SELECT Symbol, m_timetag, tot_shrhldr_eqy_excl_min_int AS eq "
            f"FROM read_parquet('{QUANTDB}/3_financial_data/balance/*.parquet') "
            f"WHERE m_anntime <= '{asof:%Y%m%d}'").df()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 读 balance 失败：{str(exc)[:120]}", file=sys.stderr)
        return {}
    return {s: list(zip(g["m_timetag"], g["eq"])) for s, g in df.groupby("Symbol")}


def latest_kline_dt(asof: date, root: Path | None = None) -> date | None:
    """日线里 dt ≤ asof 的最新一天；无分区 → None。"""
    base = Path(root or QUANTDB) / KLINE_REL
    ds = [p.name[3:] for p in base.glob("dt=*")
          if p.is_dir() and p.name[3:].isdigit() and p.name[3:] <= asof.strftime("%Y%m%d")]
    return datetime.strptime(max(ds), "%Y%m%d").date() if ds else None


def read_closes(asof: date) -> dict:
    """{symbol: [后复权收盘价（升序）]}，取 dt ≤ asof 的 LOOKBACK_DAYS 自然日。"""
    dmax = latest_kline_dt(asof)
    if dmax is None or dmax < asof - timedelta(days=KLINE_STALE_DAYS):
        print(f"⚠️ 后复权日线停更（最新 {dmax}）→ 本轮跳过长期下跌判据", file=sys.stderr)
        return {}  # fail-open：不给陈旧数据背书
    lo = (asof - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        df = _con().execute(
            f"SELECT symbol, dt, close FROM read_parquet("
            f"'{QUANTDB}/{KLINE_REL}/dt=*/data.parquet') "
            f"WHERE dt >= {lo} AND dt <= {asof:%Y%m%d} ORDER BY symbol, dt").df()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 读日线失败：{str(exc)[:120]}", file=sys.stderr)
        return {}
    return {s: g["close"].astype(float).tolist() for s, g in df.groupby("symbol")}


# ---------------------------------------------------------------- 组装 / 落盘

def build(asof: date | None = None, conf: dict | None = None, out: Path | None = None,
          income: dict | None = None, balance: dict | None = None,
          closes: dict | None = None) -> dict:
    """扫全市场 → 落盘 data/fundamental_flags.json。显式传数据即不读真实源（测试用）。"""
    from risk_list import load_conf

    asof = asof or datetime.now(BJ).date()
    conf = conf if conf is not None else load_conf()
    inc = read_income(asof) if income is None else income
    bal = read_balance(asof) if balance is None else balance
    cls = read_closes(asof) if closes is None else closes

    fin = {sym: {"loss_years": loss_streak(inc.get(sym, [])),
                 "span": latest_span(inc.get(sym, [])),
                 "net_assets": latest_value(bal.get(sym, []))}
           for sym in set(inc) | set(bal)}
    stats = {}
    for sym, cs in cls.items():
        st = trend_stats(cs)
        if st:
            stats[sym] = st

    items: dict = {}
    for flags_map, tag in ((fin_flags(fin, conf), "fin"), (trend_flags(stats, conf), "trend")):
        for code, reason in flags_map.items():
            it = items.setdefault(code, {"flags": [], "reasons": [], "asof": asof.isoformat()})
            it["flags"].append(tag)
            it["reasons"].append(reason)
    for it in items.values():
        it["reason"] = "；".join(it.pop("reasons"))

    doc = {"asof": asof.isoformat(), "generated_at": datetime.now(BJ).isoformat(),
           "items": items}
    p = out or OUT
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return {"fin": sum(1 for v in items.values() if "fin" in v["flags"]),
            "trend": sum(1 for v in items.values() if "trend" in v["flags"]),
            "total": len(items), "scanned": len(cls)}


def main() -> int:
    ap = argparse.ArgumentParser(description="基本面/长期趋势劣化清单（quantdb）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="重建 data/fundamental_flags.json")
    sub.add_parser("show", help="打印当前清单")
    a = ap.parse_args()

    if a.cmd == "build":
        st = build()
        print(f"✓ 财务劣化 {st['fin']} / 长期下跌 {st['trend']} / 合计 {st['total']}"
              f"（扫 {st['scanned']} 只）→ {OUT}")
        return 0
    try:
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        items = doc.get("items") or {}
    except (OSError, ValueError):
        print("（无缓存文件）")
        return 0
    print(f"asof={doc.get('asof')} 共 {len(items)} 只")
    for code, it in sorted(items.items()):
        print(f"  {code} [{'/'.join(it.get('flags', []))}] {it.get('reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

