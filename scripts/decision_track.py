#!/usr/bin/env python3
"""决策远期记分卡 v1 —— 把每个 agent 的每条决策落池，滚动验证选股质量。

来源：对标 deepseek-harness-quant 的「五池远期验证」（entry_date + entry_close +
T+1/5/20/60 滚动收益 + 按决策者归因），适配我们的实盘链路（三 agent 分账竞争）。

解决的问题：排行榜只有净值，而净值把选股能力、仓位、择时、执行滑点、市场 beta
全混在一起——赢了不知道是选股赢还是运气赢；复盘产出的是叙事，叙事可以自我合理化。
本模块只回答一个问题：**当这个 agent 看多某只票时，这只票后来涨了吗？**

口径纪律（不可违反，对标回测验收标准）：
  1. 入场价 = **T+1 开盘价**（决策日 → 次日开盘），不用当日收盘（防前视）
  2. T+1 一字板（开=高=低=收）→ 标记 tradable=False，剔除出收益统计（买不进）
  3. 收益 = T+n 收盘 / T+1 开盘 − 1；超额 = 个股收益 − 全市场等权同期收益
  4. 样本 < MIN_SAMPLE 只展示不结论（借 Kelly 样本折扣纪律）

决策分三类（kind）：
  bullish   看多意向（buy；或对非持仓的 watch）→ 选股能力核心指标
  position  持仓管理（对已有持仓的 watch）      → 持仓管理质量
  sell      卖出决策                            → 收益取负，正数=卖出后下跌（卖对）

数据：
  池   logs/decision_pool.jsonl（append-only，id 去重）
  行情 quantdb daily_backward parquet（与 hypothesis_lab 同源，唯一读取口径）
  汇总 logs/decision_scorecard.json（供提示词注入 / 看板 / 复盘消费）

用法：
  python scripts/decision_track.py --update     # 每日滚动：回填入场价与远期收益
  python scripts/decision_track.py --report     # 出记分卡（并写 json）
  python scripts/decision_track.py --backfill   # 从历史成交/决策文件补录
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from trading_cal import next_trading_day  # noqa: E402

POOL = ROOT / "logs" / "decision_pool.jsonl"
SCORECARD = ROOT / "logs" / "decision_scorecard.json"
HORIZONS = (1, 5, 20, 60)
MIN_SAMPLE = 30          # 低于此样本量只展示不结论（Kelly 样本折扣纪律）
TAG_HIGH_LOOKBACK = 60   # 「接近60日高」回看窗口（受面板长度限制，不用 52 周口径）
PANEL_DAYS = 90          # 面板窗口（t60 需 ~60 交易日 + 缓冲）


# ---------------------------------------------------------------- 数据源

def _qdb_base() -> Path | None:
    """quantdb 日线目录（宿主 / 容器双路径）。"""
    for p in (Path.home() / "projects/quantmind/data/quantdb/1_kline_data/daily_backward",
              Path("/data/quantdb/1_kline_data/daily_backward")):
        if p.is_dir():
            return p
    return None


def _read_panel(days: int = PANEL_DAYS):
    """全市场日线面板（含基准计算所需的全样本）。数据源缺失 → 空表（不抛）。"""
    import duckdb
    import pandas as pd

    base = _qdb_base()
    if base is None:
        return pd.DataFrame()
    parts = sorted(glob.glob(f"{base}/dt=*"))[-days:]
    if not parts:
        return pd.DataFrame()
    files = [f"{p}/*.parquet" for p in parts]
    try:
        return duckdb.connect().execute(
            f"SELECT symbol, dt, open, close, high, low FROM read_parquet({files!r})").df()
    except Exception:  # noqa: BLE001  数据源异常不得让记分卡阻塞主流程
        return pd.DataFrame()


def _bars_by_symbol(panel) -> dict[str, list[tuple]]:
    """{symbol: [(dt, open, close, high, low), ...]} 按 dt 升序。"""
    if panel is None or panel.empty:
        return {}
    out: dict[str, list[tuple]] = {}
    for sym, g in panel.groupby("symbol", sort=False):
        g = g.sort_values("dt")
        out[sym] = list(zip(g["dt"].tolist(), g["open"].tolist(), g["close"].tolist(),
                            g["high"].tolist(), g["low"].tolist()))
    return out


def _market_returns(panel, entry_dts: set, horizons=HORIZONS) -> dict:
    """全市场等权收益基准 {(entry_dt, h): ret}——同日所有股票同窗口收益均值。
    衡量「随机选股」的基准线；不是指数（quantdb 无沪深300）。"""
    if panel is None or panel.empty or not entry_dts:
        return {}
    o = panel.pivot_table(index="dt", columns="symbol", values="open")
    c = panel.pivot_table(index="dt", columns="symbol", values="close")
    dts = list(o.index)
    pos = {d: i for i, d in enumerate(dts)}
    out = {}
    for ed in entry_dts:
        i = pos.get(ed)
        if i is None:
            continue
        for h in horizons:
            j = i + h - 1
            if j >= len(dts):
                continue
            r = (c.iloc[j] / o.iloc[i] - 1).replace([float("inf"), float("-inf")], None).dropna()
            if len(r):
                out[(int(ed), int(h))] = float(r.mean())
    return out


# ---------------------------------------------------------------- 池读写

def entry_dt_of(decision_date: str) -> int:
    """决策日 → 入场日（T+1 交易日）YYYYMMDD。"""
    return int(next_trading_day(date.fromisoformat(str(decision_date)[:10])).strftime("%Y%m%d"))


def make_id(agent: str, day: str, code: str, action: str) -> str:
    """同一 agent 同日同标的同动作只记一次（幂等，多轮分析不重复入池）。"""
    return hashlib.sha1(f"{agent}|{day}|{code}|{action}".encode()).hexdigest()[:16]


def load_pool(path: Path | None = None) -> list[dict]:
    path = path or POOL
    if not path.is_file():
        return []
    out = []
    try:
        # errors="replace"：决策池是含中文的追加 JSONL（_append 走 open("a")），
        # 截断在多字节字符中间会整文件抛 UnicodeDecodeError（不是 OSError）；
        # 逐行 handler 本就容错，替换成 U+FFFD 只损失撕裂那一行（2026-09-12 批 10）
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("id"):
                out.append(r)
    except OSError:
        return []
    return out


def append_rows(rows: list[dict], path: Path) -> None:
    """追加一批记录。**先整体序列化再开文件**：逐行 dumps 遇不可序列化值会
    "前几行已落盘、后面整批丢"（09-18 审查实测：score 塞 set → 文件空虚），
    而 pool_ctx 把调用方的池行值原样带进 JSON 后，这个面变宽了。

    公开的写入原语（2026-09-19）：决策池与影子账（ghost_ledger）共用同一份
    追加语义，**不各自实现**——"先序列化后写"这条纪律复制出去必丢。
    """
    if not rows:
        return                                  # 空批不落空行（各调用点仍有 if rows 守卫）
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(payload + "\n")


def rewrite_pool(pool: list[dict], path: Path) -> None:
    """原子重写（tmp + replace）：滚动更新回填远期收益。同样供影子账共用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in pool:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 入池

