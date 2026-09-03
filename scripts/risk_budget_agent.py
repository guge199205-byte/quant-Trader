#!/usr/bin/env python3
"""风险预算 meta-agent v1（确定性规则内核，见 docs/AGENT_PHASE34_DESIGN.md）。

输入：指数 20 日波动（上证/沪深300）、分账净值回撤、Fuyao 情绪温度、大盘三态。
输出：configs/risk_budget.json（闸门读取）+ logs/budget/{date}.json 明细。
防抖：同一自然日只允许收紧一次、不允许放大（隔日按最新状态自动恢复）。
cron：交易日 北京 09:10。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "configs" / "risk_budget.json"
DETAIL = ROOT / "logs" / "budget"
STATE = ROOT / "logs" / "budget" / "state.json"


def _index_vol() -> float | None:
    """上证近 20 日日收益率标准差（%）→ 波动率代理。"""
    try:
        from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

        k = TdxBridgeBroker().get_klines("000001.SH", interval="daily")
    except Exception:  # noqa: BLE001
        return None
    closes = [float(x.get("close") or 0) for x in (k or [])[-21:]]
    if len(closes) < 11 or not all(closes):
        return None
    rets = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
    m = sum(rets) / len(rets)
    sd = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5
    return round(sd, 2)


def _equity_drawdown() -> float | None:
    """分账总净值近 20 日（取每日最后一条）最大回撤 %。"""
    try:
        daily = {}
        for l in (ROOT / "logs" / "live_equity.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("agent") is None and r.get("value") is not None:
                daily.setdefault(str(r.get("date")), r["value"])
        vals = [v for _, v in sorted(daily.items())[-20:]]
        if len(vals) < 5:
            return None
        peak = max(vals)
        dd = (peak - min(vals)) / peak * 100
        return round(dd, 2)
    except (OSError, ValueError):
        return None


def _sentiment() -> tuple[int, int]:
    """（涨停家数, 最高连板）Fuyao；失败返回 (None,None)。"""
    try:
        import os

        sys.path.insert(0, str(ROOT / "dsh/skills/ths-fuyao/scripts"))
        from ths_fuyao import get

        os.environ.setdefault("THS_FUYAO_KEY", "")
        env = {}
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("THS_FUYAO_KEY="):
                env["THS_FUYAO_KEY"] = line.split("=", 1)[1].strip().strip('"')
        os.environ.update({k: v for k, v in env.items() if v})
        zt = ((get("/api/a-share/special-data/limit-up-pool", {}).get("data") or {}).get("item")) or []
        ld = ((get("/api/a-share/special-data/limit-up-ladder", {}).get("data") or {}).get("item")) or []
        mx = max([int(x.get("continue_day") or 0) for x in ld] or [0])
        return len(zt), mx
    except Exception:  # noqa: BLE001
        return 0, 0


LEVELS = {
    "calm": {"leverage_max": 1.5, "per_stock_pct": 0.2, "max_new_buys": 3,
             "leverage_trim_to": 1.3, "label": "平静"},
    "caution": {"leverage_max": 1.2, "per_stock_pct": 0.15, "max_new_buys": 2,
                "leverage_trim_to": 1.15, "label": "谨慎"},
    "defensive": {"leverage_max": 1.0, "per_stock_pct": 0.10, "max_new_buys": 1,
                  "leverage_trim_to": 1.0, "label": "防守"},
}


def main() -> int:
    vol = _index_vol()
    dd = _equity_drawdown()
    zt, ladder = _sentiment()
    state_txt = "平静"
    reasons = []
    if vol is not None and vol >= 1.2:
        reasons.append(f"波动 {vol}% 偏高")
    if dd is not None and dd >= 5:
        reasons.append(f"回撤 {dd}% 超限")
    if dd is not None and 3 <= dd < 5:
        reasons.append(f"回撤 {dd}% 加深")
    if zt and zt < 25:
        reasons.append(f"涨停仅 {zt} 家情绪偏冷")
    if zt and zt > 90:
        reasons.append(f"涨停 {zt} 家情绪过热")
    if ladder and ladder >= 2 and zt and zt < 25:
        reasons.append("连板萎缩")
    if reasons and not (len(reasons) == 1 and "波动" in reasons[0]):
        state_txt = "防守" if (dd or 0) >= 5 else "谨慎"
    elif reasons and dd is None and vol is not None and vol >= 1.2:
        state_txt = "谨慎"
    lvl = LEVELS.get("defensive" if state_txt == "防守" else "caution" if state_txt == "谨慎" else "calm")
    today = datetime.now().strftime("%Y-%m-%d")
    # 防抖：同日只收紧一次；不允许放大
    st = {}
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    prev_level = st.get("level")
    order = {"calm": 0, "caution": 1, "defensive": 2}
    if st.get("date") == today and prev_level and order.get(state_txt, 0) < order.get(prev_level, 0):
        lvl = LEVELS.get(prev_level)
        reasons.append(f"防抖：当日已定 {prev_level}，不再放宽")
    DETAIL.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"date": today, "level": state_txt}, ensure_ascii=False), encoding="utf-8")
    doc = {"date": today, "level": state_txt, "label": lvl["label"],
           "inputs": {"vol20": vol, "drawdown20": dd, "limit_up": zt, "max_ladder": ladder},
           "budget": {k: lvl[k] for k in ("leverage_max", "per_stock_pct", "max_new_buys",
                                          "leverage_trim_to")},
           "reasons": reasons,
           "note": "确定性规则内核 v1；状态恢复即自动放松（隔日生效），同日只收紧一次"}
    # v2：LLM 解释档位含义（数值以确定性为准，解释失败不影响）
    try:
        from dsh_agent import run_agent

        task = ("当前风险档=" + lvl["label"] + "，预算="
                + json.dumps(doc["budget"], ensure_ascii=False) + "，触发因素="
                + str(reasons or "无")
                + "。用 2-3 句话给当日三个分账 agent 讲清操作含义：加仓空间、减仓纪律、新动作数量建议。")
        doc["explain"] = run_agent(task, timeout_s=90, model="glm")[:300]
    except Exception:  # noqa: BLE001
        doc["explain"] = ""
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (DETAIL / f"{today}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(f"✅ 风险预算 → {state_txt}（{lvl['label']}）", "·".join(reasons) if reasons else "(平静)")
    return 0


if __name__ == "__main__":
    sys.exit(main())