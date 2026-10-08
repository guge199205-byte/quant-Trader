#!/usr/bin/env python3
"""每日三时段 QQ 推送：早盘简报 / 盘后复盘 / 晚间选股（cron 到点消费管线产物）。

  morning     北京 09:28（开盘前）：风控档位 + 新闻主编宏观一句 + 候选池 Top3 +
              桥/Windows 状态
  postreview  北京 15:45（复盘 15:35 后）：每 agent 盘后复盘「一句话总评」
  evening     北京 23:40（选股 23:35 后）：明日候选 Top10 + 大盘方向

数据源（全部只读，绝不改管线）：
  configs/risk_budget.json          风控档位
  data/news_brief/latest.json       新闻分子（主编宏观/主题）
  logs/premarket_probe_state.json   盘前桥探测状态
  quantmind/data/reports/stock_picks/*_agent_picks.json   候选池（研究总控产出）
  logs/review/{agent}/{date}.md     每 agent 盘后复盘报告

用法：python scripts/push_daily_briefs.py {morning|postreview|evening}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from push_notify import bold, notify, _now_cn  # noqa: E402

PICKS_DIR = ROOT / "../projects/quantmind/data/reports/stock_picks"
REVIEW_DIR = ROOT / "logs" / "review"
MAX_LINE = 140


def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_picks() -> dict | None:
    """最新 *_agent_picks.json：{date, session, market_direction, picks[]}；读不到 → None。"""
    files = sorted(PICKS_DIR.glob("*_agent_picks.json")) if PICKS_DIR.exists() else []
    if not files:
        return None
    return _load_json(files[-1])


def _short(text, n: int = MAX_LINE) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:n] + "…" if len(text) > n else text


# ---------- 早盘简报 ----------


def cmd_morning() -> int:
    lines = []
    d = _load_json(ROOT / "configs" / "risk_budget.json")
    if isinstance(d, dict):
        b = (d.get("budget") or {})
        lines.append(f"风控档位：{bold(d.get('label') or d.get('level') or '?')}"
                     f"（杠杆 ≤{bold(b.get('leverage_max') or '?')}× · 单票 ≤{bold(b.get('per_stock_pct') or '?')}"
                     f" · 新开仓 ≤{bold(b.get('max_new_buys') or '?')}只）")
    else:
        lines.append("风控档位：未定档（已回退买入侧防守参数，2026-09-18 起 fail-safe）")
    news = _load_json(ROOT / "data" / "news_brief" / "latest.json")
    if isinstance(news, dict):
        macro = news.get("macro")
        if isinstance(macro, list):
            macro = macro[0] if macro else ""
        macro = _short(macro, 150)
        themes = news.get("themes")
        if isinstance(themes, list) and themes:
            themes = _short(themes[0], 90)
        if macro:
            lines.append(f"宏观：{macro}")
        if themes:
            lines.append(f"主线：{themes}")
    pool = latest_picks()
    if isinstance(pool, dict):
        picks = (pool.get("picks") or [])[:3]
        top = "、".join(bold(f"{p.get('name') or p.get('code')}") for p in picks) or "—"
        lines.append(f"候选池（{pool.get('date') or '?'}）：{top}")
    probe = _load_json(ROOT / "logs" / "premarket_probe_state.json")
    bridge = "在线 ✓" if (isinstance(probe, dict) and not probe.get("down")) else "⚠️ 不在线"
    lines.append(f"Windows/桥：{bold(bridge)}")
    notify(f"🌅 早盘简报 · {_now_cn():%m-%d}", "\n".join(lines))
    return 0


# ---------- 盘后复盘 ----------


def _clamp_sentences(text: str, max_n: int = 2) -> str:
    """按句号切句保留前 max_n 句（总评压缩：流水账的长句链只留开头结论）。"""
    parts = [p.strip() for p in re.split(r"(?<=[。！？])", str(text or "")) if p.strip()]
    return "".join(parts[:max_n])


def _review_agents(today: str) -> list[tuple[str, str]]:
    """[(agent, 一句话总评)]：读 logs/review/{agent}/{today}.md，取「一句话总评」节首段。

    标题格式各 agent 不统一（实测 2026-09-14：`## ① 一句话总评`、
    `### 一句话总评`、`## 一、一句话总评` 三种并存）——只匹配关键词「一句话总评」。
    """
    out = []
    if not REVIEW_DIR.is_dir():
        return out
    for agent_dir in sorted(REVIEW_DIR.iterdir()):
        md = agent_dir / f"{today}.md"
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^[#>*\-\s]*(?:①|一、)?\s*一句话总评[^\n]*\n+(.*?)(\n{2,}|\Z)",
                      text, re.M | re.S)
        if not m:
            print(f"⚠️ postreview: {agent_dir.name} {today}.md 无「一句话总评」节，跳过")
            continue
        # 句切压缩：长句链只保留前两句（用户口径：一眼看懂）
        summary = _short(_clamp_sentences(_strip_md(m.group(1))), 140)
        if summary:
            out.append((agent_dir.name, summary))
    return out


def _strip_md(text: str) -> str:
    return re.sub(r"^[#>*\-\s]+", "", str(text or "").strip())


def cmd_postreview() -> int:
    today = _now_cn().strftime("%Y-%m-%d")
    rows = _review_agents(today)
    if not rows:
        # 无今日复盘（节假日/未跑）→ 不打扰，但留日志便于排查
        print(f"postreview {today}: 无复盘产物或解析为空，跳过")
        return 0
    lines = []
    for agent, summary in rows:
        lines.append(f"[{bold(agent)}] {summary}")
    notify(f"🖊 盘后复盘 · {today}", "\n".join(lines))
    return 0


# ---------- 晚间选股 ----------


def cmd_evening() -> int:
    pool = latest_picks()
    if not isinstance(pool, dict):
        return 0  # 池文件都没生成（管线冻结等）→ 不打扰
    direction = "".join((pool.get("market_direction") or {}).get("direction", "")) \
        if isinstance(pool.get("market_direction"), dict) else ""
    picks = pool.get("picks") or []
    if not picks:
        # 2026-09-15 实录：研究 agent 失败落空池，静默让用户以为「没推送=没干活」。
        # 空池必须出声：说明为什么明天不开新仓。
        hint = _short(direction, 200) if direction else "（方向字段缺失）"
        notify(f"⚠️ 晚间选股无候选 · {pool.get('date') or '?'}",
               f"候选池为空 —— 研究 agent 未产出有效选股\n"
               f"线索：{hint}\n"
               f"影响：明日调仓轮不开新仓（卖出/条件位照常）")
        return 0
    lines = []
    if direction:
        lines.append(f"大盘方向：{_short(direction, 150)}")
    lines.append("")
    for i, p in enumerate((pool["picks"] or [])[:10], 1):
        lines.append(f"{i}. {bold(p.get('name') or '?')}({bold(p.get('code') or '?')}) "
                     f"分{bold(p.get('score') or '?')} {_short(p.get('reason') or '', 55)}")
    notify(f"🌙 明日候选 · {pool.get('date') or '?'}", "\n".join(lines))
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "morning":
        return cmd_morning()
    if cmd == "postreview":
        return cmd_postreview()
    if cmd == "evening":
        return cmd_evening()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())