#: 池外标的的位置戳（决策 code 既不在展示集也不在剔除名单）。
#: 也是 pool_score_report 里 GROUP_OFF 的判据来源（state=="off"）。
ABSENT_POS = {"state": "off", "shown": False, "rank": None, "score": None, "fusion": None}


def pool_stamp(shown, dropped=None, full=None, pool_file: str = "") -> dict:
    """该 agent 本轮**实际看到的**候选池 → 位置戳（P2，2026-09-18）。

    返回 {"file", "n_shown", "n_dropped", "codes": {code: 位置}}；位置四键：
      state="shown"    在模型看到的表里（filter_affordable 之后）
      state="dropped"  池里有、被资金闸剔除（模型看不到；高分票常因贵先出局）
      state="off"      池外标的（持仓盯盘/新闻自选）
      rank/score/fusion 该行在池文件里的原始值（缺则 None）

    state 与 rank **分开记**（09-18 审查 MEDIUM）：池行可能没有 rank
    （select_from_reports 的 md 兜底分支就给所有行 rank=None），此时
    "被剔除"与"池外"在 rank 上不可区分——只看 rank 会把"资金约束买不了高分票"
    读成"模型无视分数"，方向完全相反。pool_stamp 本来就知道答案（只有出现在
    dropped 名单里才走被剔除那支），所以在这一层写死，不让读方去反推。

    整份空池（09-11/09-15 实录）是有效观测（n_shown=0），不是插桩缺失。
    """
    codes: dict[str, dict] = {}

    def _rows(x) -> list:
        """序列 → list；None/标量/字符串一律当空（降级而不是抛进主链路）。

        收 tuple/set（09-18 审查 HIGH）：只认 `list` 会把非 list 序列静默读成
        "空池"——而空池在本模块里是**合法观测**（n_shown=0），错读不留痕。
        """
        if isinstance(x, (list, tuple, set, frozenset)):
            return list(x)
        return []

    def _code(x) -> str:
        """键规范化：与 ingest 侧决策 code 同口径（两侧同一处 strip）。"""
        return str(x or "").strip()

    def _pos(row, state: str) -> dict:
        row = row if isinstance(row, dict) else {}
        return {"state": state, "shown": state == "shown", "rank": row.get("rank"),
                "score": row.get("score"), "fusion": row.get("fusion")}

    shown_rows = [r for r in _rows(shown) if isinstance(r, dict) and _code(r.get("code"))]
    for r in shown_rows:
        codes[_code(r["code"])] = _pos(r, "shown")
    index = {_code(r["code"]): r for r in _rows(full)
             if isinstance(r, dict) and _code(r.get("code"))}
    dropped_rows = []
    for item in _rows(dropped):
        if isinstance(item, dict):
            code = _code(item.get("code"))
        elif isinstance(item, (tuple, list)):
            code = _code(item[0] if item else "")
        else:
            code = _code(item)
        if not code:
            continue
        dropped_rows.append(code)
        codes.setdefault(code, _pos(index.get(code), "dropped"))
    return {"file": str(pool_file or ""), "n_shown": len(shown_rows),
            "n_dropped": len(dropped_rows), "codes": codes}


