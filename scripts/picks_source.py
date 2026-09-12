#!/usr/bin/env python3
"""候选池取用（唯一入口，2026-09-12 P1-3）。

池文件两类（同一目录 quantmind `data/reports/stock_picks/`）：
  {YYYYMMDD}_picks.json        quantmind 盘后池，**按数据会话日命名**（00:35 产出）
  {YYYYMMDD}_agent_picks.json  晚间研究池（night_pool_agent），P1-4 起**按目标交易日命名**

背景：选择逻辑原先散在两处（`select_from_reports.latest_picks_file` 与
`live_llm_trade` 里取 market_direction 的 glob），都是 `sorted(glob(...))[-1]`
裸取字典序最后——同日并列谁优先未定义、最新一期整份缺失无人知晓、池子多旧
没有任何消费方知道。2026-09-11 实录：晚间研究池 09-10 20:30 就产出了
（服务 09-11 早盘），但并排的 quantmind 池字典序更大 → 晚间池从未被实盘消费。

本模块按文件名日期取最新（同日 agent 优先），返回日期与来源，并提供新鲜度说明
与消费元数据（提示词 / 日志 / 事件共用同一口径）。
"""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PICKS_DIR = ROOT.parent / "projects/quantmind/data/reports/stock_picks"

KIND_AGENT = "agent"          # {date}_agent_picks.json（晚间研究，按目标交易日命名）
KIND_QUANTMIND = "quantmind"  # {date}_picks.json（盘后池，按数据会话日命名）
_KIND_RANK = {KIND_QUANTMIND: 0, KIND_AGENT: 1}   # 同日并列 → agent 优先

_NAME_RE = re.compile(r"^(\d{8})(_agent)?_picks\.json$")

MISSING_NOTE = ("⚠️ 候选池文件缺失（stock_picks 目录无任何 *_picks.json）"
                "——本轮无池可用，新开仓/换仓受限")


def parse_picks_name(name: str) -> tuple[str, str] | None:
    """文件名 → (date8, kind)；非池文件/坏日期 → None。"""
    m = _NAME_RE.match(str(name))
    if not m:
        return None
    d8 = m.group(1)
    try:
        date(int(d8[:4]), int(d8[4:6]), int(d8[6:]))
    except ValueError:
        return None
    return d8, (KIND_AGENT if m.group(2) else KIND_QUANTMIND)


def list_picks(base: Path | None = None) -> list[tuple[str, str, Path]]:
    """目录内全部池文件 → [(date8, kind, path)]，按 (日期, 来源优先级) 升序。"""
    out = []
    for p in (base or PICKS_DIR).glob("*_picks.json"):
        parsed = parse_picks_name(p.name)
        if parsed:
            out.append((parsed[0], parsed[1], p))
    out.sort(key=lambda t: (t[0], _KIND_RANK.get(t[1], 0)))
    return out


def _dash(d8: str) -> str:
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"


