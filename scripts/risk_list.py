#!/usr/bin/env python3
"""事件风险清单：解禁 / 负面新闻 → data/risk_block.json（买入硬拦 + 持仓告警）。

为什么需要（2026-09-08 用户口径）：「A股很多有风险的股票比如解禁这种就是要告警跑的、
负面新闻股票也要避坑掉。模型有的话需要毙掉」——此前解禁/质押只作为提示词标签
（`event_radar.tag_events` → 夜池标注 / 复盘提示），模型照买不误，没有任何硬拦。

数据源与口径：
  - 解禁：data/events/unlock_*.parquet（event_radar 每交易日 18:00 采集）。
    解禁日往前 `unlock_days` 个自然日内、且解禁量占流通市值 ≥ `unlock_ratio_min`%
    → 拦买。解禁日一过风险即兑现，条目自然失效（expire = 解禁日 + 1）。
  - 负面新闻：data/news_brief/history.jsonl 的 micro 事件（sentiment ≤ 阈值）。
    回溯 `news_days` 天，expire = 事件日 + news_days。涉及标的数 > `news_max_tickers`
    的事件按板块级处理，不拦（否则一条板块负面把整个行业禁掉）。
  - 质押：**只告警不拦买**（慢性状态而非事件；比例高不等于当期风险）。

设计取舍：
  - fail-open：数据缺失/损坏 → 空清单。宁可漏拦一只，也不能因数据故障停掉全天买入
    （同 symbol_policy / live_breaker）。
  - 只拦买入：卖出永远放行（否则被套的仓位出不来）。
  - 产物是**文件**而非内存态：买入闸门在多个进程（整点轮/开盘轮/告警）里跑，
    落盘 + mtime 无关的纯读取是最简单的跨进程共享方式。

用法：
  python scripts/risk_list.py refresh          # 重建 data/risk_block.json
  python scripts/risk_list.py show             # 打印当前清单
  python scripts/risk_list.py check 688795.SH  # 查单只
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CONF = ROOT / "configs" / "live_symbols.json"
OUT = ROOT / "data" / "risk_block.json"
NEWS_HISTORY = ROOT / "data" / "news_brief" / "history.jsonl"

BJ = timezone(timedelta(hours=8))

DEFAULTS = {
    "enabled": True,
    "unlock_days": 10,          # 解禁前 N 自然日内禁买
    "unlock_ratio_min": 1.0,    # 解禁占流通市值比例阈值（%）
    "news_days": 3,             # 负面新闻回溯窗口（自然日）
    "news_sentiment_max": -0.5,  # 情绪 ≤ 该值算负面
    "news_max_tickers": 3,      # 事件涉及标的数上限（超过视为板块级，不拦）
    "pledge_warn_ratio": 50.0,  # 质押比例告警阈值（%，只告警）
}


# ---------------------------------------------------------------- 配置

def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_conf(path: Path | None = None) -> dict:
    """读 configs/live_symbols.json 的 risk 段；缺失/脏值 → 默认值逐项兜底。"""
    cfg = dict(DEFAULTS)
    try:
        doc = json.loads((path or CONF).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    risk = doc.get("risk") if isinstance(doc, dict) else None
    if not isinstance(risk, dict):
        return cfg
    for k, default in DEFAULTS.items():
        if k not in risk:
            continue
        cfg[k] = bool(risk[k]) if isinstance(default, bool) else _num(risk[k], default)
    return cfg


# ---------------------------------------------------------------- 解析

def _code6(v) -> str:
    """归一成 6 位代码（数据源后缀口径不统一，风险表按前缀匹配）。"""
    c = str(v or "").strip().split(".")[0]
    return c if len(c) == 6 and c.isdigit() else ""


def _as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _as_dt(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v))
    except (ValueError, TypeError):
        return None
    return d.replace(tzinfo=BJ) if d.tzinfo is None else d


def unlock_items(rows: list, asof: date, conf: dict) -> dict:
    """解禁明细行 → {code6: {reason, kind, expire}}。脏行跳过，绝不抛异常。"""
    out: dict = {}
    horizon = int(_num(conf.get("unlock_days"), DEFAULTS["unlock_days"]))
    ratio_min = _num(conf.get("unlock_ratio_min"), DEFAULTS["unlock_ratio_min"])
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = _code6(r.get("股票代码") or r.get("证券代码"))
        dday = _as_date(r.get("解禁时间"))
        if not code or dday is None or not (asof <= dday <= asof + timedelta(days=horizon)):
            continue
        ratio = _num(r.get("占解禁前流通市值比例"), 0.0) * 100  # 源是小数
        if ratio < ratio_min:
            continue
        kind = str(r.get("限售股类型") or "—")
        out[code] = {
            "reason": f"{dday:%m/%d}解禁{ratio:.1f}%流通盘（{kind}）",
            "kind": "unlock",
            "expire": (dday + timedelta(days=1)).isoformat(),
        }
    return out


def news_items(lines: list, asof: date, conf: dict) -> dict:
    """新闻 micro 事件 → {code6: {reason, kind, expire}}。只取负面且个股级的事件。"""
    out: dict = {}
    days = int(_num(conf.get("news_days"), DEFAULTS["news_days"]))
    sent_max = _num(conf.get("news_sentiment_max"), DEFAULTS["news_sentiment_max"])
    max_tk = int(_num(conf.get("news_max_tickers"), DEFAULTS["news_max_tickers"]))
    floor = asof - timedelta(days=days)
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        ts = _as_dt(line.get("ts"))
        if ts is None or ts.date() < floor:
            continue
        micro = (line.get("segments") or {}).get("news-micro") or {}
        for e in micro.get("events") or []:
            if not isinstance(e, dict):
                continue
            sent = e.get("sentiment")
            if not isinstance(sent, (int, float)) or sent > sent_max:
                continue
            tickers = [t for t in (e.get("tickers") or []) if _code6(t)]
            if not tickers or len(tickers) > max_tk:
                continue  # 板块级事件：不按个股负面拦
            reason = (f"{e.get('event_type') or '负面新闻'}：{e.get('name') or ''}"
                      f"（{str(e.get('note') or '')[:60]}）")
            expire = (ts.date() + timedelta(days=days)).isoformat()
            for t in tickers:
                out[_code6(t)] = {"reason": reason, "kind": "news", "expire": expire}
    return out


def pledge_warns(rows: list, conf: dict) -> dict:
    """质押比例 ≥ 阈值 → {code6: 告警文本}。只告警，不进买入闸门。"""
    out: dict = {}
    thr = _num(conf.get("pledge_warn_ratio"), DEFAULTS["pledge_warn_ratio"])
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = _code6(r.get("股票代码") or r.get("证券代码"))
        ratio = _num(r.get("质押比例"), 0.0)
        if code and ratio >= thr:
            out[code] = f"质押比例{ratio:.0f}%"
    return out


# ---------------------------------------------------------------- 组装 / 落盘

def _merge(a: dict, b: dict) -> dict:
    """同一标的命中多类风险 → 理由合并，失效日取更早（先失效的为准）。"""
    out = dict(a)
    for code, it in b.items():
        if code not in out:
            out[code] = it
            continue
        prev = out[code]
        kinds = "+".join(sorted({prev["kind"], it["kind"]}))
        out[code] = {"reason": f"{prev['reason']}；{it['reason']}",
                     "kind": kinds,
                     "expire": min(prev["expire"], it["expire"])}
    return out


def _read_unlock_rows() -> list:
    try:
        import event_radar  # 同仓库脚本；akshare 为延迟导入，读缓存不触发网络

        f = event_radar._latest_cache("unlock")
        return event_radar._read_cache(f) if f is not None else []
    except Exception:  # noqa: BLE001 数据缺失不阻塞交易（fail-open）
        return []


def _read_pledge_rows() -> list:
    try:
        import event_radar

        f = event_radar._latest_cache("pledge")
        return event_radar._read_cache(f) if f is not None else []
    except Exception:  # noqa: BLE001
        return []


def _read_news_lines() -> list:
    try:
        text = NEWS_HISTORY.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build(asof: date | None = None, conf: dict | None = None,
          unlock_rows: list | None = None, news_lines: list | None = None,
          pledge_rows: list | None = None) -> dict:
    """组装风险清单（items）。显式传入行数据即不读真实数据源（测试用）。"""
    asof = asof or datetime.now(BJ).date()
    conf = conf if conf is not None else load_conf()
    if not conf.get("enabled", True):
        return {}
    rows_u = _read_unlock_rows() if unlock_rows is None else unlock_rows
    rows_p = _read_pledge_rows() if pledge_rows is None else pledge_rows
    lines_n = _read_news_lines() if news_lines is None else news_lines
    return _merge(unlock_items(rows_u, asof, conf),
                  news_items(lines_n, asof, conf))


def refresh(path: Path | None = None, asof: date | None = None,
            unlock_rows: list | None = None, news_lines: list | None = None,
            pledge_rows: list | None = None) -> dict:
    """重建清单并原子落盘。返回 {"items": n, "warns": m}（供 cron 日志）。"""
    asof = asof or datetime.now(BJ).date()
    conf = load_conf()
    items = build(asof, conf, unlock_rows, news_lines, pledge_rows)
    rows_p = _read_pledge_rows() if pledge_rows is None else pledge_rows
    warns = pledge_warns(rows_p, conf) if conf.get("enabled", True) else {}
    doc = {"asof": asof.isoformat(),
           "generated_at": datetime.now(BJ).isoformat(),
           "items": items, "warns": warns}
    p = path or OUT
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return {"items": len(items), "warns": len(warns)}


def load_risk(path: Path | None = None, asof: date | None = None) -> dict:
    """读清单 {code6: {reason, kind, expire}}；缺失/损坏 → {}（fail-open）。

    读取时再按 expire 过滤一次：文件可能是几天前的，解禁日一过就不该继续拦。
    """
    try:
        doc = json.loads((path or OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, dict):
        return {}
    today = (asof or datetime.now(BJ).date()).isoformat()
    return {str(c): v for c, v in items.items()
            if isinstance(v, dict) and str(v.get("expire") or "") >= today}


def annotate(codes, path: Path | None = None) -> dict:
    """对给定代码清单打风险标注：{原样代码: 原因}（只含命中项）。供提示词/告警消费。"""
    risk = load_risk(path)
    out: dict = {}
    for c in codes or []:
        c6 = _code6(c)
        if c6 in risk:
            out[str(c)] = str(risk[c6].get("reason") or "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="事件风险清单：解禁/负面新闻 → 买入硬拦")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="重建 data/risk_block.json")
    sub.add_parser("show", help="打印当前清单")
    c = sub.add_parser("check", help="查指定代码是否在清单内")
    c.add_argument("codes", help="逗号分隔，带不带后缀均可")
    a = ap.parse_args()

    if a.cmd == "refresh":
        stats = refresh()
        print(f"items={stats['items']} warns={stats['warns']}")
        return 0
    if a.cmd == "show":
        risk = load_risk()
        for code, it in sorted(risk.items()):
            print(f"{code} [{it.get('kind')}] {it.get('reason')} (至 {it.get('expire')})")
        if not risk:
            print("（空清单）")
        return 0
    if a.cmd == "check":
        hit = annotate([x.strip() for x in a.codes.split(",") if x.strip()])
        for c_, r in hit.items():
            print(f"{c_}: {r}")
        if not hit:
            print("（均不在风险清单内）")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