def _kind_of(action: str, code: str, held: set) -> str | None:
    """决策 → 记分卡分类；None=不跟踪（hold 无收益语义）。"""
    if action == "sell":
        return "sell"
    if action == "buy":
        return "bullish"
    if action == "watch":
        return "position" if code in held else "bullish"
    return None


def ingest_decisions(agent: str, decisions: list, ts: str, held_codes=None,
                     source: str = "live", path: Path | None = None,
                     pool_view: dict | None = None) -> int:
    """把一轮决策落池（幂等）。返回新增条数。

    只跟踪 buy/sell/watch——hold 是"不动作"，没有可验证的收益语义。
    held_codes：该 agent 当前持仓代码集合（区分看多意向 vs 持仓管理）。
    pool_view：本轮候选池视角 {"shown"/"dropped"/"full"/"file"}，见 pool_stamp。
      传了才写 pool_ctx（**整轮不传 = 不插桩**，旧调用方与回填路径因此不受影响）；
      戳表内容坏 → 按"池外"降级（见 pool_stamp 的 _rows），pool_stamp 自身抛异常
      → 整批**不写 pool_ctx**（与未插桩同形，报告会把它们计进"未插桩"）。
      两条降级路径都不让观测层带走整批决策入库。
    """
    path = path or POOL
    day = str(ts)[:10]
    existing = {e.get("id") for e in load_pool(path)}
    held = set(held_codes or ())
    stamp = None
    if pool_view:      # 空 dict 视同不插桩：写成"全员池外"戳会谎报覆盖率（审查 MEDIUM）
        try:
            stamp = pool_stamp((pool_view or {}).get("shown"),
                               (pool_view or {}).get("dropped"),
                               full=(pool_view or {}).get("full"),
                               pool_file=(pool_view or {}).get("file"))
        except Exception:  # noqa: BLE001  戳表坏 = 观测降级，不是决策失败
            stamp = None
    rows = []
    for d in decisions or []:
        if not isinstance(d, dict):
            continue
        action = str(d.get("action") or "").lower()
        code = str(d.get("code") or "").strip()
        if not code:
            continue
        kind = _kind_of(action, code, held)
        if kind is None:
            continue
        eid = make_id(agent, day, code, action)
        if eid in existing:
            continue
        row = {
            "id": eid, "agent": agent, "date": day, "ts": str(ts),
            "action": action, "kind": kind, "code": code,
            "name": str(d.get("name") or ""),
            "pct": d.get("pct"), "confidence": d.get("confidence"),
            "stop_loss": d.get("stop_loss"), "take_profit": d.get("take_profit"),
            "reason": str(d.get("reason") or "")[:200],
            "source": source, "entry_dt": entry_dt_of(day),
            "entry_px": None, "tradable": None, "tags": [], "fwd": {}, "updated": None,
        }
        if stamp is not None:
            try:
                row["pool_ctx"] = {
                    **{k: stamp[k] for k in ("file", "n_shown", "n_dropped")},
                    **stamp["codes"].get(code, ABSENT_POS),
                }
            except Exception:  # noqa: BLE001  戳表结构坏 → 该条不写戳，决策照常入库
                pass
        rows.append(row)
    if rows:
        append_rows(rows, path)
    return len(rows)