def latest_picks(base: Path | None = None) -> tuple[Path, str, dict | None, str] | None:
    """最新一期 → (path, date8, doc, kind)；目录里没有任何池文件 → None。

    doc = None 表示最新那份**读不出/解析失败**——不静默回退旧池（那正是
    「静默取旧」的事故形态），由调用方按 meta 的 unreadable 分支告警。
    """
    files = list_picks(base)
    if not files:
        return None
    d8, kind, path = files[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        doc = None
    return path, d8, doc, kind


def expected_label(kind: str, today: date | None = None) -> str:
    """该类型池文件「应当」的日期标签（YYYYMMDD）。

    - agent 池按目标交易日命名 → 今天（交易日）或下一交易日（休市日）；
    - quantmind 池按数据会话日命名 → 最近已收盘交易日（凌晨/盘前回看前一交易日）。
    """
    import trading_cal as tc

    d = today or date.today()
    if kind == KIND_AGENT:
        return d.strftime("%Y%m%d") if tc.is_trading_day(d) else \
            tc.next_trading_day(d).strftime("%Y%m%d")
    return tc.prev_trading_day(d, inclusive=not tc.is_trading_day(d)).strftime("%Y%m%d")


def freshness_note(date8: str, kind: str, today: date | None = None) -> str:
    """池日期相对应服务日期 → 滞后说明（空串 = 新鲜/提前产出，不打扰）。"""
    try:
        exp = expected_label(kind, today)
        if not date8 or date8 >= exp:
            return ""
        lag = (date.fromisoformat(_dash(exp)) - date.fromisoformat(_dash(date8))).days
    except ValueError:
        return ""
    return (f"⚠️ 候选池非当日：{date8} 的池（应服务 {exp}），滞后 {lag} 个自然日"
            "——池内标的与价格可能已过期，新开仓谨慎")


def pool_meta(info: tuple | None, today: date | None = None) -> dict:
    """latest_picks() 结果（或 None）→ 消费元数据（提示词 / 日志 / 事件共用）。

    键：file/date/kind/stale_days/note/missing/unreadable/empty。
    missing=整份缺失；unreadable=最新一期解析失败；empty=文件在但池空
    （**设计内**：复盘判空仓时 quantmind 会给 candidates=[] + gate 原因）。
    """
    if not info:
        return {"file": "", "date": "", "kind": "", "stale_days": 0,
                "note": MISSING_NOTE, "missing": True, "unreadable": False, "empty": True}
    path, d8, doc, kind = info
    meta = {"file": path.name, "date": d8, "kind": kind, "stale_days": 0,
            "note": freshness_note(d8, kind, today), "missing": False,
            "unreadable": False, "empty": False}
    if doc is None:
        return {**meta, "unreadable": True, "empty": True,
                "note": f"⚠️ 候选池文件无法解析：{path.name}——本轮无池可用"}
    try:
        exp = expected_label(kind, today)
        if exp and d8 < exp:
            meta["stale_days"] = (date.fromisoformat(_dash(exp))
                                  - date.fromisoformat(_dash(d8))).days
    except ValueError:
        pass
    rows = doc.get("candidates")
    if not isinstance(rows, list):
        rows = doc.get("picks")
    if not isinstance(rows, list) or not rows:
        meta["empty"] = True
        gate = str(doc.get("gate") or "").strip()
        reason = f"（{gate[:80]}）" if gate else ""
        meta["note"] = (f"候选池为空：{path.name} 判定池内无标的{reason}"
                        "——属设计内（复盘/研究判空），不是数据缺失")
    return meta


def prompt_line(meta: dict) -> str:
    """池来源一行（提示词首部 / log.jsonl）：用的是哪一天哪类池 + 新鲜度说明。

    实盘提示词必须写明池来源：09-11 复盘时无法从对话流判断某一轮吃的是哪份池；
    滞后说明随行进提示词，模型据此对"池内标的与价格可能已过期"保持谨慎。
    """
    if meta.get("missing") or meta.get("unreadable"):
        return f"📌【候选池来源】{meta.get('note') or MISSING_NOTE}"
    kind_txt = "晚间研究池" if meta.get("kind") == KIND_AGENT else "盘后研究池"
    line = (f"📌【候选池来源】{meta.get('file')}（{kind_txt}，池日期 {meta.get('date')}）")
    if meta.get("note"):
        line += f"｜{meta['note']}"
    return line


def note_missing_pool(msg: str | None = None) -> None:
    """池整份缺失/最新一期读不出（= 本轮无池可用）→ 事件，alert，按自然日去重。

    实盘取池路径（09:35 调仓 / 整点轮）调用；空池（文件在、判空）不算缺失，
    不告警（属设计内，见 pool_meta）。
    """
    from live_fills import now_cn, record_event

    record_event("pool_missing", "", msg or MISSING_NOTE,
                 key=f"pool_missing:{now_cn():%Y-%m-%d}", alert=True)
