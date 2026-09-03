#!/usr/bin/env python3
"""晚间市场研究 Agent（研究总控）：quantdb 最新数据 → 板块强度 + 明日候选池 20 只。

输出三处：
1. logs/night_pool/{date}.md/.json（研究纪要）
2. quantmind 报告目录 {date}_agent_picks.json（与现有 select_from_reports 格式兼容，
   明晨 load_pool 自动优先进该池——多个 agent 选股先从这里找）
3. 对话流（pseudo agent『研究总控』，data/agent_data_astock/market-research/）→ 模型对话页可见

用法：python scripts/night_pool_agent.py [--date 2026-09-03]
cron：交易日 北京19:30（JST20:30）。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "prompts"))


def _db():
    import duckdb

    return duckdb.connect()


def latest_dt() -> str:
    import glob

    for base in (ROOT.parent / "projects/quantmind/data/quantdb/1_kline_data/daily_backward",
                 Path("/data/quantdb/1_kline_data/daily_backward")):
        if base.is_dir():
            parts = sorted(glob.glob(f"{base}/dt=*"))
            return parts[-1].rsplit("dt=", 1)[-1]
    return ""


def load_names_industry() -> dict:
    import glob

    names = {}
    for base in (ROOT.parent / "projects/quantmind/data/quantdb/2_base_sector/instrument_detail",
                 Path("/data/quantdb/2_base_sector/instrument_detail")):
        f = base / "instrument_detail.parquet"
        if not f.is_file():
            continue
        try:
            con = _db()
            for sym, nm in con.execute(
                    "SELECT Symbol, Name FROM read_parquet(?)", [str(f)]).fetchall():
                if sym and nm:
                    names[str(sym)] = str(nm)
        except Exception:  # noqa: BLE001
            pass
        break
    return names


def build_candidates(date: str, top: int = 26) -> list:
    """最新日全市场打分 → 候选（量能/动量/买卖压力复合）。"""
    import glob

    base = None
    for b in (ROOT.parent / "projects/quantmind/data/quantdb/1_kline_data/daily_backward",
              Path("/data/quantdb/1_kline_data/daily_backward")):
        if b.is_dir():
            base = b
            break
    if base is None:
        return []
    parts = sorted(glob.glob(f"{base}/dt=*"))
    d0 = parts[-1].rsplit("dt=", 1)[-1]
    prev = [p for p in parts if p.rsplit("dt=", 1)[-1] < d0]
    prev = prev[-1].rsplit("dt=", 1)[-1] if prev else d0
    con = _db()
    f0, f1 = f"{base}/dt={d0}/*.parquet", f"{base}/dt={prev}/*.parquet"
    df = con.execute(
        f"""SELECT a.symbol, a.close c1, a.volume v1, a.amount amt,
                   b.close c0
            FROM read_parquet('{f0}') a
            LEFT JOIN read_parquet('{f1}') b USING (symbol)""").df()
    df = df.dropna(subset=["c1", "c0", "amt"])
    df["chg1"] = (df["c1"] / df["c0"] - 1) * 100
    df["turn_est"] = df["v1"] * df["c1"] / 10000  # 成交额估算亿元（对照 amount 更稳则用 amt）
    df = df[df["amt"] >= 20000]  # ≥2 亿元成交（万元口径）
    df = df[(df["chg1"] >= -3) & (df["chg1"] <= 8)]
    ms_dir = f"{ROOT.parent / 'projects/quantmind/data/quantdb/5_technical_derived/market_sentiment'}/dt={d0}/*.parquet"
    import glob as _g

    ms_files = _g.glob(ms_dir)
    if ms_files:
        ms = con.execute(
            f"SELECT symbol, momentum_1d, momentum_3d, buy_pressure, sell_pressure "
            f"FROM read_parquet('{ms_files[-1]}')").df()
        df = df.merge(ms, on="symbol", how="left")
        df["score"] = (df["chg1"].clip(-3, 8) * 0.4
                       + df["momentum_3d"].fillna(0).clip(-15, 15) * 0.35
                       + (df["buy_pressure"].fillna(0.5)
                          - df["sell_pressure"].fillna(0.5)) * 100 * 0.25)
    else:
        df["score"] = df["chg1"] * 0.6 + df["turn_est"] / 10000 * 0.4
    names = load_names_industry()
    df["name"] = df["symbol"].map(names)
    df = df[~df["name"].astype(str).str.contains("ST", na=False)]
    out = df.sort_values("score", ascending=False).head(top)
    rows = [{"code": r["symbol"], "name": str(r["name"]), "chg1": round(float(r["chg1"]), 2),
             "amount_yi": round(float(r["amt"]) / 10000, 2), "score": round(float(r["score"]), 2)}
            for _, r in out.iterrows()]
    return rows, d0


def run(date: str, dry: bool = False) -> int:
    cands, d0 = build_candidates(date)
    if not cands:
        print("❌ 无候选（数据缺失）")
        return 1
    from market_state import build_market_state

    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    try:
        state = build_market_state(TdxBridgeBroker())
    except Exception:  # noqa: BLE001
        state = "（盘面状态不可用）"
    cand_json = json.dumps(cands, ensure_ascii=False)
    task = f"""[晚间市场研究任务] 基于 quantdb {d0} 收盘数据已初筛出 {len(cands)} 只候选（附分数/涨跌/成交额）。