# ---------------------------------------------------------------- 滚动回填

def _forward_for(bars: list[tuple], entry_dt: int) -> dict:
    """入场日及远期收益。bars 升序；缺数据返回 {}。"""
    idx = next((i for i, b in enumerate(bars) if b[0] == entry_dt), None)
    if idx is None:
        return {}
    _dt, op, cl, hi, lo = bars[idx]
    entry_px = float(op or 0)
    if entry_px <= 0:
        return {}
    # 一字板买不进：全无振幅且收阳（开盘即封板，实际无法以开盘价成交）
    one_word = (hi and lo and (float(hi) - float(lo)) <= entry_px * 0.005
                and float(cl or 0) > entry_px * 1.045)
    fwd = {}
    for h in HORIZONS:
        j = idx + h - 1
        if j >= len(bars):
            fwd[f"t{h}"] = None
            continue
        c = float(bars[j][2] or 0)
        fwd[f"t{h}"] = round(c / entry_px - 1, 4) if c > 0 else None
    return {"entry_px": round(entry_px, 4), "tradable": not one_word, "fwd": fwd}


def _tags(bars: list[tuple], entry_dt: int) -> list[str]:
    """入场前的形态标签（确定性、无 LLM）：用于"你在什么模式下亏钱"的归因。"""
    idx = next((i for i, b in enumerate(bars) if b[0] == entry_dt), None)
    if idx is None or idx < 21:
        return []
    closes = [float(b[2] or 0) for b in bars[:idx] if b[2]]
    if len(closes) < 21:
        return []
    tags = []
    chg5 = closes[-1] / closes[-6] - 1 if closes[-6] > 0 else 0.0
    ma20 = sum(closes[-20:]) / 20
    hi_n = max(closes[-TAG_HIGH_LOOKBACK:])
    dist = closes[-1] / hi_n - 1 if hi_n > 0 else 0.0
    if chg5 > 0.08:
        tags.append("追高")
    elif chg5 < -0.08:
        tags.append("超跌")
    if dist > -0.05:
        tags.append("接近60日高")
    if closes[-1] > ma20:
        tags.append("站上MA20")
    else:
        tags.append("破MA20")
    return tags


