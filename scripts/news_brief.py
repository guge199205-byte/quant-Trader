#!/usr/bin/env python3
"""新闻 Agent 管线（A股实盘，全 deepseek-v4-flash）：RSS 新闻 → 金融相关筛选
→ 大方向/小方向/持仓情报 → 主编「新闻分子」简报。

数据源：quantmind 后端聚合 API（Huntly RSS + enrichment 标签：tickers/industries/
event_tags/sentiment，正文级规则抽取）。本管线把标签当证据喂各段，不重复读正文。
源分层依据：2026-09-07 一周 19,238 篇实证（详见模块内分层常量注释）。

五段流水线（每段独立 try/超时，失败跳段沿用上一版，绝不空手）：
  ① news-gate     新闻门卫：标题+标签证据 → 金融相关? 粗分 macro/micro/holdings
  ② news-macro    宏观政策研究（大方向）：宏观/政策/监管/外围 → 大盘方向分+主线+风险
  ③ news-micro    板块个股情报（小方向）：行业/概念/公司事件 → 热点主题+事件清单
  ④ news-holdings 持仓情报（重点）：只吃命中持仓/关注池的条目 → 利好/利空/幅度/时效
  ⑤ news-chief    新闻主编：②③④ → 「新闻分子」latest.json（交易/市场研究唯一入口）

窗口语义（用户口径）：不重复历史——每轮只分析「上次成功分析结束 → 现在」的新增；
游标存 data/news_brief/state.json；超过 MAX_WINDOW_HOURS 自动截断。

触发 cron（北京时刻 → JST+1h 写）：
  09:25 盘前(隔夜+早间公告)    25 10 * * 1-5
  09:55/10:55/12:55/13:55 盘中 55 10,11,13,14 * * 1-5   # 整点交易分析前 5 分钟
  19:05 收盘当日全景           5 20 * * 1-5  # 夜池 19:30 消费 fresh 分子
手动：python scripts/news_brief.py [--since 'YYYY-MM-DD HH:MM'] [--stage ...] [--force]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from news_digest import FIN_KEYWORDS, GLOBAL_KW, NOISY_SOURCES, _norm_key, _to_bj  # noqa: E402
from trading_cal import is_trading_day  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))
MODEL = "deepseek-v4-flash"
API = "http://127.0.0.1:8000/api/v1/news/articles"
FETCH_FOLDERS = list(range(1, 10))     # Huntly 订阅桶（源分层在本地做）
MAX_WINDOW_HOURS = 72                  # 增量兜底上限（覆盖周五晚→周一早盘前）
MAX_TITLES = 400                       # 单轮进门卫上限（按新鲜度截断）
MAX_PAGES_PER_FOLDER = 4               # 单桶翻页上限（防深偏移 502）

STATE_FILE = ROOT / "data" / "news_brief" / "state.json"
BRIEF_FILE = ROOT / "data" / "news_brief" / "latest.json"
HISTORY_FILE = ROOT / "data" / "news_brief" / "history.jsonl"
WATCH_FILE = ROOT / "data" / "news_brief" / "watch_codes.json"
LESSONS_FILE = ROOT / "data" / "news_brief" / "lessons.json"  # 晚间复盘经验（news_review 维护）
INTRA_WATCH_FILE = ROOT / "data" / "news_brief" / "intraday_watch.json"  # 盘中异动关注（L2 轮询消费）
INTRA_WATCH_TTL_MIN = 60   # 异动关注冷却：60 分钟后过期
INTRA_WATCH_MAX = 20       # 上限（新优先）

# signature = 落盘目录名（data/agent_data_astock/{sig}/log/... → 前端直接复用）
GATE, MACRO, MICRO, HOLD, CHIEF = ("news-gate", "news-macro", "news-micro",
                                   "news-holdings", "news-chief")
AGENT_CN = {GATE: "新闻门卫", MACRO: "宏观政策研究", MICRO: "板块个股情报",
            HOLD: "持仓情报", CHIEF: "新闻主编"}
ALL_STAGES = (GATE, MACRO, MICRO, HOLD, CHIEF)

# ---- 源分层（2026-09-07 一周 19,238 篇实证） ----
# S=核心快讯源（快讯准/富化率高，全量进门卫） A=高量需门卫去噪 B=外围/未知 X=实证排除
_TIER_S = ("财联社", "格隆汇", "华尔街见闻", "智通财经", "财新", "金十",
           "⚡️ 7x24投资快讯", "同花顺", "万得", "雪球", "界面", "富途", "36氪")
_TIER_A = ("7x24", "实时快讯", "看盘", "电报", "时讯", "Telegram Channel")
_TIER_X = ("World - Latest - Google News", "新浪财经－国内滚动", "澎湃新闻 - 首页头条",
           "中国话题", "国内滚动", "全球新闻", "中央社",
           # Crypto/Web3（与 A股 主线无关；要独立通道再开）
           "Crypto", "CoinDesk", "Cointelegraph", "链捕手", "TechFlow", "登链",
           "Web3", "吴说", "白话区块链", "虚拟货币", "敏感经济信息")
_GOOGLE_ALERT_OK = ("证监会", "汇率", "商务部", "海关总署", "央行", "发改委", "工信部")
_FIN_RE = re.compile(FIN_KEYWORDS)

_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "X": 3}


def source_tier(src: str) -> str:
    if any(k in src for k in _TIER_X):
        return "X"
    if "Google" in src or "Alert" in src:
        return "B" if any(k in src for k in _GOOGLE_ALERT_OK) else "X"
    if any(k in src for k in _TIER_S):
        return "S"
    if any(k in src for k in _TIER_A):
        return "A"
    return "B"


def pick_relevant(art: dict) -> bool:
    """金融相关规则减载（进门卫前挡 ~85% 噪声，各层统一）：
    正文级标签证据（tickers/industries/event_tags/key_terms）或标题财经词
    命中即保留；排除桶（社会/crypto）必须标题财经词佐证（防"路况新闻带公司
    代码"误伤）。判定权仍在门卫 agent——这里只做量级减载。"""
    src = str(art.get("source_name") or "")
    title = str(art.get("title") or "")
    tier = source_tier(src)
    if tier == "X":
        return bool(_FIN_RE.search(title) and not any(n in src for n in NOISY_SOURCES))
    enr = art.get("enrichment") or {}
    if enr.get("tickers") or enr.get("industries") or enr.get("event_tags") or enr.get("key_terms"):
        return True
    return bool(_FIN_RE.search(title))


