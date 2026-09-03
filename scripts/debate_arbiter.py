#!/usr/bin/env python3
"""辩论 arbiter v2：检测跨 agent 分歧 → 单次廉价 LLM 仲裁（只出建议，不代执行权）。

触发：同一自然日、同标的、方向相反（sell vs buy/watch-加仓）。
流程：分歧事实 → glm 单轮仲裁 → {resolution, leaner, confidence, note}
落盘 logs/debates/{date}.jsonl；供人审与次日提示词引用（v1 分歧标注已在
_cross_agent_refs）。任何异常静默降级（不阻塞分析主流程）。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DB_DIR = ROOT / "logs" / "debates"
CONF_THRESHOLD = 0.7  # 任一方高置信才仲裁


def find_conflicts(decisions_map: dict | None = None) -> list:
    if decisions_map is None:
        from live_hourly_analysis import load_last_decisions

        decisions_map = load_last_decisions()
    last = decisions_map
    by_code: dict = {}
    agents = []
    for agent, rec in (last or {}).items():
        agents.append(agent)
        for d in (rec or {}).get("decisions") or []:
            code = str(d.get("code") or "")
            act = str(d.get("action") or "hold").lower()
            if not code or act not in ("buy", "sell", "watch"):
                continue
            by_code.setdefault(code, []).append(
                {"agent": agent, "action": act, "conf": float(d.get("confidence") or 0),
                 "reason": str(d.get("reason") or "")[:120]})
    conflicts = []
    for code, lst in by_code.items():
        sells = [x for x in lst if x["action"] == "sell"]
        buys = [x for x in lst if x["action"] in ("buy", "watch")]
        if not sells or not buys:
            continue
        hi = [x for x in lst if x["conf"] >= CONF_THRESHOLD]
        if not hi and len(lst) < 3:
            continue  # 低置信分歧不打扰
        conflicts.append({"code": code, "sides": lst})
    return conflicts


def check_and_arbitrate() -> None:
    conflicts = find_conflicts()
    if not conflicts:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    DB_DIR.mkdir(parents=True, exist_ok=True)
    out_f = DB_DIR / f"{today}.jsonl"
    for c in conflicts:
        brief = json.dumps(c, ensure_ascii=False)
        task = (
            "你是交易分歧仲裁员。三位分账交易 agent 对同一标的方向相反，请基于"
            "分歧双方理由与风控纪律给出仲裁建议（只建议，不执行）：\n" + brief +
            "\n输出 JSON：{\"resolution\":\"buy|sell|hold|watch\","
            "\"leaner\":\"更认可哪方或 none\",\"confidence\":0-1,"
            "\"note\":\"2-3句理由（引用证据/风控）\"}")
        note = ""
        try:
            from dsh_agent import run_agent

            content = run_agent(task, timeout_s=120, model="glm")
            import re

            for m in re.finditer(r"\{[\s\S]*\}", content):
                try:
                    verdict = json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
                if isinstance(verdict, dict) and verdict.get("resolution"):
                    note = json.dumps(verdict, ensure_ascii=False)
                    break
            if not note:
                note = f"（仲裁未结构化：{content[:200]}）"
        except Exception as exc:  # noqa: BLE001
            note = f"（仲裁失败：{exc}）"
        row = {"ts": datetime.now().isoformat(), "conflict": c, "verdict": note}
        with out_f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"⚖️ 分歧仲裁 {c['code']} → {note[:160]}")


if __name__ == "__main__":
    check_and_arbitrate()
    sys.exit(0)