def update_forward(path: Path | None = None, days: int = PANEL_DAYS,
                   today: str | None = None) -> dict:
    """滚动回填：入场价 / 远期收益 / 超额 / 标签。返回 {updated, n}。"""
    path = path or POOL
    pool = load_pool(path)
    if not pool:
        return {"updated": 0, "n": 0}
    panel = _read_panel(days)
    bars = _bars_by_symbol(panel)
    mkt = _market_returns(panel, {int(e.get("entry_dt") or 0) for e in pool})
    today = today or date.today().isoformat()
    n_upd = 0
    for e in pool:
        b = bars.get(e.get("code"))
        if not b:
            continue
        r = _forward_for(b, int(e.get("entry_dt") or 0))
        if not r:
            continue
        e["entry_px"], e["tradable"] = r["entry_px"], r["tradable"]
        fwd = {}
        for h in HORIZONS:
            ret = r["fwd"].get(f"t{h}")
            if ret is None:
                fwd[f"t{h}"] = None
                continue
            m = mkt.get((int(e["entry_dt"]), h))
            fwd[f"t{h}"] = {"ret": ret,
                            "bench": round(m, 4) if m is not None else None,
                            "excess": round(ret - m, 4) if m is not None else None}
        e["fwd"] = fwd
        e["tags"] = _tags(b, int(e["entry_dt"]))
        e["updated"] = today
        n_upd += 1
    rewrite_pool(pool, path)
    return {"updated": n_upd, "n": len(pool)}


# ---------------------------------------------------------------- 汇总

def _stat(entries: list[dict], h: int, invert: bool = False) -> dict:
    """单组单周期统计。invert=True 用于卖出决策（收益取负：卖出后跌=对）。"""
    rets, exs = [], []
    for e in entries:
        f = (e.get("fwd") or {}).get(f"t{h}")
        if not f or f.get("ret") is None:
            continue
        rets.append(-f["ret"] if invert else f["ret"])
        if f.get("excess") is not None:
            exs.append(-f["excess"] if invert else f["excess"])
    if not rets:
        return {"n": 0, "win_rate": None, "avg_ret": None, "avg_excess": None}
    return {"n": len(rets),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 3),
            "avg_ret": round(sum(rets) / len(rets), 4),
            "avg_excess": round(sum(exs) / len(exs), 4) if exs else None}


def build_scorecard(pool: list[dict], today: str | None = None) -> dict:
    """按 agent × 决策类别 × 周期汇总。sell 类别收益取负。"""
    out = {"generated_at": today or date.today().isoformat(),
           "n_entries": len(pool), "min_sample": MIN_SAMPLE,
           "bench": "全市场等权（同窗口 open→close 均值）",
           "entry_rule": "T+1 开盘价；一字板剔除",
           "agents": {}}
    agents = sorted({e.get("agent") for e in pool if e.get("agent")})
    for agent in agents:
        mine = [e for e in pool if e.get("agent") == agent]
        blk: dict = {"n_total": len(mine)}
        for kind in ("bullish", "position", "sell"):
            sub = [e for e in mine if e.get("kind") == kind]
            if kind == "bullish":
                sub = [e for e in sub if e.get("tradable") is not False]  # 一字板买不进，剔除
            if not sub:
                continue
            blk[kind] = {"n": len(sub),
                         **{f"t{h}": _stat(sub, h, invert=(kind == "sell"))
                            for h in HORIZONS}}
            if kind == "bullish":
                by_tag: dict = {}
                for tag in sorted({t for e in sub for t in (e.get("tags") or [])}):
                    sel = [e for e in sub if tag in (e.get("tags") or [])]
                    if len(sel) >= 5:
                        by_tag[tag] = {"n": len(sel), **_stat(sel, 5)}
                if by_tag:
                    blk["bullish"]["by_tag_t5"] = by_tag
        if len(blk) > 1:
            out["agents"][agent] = blk
    return out


def write_scorecard(path: Path | None = None, pool_path: Path | None = None,
                    today: str | None = None) -> dict:
    sc = build_scorecard(load_pool(pool_path), today)
    p = path or SCORECARD
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return sc


# ---------------------------------------------------------------- 补录