def dedup_articles(arts: list) -> list:
    """事件聚类去重（news_digest 同口径）：同标的同事件多源转发 → 一条。
    优先保留带 ticker 证据的变体；其余稳定保留首条。"""
    seen, out = set(), []
    for a in sorted(arts, key=lambda x: -int(bool((x.get("enrichment") or {}).get("tickers")))):
        k = _norm_key(a)
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


def split_holdings_related(arts: list, watch_codes: set) -> tuple[list, list]:
    """规则保底分流：enrichment.tickers 命中 watch → holdings；其余 → 其他。"""
    h, other = [], []
    for a in arts:
        tk = set((a.get("enrichment") or {}).get("tickers") or [])
        (h if tk & watch_codes else other).append(a)
    return h, other


_GLOBAL_RE = re.compile("|".join(GLOBAL_KW))


def rule_direction(art: dict, watch_codes: set) -> str:
    """门卫降级时的规则方向：命中关注代码 → holdings；外围/宏观词 → macro；
    其余 → micro。只用于门卫 LLM 失败时的兜底（宁粗勿丢）。"""
    tk = set((art.get("enrichment") or {}).get("tickers") or [])
    if tk & watch_codes:
        return "holdings"
    title = str(art.get("title") or "")
    if _GLOBAL_RE.search(title):
        return "macro"
    return "micro"


# ---------- 时间与状态 ----------