要求（时间盒 90 秒，禁止逐只调工具取数）：
1. 初筛数据已系统算好（涨跌/成交额/复合分/名称），直接使用；
   只有当名称缺失或分数明显异常（如 ST/涨跌停残留）时，才对单只做一次核对。
2. 综合给出：明日市场观点一句话 + 看好的 2-3 个板块（名称+强度理由）+
   最终 20 只股票池（code/name/score/一句话选股理由），按行业分散、剔除重复题材。
3. 若现有持仓 688183/600309/300750 等本身强势也可保留注明。
盘面状态：{state}
候选初筛：{cand_json}

严格以 JSON 输出（无其他文字）：
{{"date":"{d0}","market_view":"...","sectors":[{{"name":"","strength":"","reason":""}}],
 "pool":[{{"code":"","name":"","score":0,"reason":""}}]}}
"""
    if dry:
        print(task[:2500])
        return 0
    from dsh_agent import run_agent

    try:
        content = run_agent(task, timeout_s=420, model="glm")  # glm 快端点，时间盒90s
    except Exception as exc:  # noqa: BLE001
        content = f"（研究 agent 失败：{exc}）"
    # JSON 抽取
    import re

    payload = None
    for m in re.finditer(r"\{[\s\S]*\}", content):
        try:
            payload = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    if not isinstance(payload, dict):
        payload = {"date": d0, "pool": [], "market_view": content[:400]}
    payload["date"] = d0
    dpath = ROOT / "logs" / "night_pool"
    dpath.mkdir(parents=True, exist_ok=True)
    (dpath / f"{d0}.md").write_text(f"# 晚间市场研究 · {d0}\n\n{content}", encoding="utf-8")
    (dpath / f"{d0}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    # 候选池写 quantmind 报告目录（兼容 select_from_reports：side/score/reason）
    try:
        pool = []
        for p in (payload.get("pool") or [])[:20]:
            pool.append({"code": p.get("code"), "name": p.get("name"),
                         "side": "HOLD", "score": float(p.get("score") or 0),
                         "reason": str(p.get("reason") or "")[:160]})
        out = {"date": d0, "market_direction": {"direction": payload.get("market_view", "")[:80]},
               "picks": pool}
        picks_dir = ROOT.parent / "projects/quantmind/data/reports/stock_picks"
        picks_dir.mkdir(parents=True, exist_ok=True)
        (picks_dir / f"{d0}_agent_picks.json").write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"✅ 池文件 {d0}_agent_picks.json（{len(pool)} 只）")
    except OSError as exc:  # noqa: BLE001
        print(f"⚠️ 写池文件失败: {exc}")
    # 对话流：pseudo『研究总控』
    try:
        lf = ROOT / "data" / "agent_data_astock" / "market-research" / "log" / date / "log.jsonl"
        lf.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": datetime.now().astimezone().isoformat(),
               "signature": "market-research", "kind": "night_pool",
               "new_messages": [
                   {"role": "user", "content": f"【晚间市场研究任务】{d0} 收盘数据 → 明日板块+候选池"},
                   {"role": "assistant", "content": content},
               ]}
        with lf.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:  # noqa: BLE001
        print(f"⚠️ 写对话流失败: {exc}")
    print(f"✅ 晚间研究完成 → logs/night_pool/{d0}.md/.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    date = a.date or datetime.now().strftime("%Y-%m-%d")
    return run(date, dry=a.dry)


if __name__ == "__main__":
    sys.exit(main())