def _trading_agents() -> set:
    """分账实盘 agent 名单（从 live_equity.jsonl 反推，避免硬编码模型名）。"""
    agents: set = set()
    try:
        for line in (ROOT / "logs" / "live_equity.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("agent"):
                agents.add(str(r["agent"]))
    except OSError:
        pass
    return agents


def _row(agent: str, day: str, ts: str, d: dict, source: str) -> dict:
    action = str(d.get("action") or "").lower()
    code = str(d.get("code") or "").strip()
    return {"id": make_id(agent, day, code, action), "agent": agent, "date": day, "ts": ts,
            "action": action, "kind": _kind_of(action, code, set()), "code": code,
            "name": str(d.get("name") or ""), "pct": d.get("pct"),
            "confidence": d.get("confidence"), "stop_loss": d.get("stop_loss"),
            "take_profit": d.get("take_profit"),
            "reason": str(d.get("reason") or "")[:200], "source": source,
            "entry_dt": entry_dt_of(day), "entry_px": None, "tradable": None,
            "tags": [], "fwd": {}, "updated": None}


def backfill_from_agent_logs(path: Path | None = None) -> dict:
    """从 data/agent_data_astock/{agent}/log/{date}/log.jsonl 解析历史决策。

    这是**决策的原始出处**（模型当轮完整输出），比成交流水覆盖广得多——
    成交只是决策的子集（多数决策是 watch/条件单，未必成交）。
    """
    from live_prompt_context import parse_intraday_decision

    path = path or POOL
    existing = {e.get("id") for e in load_pool(path)}
    trading = _trading_agents()
    rows: list[dict] = []
    for f in sorted((ROOT / "data" / "agent_data_astock").glob("*/log/*/log.jsonl")):
        agent = f.parents[2].name  # data/agent_data_astock/{agent}/log/{date}/log.jsonl
        if trading and agent not in trading:
            continue
        day = f.parent.name
        try:
            # errors="replace"：模型对话流是含中文的追加日志，截断在多字节字符中间会
            # 整文件抛 UnicodeDecodeError（不是 OSError）→ 该日历史决策补录整段丢（批 10）
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(rec.get("timestamp") or "")
            for m in rec.get("new_messages") or []:
                if not isinstance(m, dict) or m.get("role") != "assistant":
                    continue
                content = m.get("content")
                if not isinstance(content, str):
                    continue
                for d in parse_intraday_decision(content) or []:
                    code = str(d.get("code") or "").strip()
                    if not code:
                        continue
                    action = str(d.get("action") or "").lower()
                    if _kind_of(action, code, set()) is None:
                        continue
                    eid = make_id(agent, day, code, action)
                    if eid in existing:
                        continue
                    existing.add(eid)
                    rows.append(_row(agent, day, ts, d, "backfill_agent_log"))
    if rows:
        append_rows(rows, path)
    return {"backfilled_agent_logs": len(rows)}


def backfill_from_logs(path: Path | None = None) -> dict:
    """从 data/agent_last_decisions.json + logs/live_trade_*.jsonl 补录历史决策。

    live_trade 是**实际成交**（有真实成交价与量）——与决策记分卡的 T+1 开盘口径
    不同，这里只补录其中"该 agent 确实下过单"的标的动作，入场价仍按 T+1 开盘
    统一计算（保持口径一致：衡量决策质量，不衡量执行滑点）。
    """
    path = path or POOL
    existing = {e.get("id") for e in load_pool(path)}
    rows: list[dict] = []

    # 1) 各 agent 最近一轮决策
    try:
        last = json.loads((ROOT / "data" / "agent_last_decisions.json").read_text(encoding="utf-8"))
        for agent, blk in (last or {}).items():
            if not isinstance(blk, dict):
                continue
            ts = str(blk.get("ts") or "")
            if len(ts) < 10:
                continue
            for d in blk.get("decisions") or []:
                if not isinstance(d, dict):
                    continue
                action = str(d.get("action") or "").lower()
                code = str(d.get("code") or "").strip()
                kind = _kind_of(action, code, set())
                if kind is None or not code:
                    continue
                eid = make_id(agent, ts[:10], code, action)
                if eid in existing:
                    continue
                existing.add(eid)
                rows.append({"id": eid, "agent": agent, "date": ts[:10], "ts": ts,
                             "action": action, "kind": kind, "code": code,
                             "name": str(d.get("name") or ""), "pct": d.get("pct"),
                             "confidence": d.get("confidence"),
                             "stop_loss": d.get("stop_loss"),
                             "take_profit": d.get("take_profit"),
                             "reason": str(d.get("reason") or "")[:200],
                             "source": "backfill_last_decisions",
                             "entry_dt": entry_dt_of(ts[:10]),
                             "entry_px": None, "tradable": None, "tags": [],
                             "fwd": {}, "updated": None})
    except (OSError, ValueError):
        pass

    # 2) 历史成交流水（买入=看多意向，卖出=卖出决策）
    for f in sorted((ROOT / "logs").glob("live_trade_*.jsonl")):
        if any(m in f.name for m in ("_us_", "_hk_")):
            continue
        try:
            # errors="replace"：含中文追加流水可能截断在多字节字符中间，严格解码会
            # 整文件抛 UnicodeDecodeError（不是 OSError）→ 历史补录整段丢（批 10）
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("error") or not r.get("side") or not r.get("code"):
                continue
            agent, day = r.get("agent"), str(r.get("ts") or "")[:10]
            if not agent or len(day) < 10:
                continue
            action = "buy" if str(r["side"]).lower() == "buy" else "sell"
            eid = make_id(agent, day, r["code"], action)
            if eid in existing:
                continue
            existing.add(eid)
            rows.append({"id": eid, "agent": agent, "date": day, "ts": str(r.get("ts") or ""),
                         "action": action, "kind": _kind_of(action, r["code"], set()),
                         "code": r["code"], "name": "", "pct": None, "confidence": None,
                         "stop_loss": None, "take_profit": None,
                         "reason": "（历史成交流水补录）",
                         "source": "backfill_trade_log", "entry_dt": entry_dt_of(day),
                         "entry_px": None, "tradable": None, "tags": [],
                         "fwd": {}, "updated": None})
    if rows:
        append_rows(rows, path)
    return {"backfilled": len(rows)}


# ---------------------------------------------------------------- 报告

def _fmt_stat(s: dict) -> str:
    if not s.get("n"):
        return "n=0"
    exc = f" · 超额 {s['avg_excess']:+.1%}" if s.get("avg_excess") is not None else ""
    return f"n={s['n']} · 胜率 {s['win_rate']:.0%} · 均收益 {s['avg_ret']:+.1%}{exc}"


def print_report(sc: dict) -> None:
    print(f"决策远期记分卡 · {sc['generated_at']} · 池内 {sc['n_entries']} 条")
    print(f"口径：{sc['entry_rule']}；基准：{sc['bench']}")
    for agent, blk in (sc.get("agents") or {}).items():
        print(f"\n■ {agent}（总决策 {blk['n_total']}）")
        for kind, label in (("bullish", "看多意向（选股能力）"),
                            ("position", "持仓管理"), ("sell", "卖出决策（正数=卖后跌）")):
            sub = blk.get(kind)
            if not sub:
                continue
            print(f"  {label}：")
            for h in HORIZONS:
                print(f"    T+{h:<3} {_fmt_stat(sub.get(f't{h}') or {})}")
            for tag, ts_ in (sub.get("by_tag_t5") or {}).items():
                print(f"    [T+5·{tag}] {_fmt_stat(ts_)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="滚动回填入场价与远期收益")
    ap.add_argument("--report", action="store_true", help="出记分卡")
    ap.add_argument("--backfill", action="store_true", help="从历史成交/决策文件补录")
    ap.add_argument("--days", type=int, default=PANEL_DAYS)
    args = ap.parse_args()
    if args.backfill:
        print("补录:", backfill_from_agent_logs())
        print("补录:", backfill_from_logs())
    if args.update or not (args.report or args.backfill):
        print("滚动回填:", update_forward(days=args.days))
    if args.report or not (args.update or args.backfill):
        sc = write_scorecard()
        print_report(sc)
        print(f"\n→ {SCORECARD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