def _iso(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.replace(tzinfo=CN_TZ)
    except (ValueError, TypeError):
        return None


def _bj_fmt(d: datetime | None, fmt: str = "%m-%d %H:%M") -> str:
    return d.astimezone(CN_TZ).strftime(fmt) if d else ""


def load_state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(st: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name("state.json.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def window_for(last_end: str, now_iso: str) -> tuple[str, str]:
    """增量窗口 (start,end)：从上次成功分析结束起（不重复历史），
    超 MAX_WINDOW_HOURS 自动截断（首跑/长假防爆量）。"""
    now = _iso(now_iso) or datetime.now(CN_TZ)
    start = _iso(last_end) if last_end else None
    if start is None or start >= now:
        start = now - timedelta(hours=MAX_WINDOW_HOURS)
    if (now - start).total_seconds() > MAX_WINDOW_HOURS * 3600:
        start = now - timedelta(hours=MAX_WINDOW_HOURS)
    return start.isoformat(), now.isoformat()


# ---------- 拉取 ----------

def _get_json(url: str, timeout: int = 25) -> dict:
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _utc(iso_s: str) -> str:
    d = _iso(iso_s) or datetime.now(CN_TZ)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_incr(since_iso: str, until_iso: str) -> tuple[list, list]:
    """逐桶增量拉取（time_desc 分页，翻到早于 since 即停，单桶 ≤4 页）。
    返回 (articles 新→旧, [(folder, err)])；单桶失败跳过不阻塞（缺口记账）。"""
    since_dt = _iso(since_iso)
    until_u, out, fails = _utc(until_iso), [], []
    for fid in FETCH_FOLDERS:
        try:
            page, done = 1, False
            while page <= MAX_PAGES_PER_FOLDER and not done:
                q = f"since=2000-01-01T00:00:00Z&until={until_u}&folder_id={fid}" \
                    f"&page={page}&page_size=500&sort=time_desc"
                d = _get_json(f"{API}?{q}")
                arts = d.get("articles") or []
                for a in arts:
                    ts = _iso(str(a.get("published_at") or "").replace("Z", "+00:00"))
                    if ts and since_dt and ts < since_dt:
                        done = True
                        break
                    out.append(a)
                if len(arts) < 500:
                    done = True
                page += 1
        except Exception as exc:  # noqa: BLE001
            fails.append((fid, str(exc)[:100]))
    seen, uniq = set(), []
    for a in out:
        if a.get("id") in seen:
            continue
        seen.add(a["id"])
        uniq.append(a)
    return uniq, fails


# ---------- LLM 各段 ----------

def _fmt_titles(arts: list) -> str:
    lines = []
    for i, a in enumerate(arts):
        enr = a.get("enrichment") or {}
        ev = (" 标签[" + ",".join((enr.get("event_tags") or [])[:3]) + "]") if enr.get("event_tags") else ""
        tk = (" 代码[" + ",".join((enr.get("tickers") or [])[:4]) + "]") if enr.get("tickers") else ""
        ind = (" 行业[" + ",".join((enr.get("industries") or [])[:2]) + "]") if enr.get("industries") else ""
        se = (enr.get("sentiment_label") or "").lower() or ""
        st = {"bullish": " 情绪[多]", "bearish": " 情绪[空]"}.get(se, "")
        src = str(a.get("source_name") or "?")
        lines.append(
            f"{i}. [{_to_bj(str(a.get('published_at') or ''))[5:]}][{source_tier(src)}]"
            f"{src[:20]} {str(a.get('title') or '')[:104]}{tk}{ind}{ev}{st}")
    return "\n".join(lines)


def _user_block(title: str, arts: list) -> str:
    return f"{title}（{len(arts)} 条，新→旧）\n" + _fmt_titles(arts)


def _system_for(stage: str) -> str:
    if stage == GATE:
        return ("你是 A股实盘『新闻门卫』：从批量标题中挑出对 A股（含港股联动/外围传导）"
                "可能有影响的条目，剔除无关噪音（社会/体育/娱乐/纯海外与他国市场/广告）。"
                "每保留条标注方向：macro=宏观/政策/货币/监管/外围(美债/汇率/地缘/大宗)/大盘；"
                "micro=行业板块/概念/公司公告/业绩/盘中异动；holdings=直接命中给定关注代码"
                "或这些标的所属板块的强相关条目。输入带 序号/来源/时间/标签证据(正文级)。"
                "宁全勿漏：不确定就保留，方向二选一取更可能。"
                "输出：每行一个保留项 `序号 方向`，如 `12 macro`、`33 micro`、"
                "`7 holdings`；只输出这些行。")
    if stage == MACRO:
        return ("你是『宏观政策研究』（大方向，只分析不下单）：输入为门卫筛出的宏观/政策/"
                "监管/外围标题。输出对 A股 大盘/风格/利率的影响判断。只引用输入事实，"
                "不臆造数据。行式输出（每行一条，字段用 | 分隔，严禁其他文字）：\n"
                "V | 观点一句话 | bias(-1..1)\n"
                "D | 主线 | 对A股传导（可多行）\n"
                "R | 风险 | severity(1..3)（可多行）\n"
                "C | confidence(0..1)\n"
                "示例：V | 中性偏多，政策托底 | 0.2\nD | 3000亿特别国债 | 利好金融权重")
    if stage == MICRO:
        return ("你是『板块个股情报』（小方向，只分析不下单）：输入为门卫筛出的行业/板块/"
                "公司/业绩/异动标题。归纳热点主题与公司事件（精选有影响的，禁止逐条复述）。"
                "股票代码只许来自输入证据，禁止臆造代码。行式输出（每行一条，字段用 | "
                "分隔，严禁其他文字）：\n"
                "T | 板块/主题 | 驱动逻辑 | 强度(1..3) | 催化（可多行）\n"
                "E | 代码(逗号分隔) | 公司 | 事件类型 | sentiment(-1..1) | 备注（可多行）\n"
                "O | 轮动观察一句话\nC | confidence(0..1)\n"
                "示例：T | 存储芯片 | 涨价+需求复苏 | 2 | 三星涨价\n"
                "E | 688123.SH | 聚辰股份 | 涨停异动 | 0.8 | 板块带动")
    if stage == HOLD:
        return ("你是『持仓情报』（重点岗，只分析不下单）：输入为命中实盘持仓/关注池的新闻。"
                "逐条判断对股价影响。行式输出（每行一条，字段用 | 分隔，严禁其他文字）：\n"
                "H | 代码 | 名称 | verdict(利好/利空/中性) | impact(-2..2) | "
                "事件类型+一句话依据 | 来源（可多行）\n"
                "X | 代码 | 跨持仓联动风险（可多行）\n"
                "A | 动作提示(观察/挂条件/警示)（可多行）\nC | confidence(0..1)\n"
                "示例：H | 600309.SH | 万华化学 | 利好 | 1 | 涨价：MDI挂牌价上调 | 财联社\n"
                "宁精勿滥：泛泛而谈标中性+impact0。只输出这些行。")
    return ("你是『新闻主编』：汇总本轮宏观/板块/持仓分析，产出面向交易的『新闻分子』。"
            "宏观已在输入给出不用重述；你负责：主题≤4、watch≤5（给代码+触发条件）、"
            "开放风险≤3、持仓逐票保真转写（≤8，只转写输入出现的，不加新内容）、"
            "给市场研究的参考、一句话编辑备注。行式输出（每行一条，字段用 | 分隔，"
            "严禁其他文字）：\n"
            "T | 主题 | 驱动逻辑（可多行）\n"
            "W | 代码 | 名称 | 逻辑 | 触发条件（可多行）\n"
            "R | 开放风险（可多行）\n"
            "H | 代码 | 名称 | verdict(利好/利空/中性) | impact(-2..2) | horizon | "
            "event_type | headline(截40字) | source | note（≤8行）\n"
            "N | 给市场研究的参考\nE | 编辑备注一句话\n"
            "C | confidence(0..1)（示例：C | 0.75）")


def _fix_leading_zero_ints(t: str) -> str:
    """模型会照抄输入里的补零序号（如 000）——JSON 规范不允许前导零：
    冒号/逗号/数组边界后的 -?0+数字 → 去前导零（保留符号与前导空白）。"""

    def _rep(m: re.Match) -> str:
        body, lead = m.group(0), m.group(1)
        s = body[len(lead):]
        neg = s.startswith("-")
        digits = s.lstrip("-").lstrip("0") or "0"
        return lead + ("-" if neg else "") + digits

    return re.sub(r"(?<=[:,\[{])(\s*)-?0+(?=\d)", _rep, t)


_GATE_LINE = re.compile(r"(\d{1,3})\s+(macro|micro|holdings)")
_PIPE = re.compile(r"\s*\|\s*")


def parse_pipe_lines(content: str, kind: str) -> dict | None:
    """行式填表协议解析（| 分隔）。散文/未知行自动忽略（宁缺勿滥）。
    kind: macro|micro|holdings → 规范化 dict（与 JSON 结构同构，供 chief/落盘）。"""
    out: dict = {}
    for ln in str(content or "").splitlines():
        f = [x.strip() for x in _PIPE.split(ln.strip())]
        if len(f) < 2 or not f[0]:
            continue
        if _is_placeholder_row(f):
            continue  # 模型照抄提示词里的示例占位行 → 丢弃
        tag = f[0].upper()
        if kind == "macro":
            if tag == "V" and len(f) >= 3:
                out["view"], out["bias"] = f[1], _fnum(f[2])
            elif tag == "D" and len(f) >= 2:
                out.setdefault("drivers", []).append(
                    {"item": f[1], "transmission": f[2] if len(f) > 2 else ""})
            elif tag == "R" and len(f) >= 2:
                out.setdefault("risks", []).append(
                    {"item": f[1], "severity": _fnum(f[2], 1) if len(f) > 2 else 1})
            elif tag == "C":
                out["confidence"] = _fnum(f[1])
        elif kind == "micro":
            if tag == "T" and len(f) >= 3:
                out.setdefault("themes", []).append({
                    "name": f[1], "logic": f[2],
                    "strength": int(_fnum(f[3], 1)) if len(f) > 3 else 1,
                    "catalyst": f[4] if len(f) > 4 else ""})
            elif tag == "E" and len(f) >= 4:
                out.setdefault("events", []).append({
                    "tickers": [t for t in f[1].replace("，", ",").split(",") if t],
                    "name": f[2], "event_type": f[3],
                    "sentiment": _fnum(f[4], 0) if len(f) > 4 else 0,
                    "note": f[5] if len(f) > 5 else ""})
            elif tag == "O":
                out["rotation"] = f[1]
            elif tag == "C":
                v = _strict_fnum(f[1])
                if v is not None:
                    out["confidence"] = max(0.0, min(1.0, v))
        elif kind == "chief":
            if tag == "T" and len(f) >= 2:
                out.setdefault("themes", []).append(
                    {"name": f[1], "logic": f[2] if len(f) > 2 else ""})
            elif tag == "W" and len(f) >= 2:
                out.setdefault("watch_list", []).append({
                    "code": f[1], "name": f[2] if len(f) > 2 else "",
                    "logic": f[3] if len(f) > 3 else "",
                    "trigger": f[4] if len(f) > 4 else ""})
            elif tag == "R":
                out.setdefault("open_risks", []).append(f[1])
            elif tag == "H" and len(f) >= 6:
                out.setdefault("holdings", []).append({
                    "code": f[1], "name": f[2], "verdict": f[3] or "中性",
                    "impact": _fnum(f[4], 0), "horizon": f[5] if len(f) > 5 else "",
                    "event_type": f[6] if len(f) > 6 else "",
                    "headline": f[7] if len(f) > 7 else "",
                    "source": f[8] if len(f) > 8 else "",
                    "note": f[9] if len(f) > 9 else ""})
            elif tag == "N":
                out["market_notes"] = f[1]
            elif tag == "E":
                out["editor_note"] = f[1]
            elif tag == "C":
                v = _strict_fnum(f[1])
                if v is not None:
                    out["confidence"] = max(0.0, min(1.0, v))
        elif kind == "holdings":
            if tag == "H" and len(f) >= 6:
                info = f[5] if len(f) > 5 else ""
                out.setdefault("per_stock", []).append({
                    "code": f[1], "name": f[2], "verdict": f[3] or "中性",
                    "impact": _fnum(f[4], 0),
                    "horizon": "短期" if "短期" in info else ("日内" if "日内" in info else ""),
                    "event_type": info.split("：")[0][:24] if info else "",
                    "headline": info.split("：", 1)[1] if "：" in info else info,
                    "source": f[6] if len(f) > 6 else "", "note": ""})
            elif tag == "X" and len(f) >= 2:
                out.setdefault("cross_risks", []).append({"code": f[1], "why": f[2] if len(f) > 2 else ""})
            elif tag == "A" and len(f) >= 2:
                out.setdefault("action_hints", []).append(f[1])
            elif tag == "C":
                v = _strict_fnum(f[1])
                if v is not None:
                    out["confidence"] = max(0.0, min(1.0, v))
    return out if out else None


def parse_gate_lines(content: str) -> dict | None:
    """门卫行式输出解析：`12 macro [证据]` 每行一条。模型写散文时整篇
    findall 救捞（散文里同样会出现 `12 macro` 形态）；无任何命中返回 None。"""
    hits = _GATE_LINE.findall(str(content or ""))
    if not hits:
        return None
    rel = []
    seen = set()
    for idx, direction in hits:
        i = int(idx)
        if i in seen:
            continue
        seen.add(i)
        rel.append({"i": i, "dir": direction})
    return {"related": rel} if rel else None


def parse_llm_json(text: str) -> dict | None:
    if not text:
        return None
    t = str(text)
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    for cand in (t[i:j + 1], _fix_leading_zero_ints(t[i:j + 1])):
        try:
            d = json.loads(cand)
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            continue
    return None


def call_llm(user: str, system: str, stage: str = "") -> tuple[str, dict | None]:
    """v4-flash 直连（重试 1 次，120s 超时）。失败 raise 由调用段降级。"""
    env = {}
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in env:
                env[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    base = env.get("OPENAI_API_BASE", "").rstrip("/")
    key = env.get("OPENAI_API_KEY", "")
    if not base or not key:
        raise RuntimeError("OPENAI_API_BASE/KEY 缺失")
    import requests

    payload = {"model": MODEL,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "temperature": 0.2, "max_tokens": _MAX_TOKENS.get(stage, 8000)}
    last_exc = None
    for attempt in range(2):
        try:
            resp = requests.post(f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 json=payload, timeout=120)
            resp.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == 0:
                print(f"  ⚠️ {MODEL} 调用失败，重试 1 次: {exc}")
    else:
        raise last_exc  # type: ignore[union-attr]
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    content = str(msg.get("content") or "").strip() or str(msg.get("reasoning_content") or "").strip()
    usage = data.get("usage") or None
    if usage:
        usage = {k: int(usage.get(k) or 0)
                 for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return content, usage


def append_log(user: str, content: str, sig: str, usage: dict | None = None) -> Path:
    """落盘对话日志（与交易 agent 同结构 → 前端 /api/agents/{sig}/logs 直接可读）。"""
    now = datetime.now(CN_TZ)
    log_dir = ROOT / "data" / "agent_data_astock" / sig / "log" / now.strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": now.isoformat(), "signature": sig,
             "new_messages": [{"role": "user", "content": user},
                              {"role": "assistant", "content": content}]}
    if usage:
        entry["usage"] = usage
    path = log_dir / "log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ---------- 主编分子：落盘 / 渲染 / 交易侧注入 ----------

def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def save_brief(brief: dict) -> None:
    _atomic_write(BRIEF_FILE, brief)
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(brief, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _list_of_dicts(v) -> list:
    """LLM 偶发把数组写成字符串/其他形态 → 统一只留 dict 列表，防下游崩。"""
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, dict)]


def _rows(v) -> list:
    """简报行列表：dict 或 str 均可（首席的 open_risks 常为字符串数组）。"""
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, (dict, str))]


def _fnum(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _strict_fnum(v) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


_PLACEHOLDER_PAT = ("（可多行）", "示例", "verdict(利好", "impact(-2..2)", "bias(-1..1)",
                    "代码(逗号分隔)", "confidence(0..1)", "severity(1..3)")
_PLACEHOLDER_TOKENS = {"theme", "logic", "name", "code", "主题", "名称", "代码",
                       "驱动逻辑", "trigger condition", "event_type", "板块/主题"}


def _is_placeholder_row(fields: list) -> bool:
    joined = " | ".join(fields)
    if any(p in joined for p in _PLACEHOLDER_PAT):
        return True
    # 模型把提示词字段名照抄成数据（如 T | theme | logic、W | name | code | ...）
    body = [x.strip().lower() for x in fields[1:] if x.strip()]
    return any(x in _PLACEHOLDER_TOKENS for x in body[:2])


def _maybe_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def brief_to_text(b: dict) -> str:
    """『新闻分子』markdown（嵌入交易/市场研究提示词）。字段缺失/异形逐段容忍。"""
    m = _maybe_dict(b.get("macro"))
    win = b.get("window") or {}
    ts = _iso(b.get("ts"))
    head = (f"【新闻分子（主编 {_bj_fmt(ts, '%H:%M')} · "
            f"窗口 {str(win.get('start') or '')[:16]}→{str(win.get('end') or '')[:16]}）】")
    lines = [head]
    if m:
        drv = "；".join(d.get("item", "") for d in _list_of_dicts(m.get("drivers"))[:3])
        lines.append(f"- 宏观：{str(m.get('view') or '—')}"
                     f"（偏度 {float(m.get('bias') or 0):+.1f}）"
                     + (f" 主线：{drv}" if drv else ""))
    for t in _list_of_dicts(b.get("themes"))[:3]:
        stars = "★" * int(_fnum(t.get("strength"), 0))
        lines.append(f"- 主题：{t.get('name')}"
                     + (f"（{stars}）" if stars else "") + f"· {str(t.get('logic', ''))[:80]}")
    for r in _rows(b.get("open_risks"))[:3]:
        item = r if isinstance(r, str) else r.get("item", "")
        lines.append(f"- 风险：{str(item)[:90]}")
    for h in _list_of_dicts(b.get("holdings"))[:8]:
        ev = str(h.get("event_type") or h.get("headline") or "")
        lines.append(f"- 持仓：{h.get('name')}({h.get('code')}) {h.get('verdict') or '中性'}"
                     f" impact{int(h.get('impact') or 0):+d} · {ev[:60]}（{h.get('source')}）")
    for w in _list_of_dicts(b.get("watch_list"))[:5]:
        lines.append(f"- 盯：{w.get('name')}({w.get('code')})"
                     f" {w.get('trigger') or w.get('logic') or ''}")
    conf = _fnum(b.get("confidence"))
    lines.append(f"- 置信度 {'—' if conf <= 0 else f'{conf:.2f}'} · 生成 {_bj_fmt(ts, '%H:%M')}"
                 f" · 主编备注：{b.get('editor_note') or '无'}")
    return "\n".join(lines)


def _seg_compact(stage: str, d: dict | None) -> str:
    """段结果 → 给主编的紧凑要点行（不贴大 JSON，防散文模型又写长篇）。"""
    if not d or d.get("skipped"):
        return f"[{AGENT_CN.get(stage, stage)}] 本窗口无输出"
    out = [f"[{AGENT_CN.get(stage, stage)}]"]
    if stage == MACRO:
        out.append(f"宏观 {d.get('view')}（bias {_fnum(d.get('bias')):+.1f}）")
        for x in _list_of_dicts(d.get("drivers"))[:4]:
            out.append(f"  · {x.get('item')} → {x.get('transmission')}")
        for x in _list_of_dicts(d.get("risks"))[:4]:
            out.append(f"  ⚠ {x.get('item')}(sev{_fnum(x.get('severity'), 1):.0f})")
    elif stage == MICRO:
        for t in _list_of_dicts(d.get("themes"))[:6]:
            out.append(f"主题 {t.get('name')} 强度{int(_fnum(t.get('strength'), 1))} · "
                       f"{t.get('logic')}（{t.get('catalyst')}）")
        for e in _list_of_dicts(d.get("events"))[:30]:
            out.append(f"事件 {e.get('name')} {','.join(e.get('tickers') or [])} "
                       f"{e.get('event_type')} {_fnum(e.get('sentiment')):+.1f} {e.get('note')}")
        if d.get("rotation"):
            out.append(f"轮动 {d.get('rotation')}")
    elif stage == HOLD:
        for h in _list_of_dicts(d.get("per_stock"))[:10]:
            out.append(f"持仓 {h.get('name')}({h.get('code')}) {h.get('verdict')} "
                       f"impact{_fnum(h.get('impact')):+.0f} {h.get('horizon')} · "
                       f"{h.get('event_type')}：{str(h.get('headline'))[:44]}（{h.get('source')}）"
                       + (f" note={h.get('note')}" if h.get("note") else ""))
        for x in _list_of_dicts(d.get("cross_risks"))[:3]:
            out.append(f"  ⚠ 联动 {x.get('code')} {x.get('why')}")
    out.append(f"置信度 {_fnum(d.get('confidence')):.2f}")
    return "\n".join(out)


def update_intraday_watch(micro: dict | None, watch_list: list | None,
                          path: Path | None = None) -> dict:
    """盘中异动关注（用户口径：池外异动股也要有 L2/微观结构关注，视野别是闭集）：
    来源 = 板块个股情报段的事件 tickers + 主编 watch_list。冷却 60 分钟、上限 20、
    新优先；live_l2_capture 轮询时合并。"""
    out_path = path or INTRA_WATCH_FILE
    now = datetime.now(CN_TZ)
    items: dict = {}
    try:
        old = _list_of_dicts(json.loads(out_path.read_text(encoding="utf-8")).get("items"))
        for it in old:
            if it.get("code") and it.get("ts"):
                items[str(it["code"])] = it
    except (OSError, json.JSONDecodeError):
        pass

    def _add(code: str, why: str) -> None:
        code = str(code or "").strip()
        if not code:
            return
        items[code] = {"code": code, "ts": now.isoformat(timespec="seconds"),
                       "why": str(why or "")[:60]}

    for e in _list_of_dicts((micro or {}).get("events")):
        for code in (e.get("tickers") or []):
            _add(code, f"{e.get('name')} {e.get('event_type')}")
    for w in _list_of_dicts(watch_list or []):
        _add(w.get("code"), f"主编盯:{w.get('trigger') or w.get('logic')}")

    # 冷却淘汰
    cutoff = now.timestamp() - INTRA_WATCH_TTL_MIN * 60
    kept = []
    for it in items.values():
        ts = _iso(it.get("ts"))
        if ts and ts.timestamp() >= cutoff:
            kept.append(it)
    kept.sort(key=lambda x: x["ts"], reverse=True)
    kept = kept[:INTRA_WATCH_MAX]
    out = {"ts": now.isoformat(timespec="seconds"), "items": kept}
    _atomic_write(out_path, out)
    return out


def load_intraday_watch() -> list:
    """供 L2 采集合并的异动代码列表（过期条目忽略）。"""
    try:
        d = json.loads(INTRA_WATCH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cutoff = datetime.now(CN_TZ).timestamp() - INTRA_WATCH_TTL_MIN * 60
    out = []
    for it in _list_of_dicts(d.get("items")):
        ts = _iso(it.get("ts"))
        if ts and ts.timestamp() >= cutoff and it.get("code"):
            out.append(str(it["code"]))
    return out[:INTRA_WATCH_MAX]


def load_lessons_text(top: int = 4) -> str:
    """晚间复盘经验（信号有效性统计）→ 各段提示词先验注入。缺失返回空。"""
    try:
        d = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    from news_review import lessons_text  # 延迟导入防环

    t = lessons_text(d, top=top)
    return (f"【历史经验（近30日新闻信号复盘，样本≥3 才列出；反向率高的事件类型要降权）】\n{t}"
            if t else "")


def empty_window_markup(ts: str) -> str:
    d = _iso(ts)
    return (f"窗口（截至 {_bj_fmt(d)}）无新增相关新闻：沿用上一版分子，无新决策依据。"
            if d else "窗口无新增相关新闻：沿用上一版分子。")


# ---------- 关注代码（持仓 + 候选池） ----------

def merge_watch_codes(positions: list | None, pool: list | None,
                      pool_codes: set | None = None) -> set:
    out = set(pool_codes or ())
    for p in positions or []:
        c = p.get("stock_code") if isinstance(p, dict) else ""
        if c and float(p.get("total_volume") or p.get("volume") or 0) > 0:
            out.add(str(c))
    for p in pool or []:
        c = p.get("code") or p.get("symbol") or p.get("stock_code") or ""
        if c:
            out.add(str(c))
    return out


def load_watch_codes() -> tuple[set, list]:
    """现场取持仓（桥）+ 候选池（live_trade_picks）；各自独立降级。"""
    codes, warns = set(), []
    try:
        from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

        acct = TdxBridgeBroker()._account_query()
        positions = [p for p in (acct.get("positions") or [])
                     if float(p.get("total_volume") or 0) > 0]
        codes |= {str(p.get("stock_code")) for p in positions if p.get("stock_code")}
    except Exception as exc:  # noqa: BLE001
        warns.append(f"持仓获取失败: {str(exc)[:80]}")
    try:
        from live_trade_picks import load_pool

        pool, _ = load_pool(20)
        codes |= {str(p.get("code")) for p in (pool or []) if p.get("code")}
    except Exception as exc:  # noqa: BLE001
        warns.append(f"候选池获取失败: {str(exc)[:80]}")
    return codes, warns


# ============================================================
# 编排
# ============================================================

CHUNK = 100          # 单批进门卫的条数（指令保持率 & 输出不超限）
_JSON_TAIL = ("\n\n严格只输出 JSON：不要任何解释/思考过程/分析草稿/markdown 代码块，"
              "回答的首字符必须是 {。")
_LINE_TAIL = ("\n\n逐行输出保留项（每行一个，格式：`序号 方向`，"
              "如 `12 macro`；方向限 macro|micro|holdings）。"
              "只输出这些行，不要任何解释/序号说明/代码块。")
_PIPE_TAIL = ("\n\n严格行式输出：每行一个条目，字段以 | 分隔（示例见上），"
              "不要解释/序号/标题/markdown/JSON，散文与多余文字一律不要。")
# 各段输出协议：全部行式填表（散文型模型 JSON 遵从率差，填表最稳；
# chief 2026-09-07 实测 JSON/行式混排指令会引发模型自我矛盾性长思考）
_MODE = {GATE: "gate", MACRO: "macro", MICRO: "micro", HOLD: "holdings", CHIEF: "chief"}
# 输出预算受 120s 超时约束（≈4-6k token 上限）：批量段用行式协议压缩输出
_MAX_TOKENS = {GATE: 4000, MICRO: 4000, MACRO: 3000, HOLD: 3000, CHIEF: 6000}


def _chunked(seq: list, n: int = CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _run_stage(stage: str, user: str) -> tuple[dict | None, bool]:
    """单段单批执行：LLM → 按段协议解析（行式/门卫/JSON）→ 落盘对话。
    失败返回 (None, False) 由上层降级（规则兜底/跳段）。"""
    mode = _MODE.get(stage, "json")
    tail = {"gate": _LINE_TAIL, "json": _JSON_TAIL}.get(mode, _PIPE_TAIL)
    try:
        content, usage = call_llm(user + tail, _system_for(stage), stage=stage)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ [{AGENT_CN[stage]}] LLM 失败: {exc}")
        return None, False
    if mode == "gate":
        d = parse_gate_lines(content)
    elif mode == "json":
        d = parse_llm_json(content)
    else:
        d = parse_pipe_lines(content, mode)
    path = append_log(user, content or "(空输出)", stage, usage)
    tok = f" token={usage and usage.get('total_tokens')}"
    if d is None:
        print(f"⚠️ [{AGENT_CN[stage]}] 结构化解析失败，原文已落盘{tok}")
    else:
        print(f"✓ [{AGENT_CN[stage]}] 完成{tok} → {path.relative_to(ROOT)}")
    return d, d is not None


def _merge_chunks(results: list) -> dict | None:
    """多批输出合并：同名字段 concat 数组 / 均值数值 / 首值其他。全败返回 None。"""
    rs = [r for r in results if r]
    if not rs:
        return None
    if len(rs) == 1:
        return rs[0]
    keys: set = set()
    for r in rs:
        keys |= set(r.keys())
    out: dict = {}
    for k in keys:
        vals = [r.get(k) for r in rs if r.get(k) is not None]
        if not vals:
            continue
        if all(isinstance(v, list) for v in vals):
            out[k] = [x for v in vals for x in v]
        elif all(isinstance(v, (int, float)) for v in vals):
            out[k] = sum(vals) / len(vals)
        else:
            out[k] = vals[0]
    return out


def _run_chunked(stage: str, arts: list, title: str) -> tuple[dict | None, bool]:
    """分批跑一段并合并；空输入短路。"""
    if not arts:
        print(f"⏭️ [{AGENT_CN[stage]}] 无输入，跳过")
        return None, False
    chunks = list(_chunked(arts))
    if len(chunks) > 1:
        print(f"[{AGENT_CN[stage]}] 分批 {len(chunks)}×{len(chunks[0])} 处理")
    d_list = []
    ok_all = True
    for c in chunks:
        d, ok = _run_stage(stage, _user_block(title, c))
        d_list.append(d)
        ok_all = ok_all and ok
    return _merge_chunks(d_list), ok_all and any(d_list)


def run_pipeline(since_iso: str = "", stages: tuple = ALL_STAGES,
                 window_until: str = "", force: bool = False) -> int:
    """完整管线。0=成功/空窗短路。每段独立降级，不因单段失败中断下游。
    节假日（工作日但非交易日）：白天不跑，晚间 22:00 后统一一次全景；
    --force 手动不受限。"""
    now = datetime.now(CN_TZ)
    if not force and not is_trading_day(now.date()) and now.hour < 21:
        print("⏭️ 节假日：白天不分析，晚间（22:00 后）统一一次全景")
        return 0
    st = load_state()
    if not since_iso:
        since_iso, _ = window_for(st.get("last_end", ""), now.isoformat())
    until_iso = window_until or now.isoformat()

    # 1) 拉取 + 规则减载 + 去重 + 截断（新→旧）
    raw, fails = fetch_incr(since_iso, until_iso)
    arts = dedup_articles([a for a in raw if pick_relevant(a)])
    if len(arts) > MAX_TITLES:
        print(f"ℹ️ 候选 {len(arts)} 超上限，保留最新 {MAX_TITLES}")
        arts = arts[:MAX_TITLES]
    print(f"窗口 {_bj_fmt(_iso(since_iso))} → {_bj_fmt(_iso(until_iso))}："
          f"原始 {len(raw)} / 候选 {len(arts)}"
          + (f" / 桶失败 {len(fails)}" if fails else ""))

    if not arts:
        save_state({**st, "last_end": until_iso, "last_empty": now.isoformat()})
        msg = empty_window_markup(until_iso)
        print("⏭️ " + msg)
        if CHIEF in stages:
            append_log(msg, "（无新增输入，未调用 LLM）", CHIEF)
        return 0

    # 2) 关注代码（持仓+候选池）→ gate 判定依据 + holdings 段
    lessons_block = ""
    try:
        lessons_block = load_lessons_text()
    except Exception:  # noqa: BLE001 经验缺失不阻塞
        lessons_block = ""
    watch_codes, warns = load_watch_codes()
    _atomic_write(WATCH_FILE, {"ts": now.isoformat(), "codes": sorted(watch_codes),
                               "warns": warns})
    watch_line = "关注代码（命中→holdings 方向）：" + (",".join(sorted(watch_codes)) or "无")

    # 3) 门卫（agent 化金融相关 + macro/micro/holdings 方向；分批防超限/防指令丢失）
    g_macro, g_micro, h_art, g_skip = [], [], [], 0
    if GATE in stages:
        rel_idx: dict = {}
        chunks = list(_chunked(arts))
        print(f"[{AGENT_CN[GATE]}] 并行 {len(chunks)} 批 × {len(chunks[0]) if chunks else 0}")

        def _gate_chunk(chunk: list) -> dict:
            gate_user = _user_block("候选新闻（请剔除与 A股 无关条目并按方向归类）", chunk) \
                + "\n" + watch_line + (f"\n{lessons_block}" if lessons_block else "")
            d, _ok = _run_stage(GATE, gate_user)
            if d:
                base = arts.index(chunk[0])  # 块内序号 → 全局序号
                return {base + x.get("i"): x.get("dir")
                        for x in (d.get("related") or [])
                        if isinstance(x, dict) and isinstance(x.get("i"), int)}
            print("⚠️ 门卫该批失败，本批按规则方向兜底")
            return {arts.index(a): rule_direction(a, watch_codes) for a in chunk}

        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
            for got in ex.map(_gate_chunk, chunks):
                rel_idx.update(got)
        kept_i = set(rel_idx)
        g_skip = len(arts) - len(kept_i)
        g_macro = [a for i, a in enumerate(arts) if rel_idx.get(i) == "macro"]
        g_micro = [a for i, a in enumerate(arts) if rel_idx.get(i) == "micro"]
        h_art = [a for i, a in enumerate(arts) if rel_idx.get(i) == "holdings"]
        g_micro = list({id(a): a for a in g_micro + [a for i, a in enumerate(arts)
                                                     if i not in kept_i]}.values())
        print(f"门卫分流 → macro {len(g_macro)} / micro {len(g_micro)} / "
              f"holdings {len(h_art)} / 剔除 {g_skip}")
    else:
        h_art, others = split_holdings_related(arts, watch_codes)
        g_macro = [a for a in others if _GLOBAL_RE.search(str(a.get("title") or ""))]
        g_micro = [a for a in others if not _GLOBAL_RE.search(str(a.get("title") or ""))]

    # 4) 大/小/持仓 并行（各段内分批；空子集短路；--stage 单跑时全量喂该段）
    gate_active = GATE in stages
    results: dict = {}

    def _task(stage: str, arts_sub: list) -> None:
        user = f"新闻输入（{AGENT_CN[stage]}）"
        if lessons_block:
            user += f"\n{lessons_block}"
        if stage == HOLD:
            user += "\n当前实盘持仓/关注池代码：" + (",".join(sorted(watch_codes)) or "无")
        d, ok = _run_chunked(stage, arts_sub, user)
        results[stage] = d if ok else {"skipped": True, "degraded": not d}

    tasks = []
    if MACRO in stages and (g_macro or not gate_active):
        tasks.append((MACRO, g_macro))
    if MICRO in stages and (g_micro or not gate_active):
        tasks.append((MICRO, g_micro))
    if HOLD in stages and (h_art or not gate_active):
        tasks.append((HOLD, h_art))
    with ThreadPoolExecutor(max_workers=3) as ex:
        for f in (ex.submit(_task, s, a) for s, a in tasks):
            f.result()
    for s, _a in tasks:
        results.setdefault(s, {"skipped": True})

    # 5) 主编汇总（含上一版分子连续性 + 各段原始 JSON）
    if CHIEF in stages:
        seg_parts = [_seg_compact(s, results.get(s)) for s in (MACRO, MICRO, HOLD)]
        prev = {}
        try:
            prev = json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        chief_user = (f"本窗口 {_bj_fmt(_iso(since_iso))} → {_bj_fmt(_iso(until_iso))} "
                      f"的三段分析如下；请汇总『新闻分子』。\n"
                      f"{watch_line}\n")
        if prev:
            prev_ref = {k: prev.get(k) for k in ("macro", "themes", "open_risks", "watch_list")}
            chief_user += "\n--- 上一版分子（保持连续口径参考）---\n" \
                + json.dumps(prev_ref, ensure_ascii=False)[:1600] + "\n"
        chief_user += "\n" + ("\n".join(seg_parts) if seg_parts else "（三段均无输出）")
        d, ok = _run_stage(CHIEF, chief_user)
        if d:
            # 主题按名去重（模型偶发同一主题多行）；confidence 缺失回退三段均值
            themes_dedup = list({t.get("name"): t
                                 for t in _list_of_dicts(d.get("themes"))}.values())
            seg_confs = [_fnum((results.get(k) or {}).get("confidence"))
                         for k in (MACRO, MICRO, HOLD)]
            seg_confs = [v for v in seg_confs if v > 0]
            conf = _fnum(d.get("confidence")) or (
                round(sum(seg_confs) / len(seg_confs), 2) if seg_confs else 0.0)
            brief = {
                "ts": now.isoformat(),
                "window": {"start": since_iso, "end": until_iso},
                "counts": {"raw": len(raw), "cand": len(arts), "gate_skip": g_skip},
                "warns": warns, "folder_failures": fails,
                "segments": results,                       # 各段原始 JSON（审计/前端可选读）
                "macro": _maybe_dict(results.get(MACRO)),  # 宏观分子 = ② 段（主编不重述）
                "themes": themes_dedup,
                "watch_list": _list_of_dicts(d.get("watch_list")),
                "open_risks": _list_of_dicts(d.get("open_risks")),
                "holdings": _list_of_dicts(d.get("holdings")),  # 主编保真转写（≤8）
                "market_notes": d.get("market_notes") or "",
                "confidence": conf,
                "editor_note": d.get("editor_note") or "",
            }
            brief["text"] = brief_to_text(brief)
            save_brief(brief)
            # 盘中异动关注（micro 事件 + 主编 watch）→ L2 轮询合并，视野不闭集
            iw = update_intraday_watch(results.get(MICRO), brief.get("watch_list"))
            print(f"✓ 盘中异动关注 {len(iw['items'])} 只 → {INTRA_WATCH_FILE.name}")
            print(f"✓ 主编分子已落盘 → {BRIEF_FILE}（confidence={brief['confidence']:.2f}）")
            print(brief["text"])

    save_state({**st, "last_end": until_iso, "last_stages": list(stages),
                "last_ok": now.isoformat()})
    return 0


def cli() -> int:
    ap = argparse.ArgumentParser(description="新闻 Agent 管线（全 v4-flash）")
    ap.add_argument("--since", default="", help="北京 'YYYY-MM-DD HH:MM'（默认增量游标）")
    ap.add_argument("--until", default="", help="北京 'YYYY-MM-DD HH:MM'（默认 now）")
    ap.add_argument("--stage", default="",
                    help="只跑单段：gate|macro|micro|holdings|chief")
    a = ap.parse_args()
    stages = ALL_STAGES
    if a.stage:
        stage_map = {}
        for _s in ALL_STAGES:
            stage_map[_s] = (_s,)
            stage_map[_s.removeprefix("news-")] = (_s,)
        stages = stage_map.get(a.stage)
        if not stages:
            print(f"未知 stage: {a.stage}（可选 {','.join(stage_map)}）", file=sys.stderr)
            return 2

    def _to_iso(local: str) -> str:
        return datetime.strptime(local, "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ).isoformat()

    try:
        return run_pipeline(
            since_iso=_to_iso(a.since) if a.since else "",
            window_until=_to_iso(a.until) if a.until else "",
            stages=stages, force=a.force)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 新闻管线失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
