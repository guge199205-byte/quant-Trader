#!/usr/bin/env python3
"""盘中持股分析：交易时段每小时分析实盘持仓（通达信桥），写入模型对话日志。

- 数据: 桥 _account_query(持仓+实时价) + 日K昨收(涨跌) + quantdb/静态表(股票名称)
- 分析: 每个 enabled agent 各自逐只简评 + 操作建议（LLM 失败时降级为数据摘要，不崩溃）
- 落盘: data/agent_data_astock/deepseek-v4-flash/log/{北京日期}/log.jsonl (append)
        → ARENA(8092) 模型对话 tab 直接可读
- 时段: 北京 9:30-11:30 / 13:00-15:00 工作日（本机 JST，不依赖系统时区）

用法:
  python scripts/live_hourly_analysis.py            # 交易时段才执行
  python scripts/live_hourly_analysis.py --force    # 忽略时段检查（调试/补跑）
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))

# ---- 方案 C: 波动触发参数 ----
STATE_PATH = ROOT / "logs" / "live_analysis_state.json"  # 上次分析的持仓基线
MIN_ANALYSIS_INTERVAL_MIN = 20  # 波动触发节流: 距上次完整分析不足 20 分钟不重复触发
TRIGGER_PNL_PP = 3.0            # 任一持仓盈亏% 较上次分析变化 ≥3pp → 触发
TRIGGER_DAY_CHG = 5.0           # 任一持仓个股当日涨跌 ≥5% → 触发



def _load_dotenv() -> None:
    """加载项目 .env（仅补缺省环境变量，不打印任何值）。"""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"')


_load_dotenv()


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def in_trading_window(now: datetime) -> bool:
    """A股交易日交易时段（北京）：9:30-11:30 / 13:00-15:00。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


def load_names() -> dict:
    """股票名称: 静态表 + quantdb instrument_detail(全市场)。"""
    names: dict = {}
    try:
        from tools.stock_names import CN_STOCK_NAMES

        names = dict(CN_STOCK_NAMES)
    except Exception:  # noqa: BLE001
        pass
    try:
        import duckdb

        for _root in (Path(os.getenv("QM_QUANTDB_DATA_DIR", "")),
                      Path.home() / "projects/quantmind/data/quantdb",
                      Path("/data/quantdb")):
            detail = _root / "2_base_sector/instrument_detail/instrument_detail.parquet"
            if not detail.is_file():
                continue
            _con = duckdb.connect()
            try:
                for sym, nm in _con.execute(
                        "SELECT Symbol, Name FROM read_parquet(?)", [str(detail)]).fetchall():
                    if sym and nm:
                        names.setdefault(sym, nm)
            finally:
                _con.close()
            break
    except Exception:  # noqa: BLE001
        pass
    return names


def build_rows(broker, positions: list, names: dict) -> list:
    """持仓 → 分析行: 名称/代码/现价/成本/数量/盈亏/盈亏%/今日涨跌。"""
    rows = []
    for p in positions:
        code = p.get("stock_code") or ""
        if not code:
            continue
        cost = float(p.get("cost_price") or 0)
        price = float(p.get("last_price") or 0)
        volume = float(p.get("total_volume") or 0)
        # 桥 account 不返回实时价，需按代码补 quote（同 api_server live_account）
        if not price and code:
            try:
                quote = broker.get_quote(code, "")
                price = float((quote or {}).get("close") or 0)
            except Exception:  # noqa: BLE001
                price = 0
        pnl = price * volume - cost * volume
        pnl_pct = (price - cost) / cost * 100 if cost else 0.0
        # 今日涨跌: 日K 昨收
        day_chg = None
        try:
            klines = broker.get_klines(code, interval="daily")
            if len(klines) >= 2:
                prev_close = float(klines[-2]["close"])
                if prev_close:
                    day_chg = (price - prev_close) / prev_close * 100
        except Exception:  # noqa: BLE001
            pass
        rows.append({
            "code": code, "name": names.get(code, code),
            "price": round(price, 2), "cost": round(cost, 2),
            "volume": int(volume), "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "day_chg": round(day_chg, 2) if day_chg is not None else None,
        })
    return rows


def build_user_content(rows: list, asset: float, cash: float, agent: str) -> str:
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（A股{('盘中' if in_trading_window(now_cn()) else '盘前/盘后')}）。"
        f"你是 {agent}（实盘分账账户，初始额度 ¥10 万）。你名下虚拟资产 ¥{asset:,.0f}、"
        f"可用虚拟现金 ¥{cash:,.0f}。你名下实盘持仓：",
        "",
        "| 股票 | 代码 | 现价 | 成本 | 数量 | 持仓金额 | 盈亏 | 盈亏% | 今日涨跌% |",
        "|------|------|------|------|------|----------|------|-------|-----------|",
    ]
    for r in rows:
        day = f"{r['day_chg']:+.2f}" if r["day_chg"] is not None else "—"
        lines.append(
            f"| {r['name']} | {r['code']} | {r['price']} | {r['cost']} | {r['volume']} "
            f"| ¥{r['price'] * r['volume']:,.0f} | ¥{r['pnl']:+,.0f} | {r['pnl_pct']:+.2f}% | {day} |"
        )
    lines += [
        "",
        "请逐只给出：①一句话简评（行情/基本面角度）②操作建议（持有/加仓/减仓/止损）③理由。"
        "今天买入的股票 T+1 明天才能卖，建议时注意这一点。输出简洁 markdown，不用复述表格。",
    ]
    return "\n".join(lines)


def call_llm(user_content: str, model: str, system_prompt: str | None = None) -> tuple[str, dict | None]:
    """OpenAI 兼容接口调用指定模型；失败返回空串。
    返回 (content, usage)：usage = {prompt_tokens, completion_tokens, total_tokens} 供累计统计。
    system_prompt 覆盖默认系统提示词（比赛配置多轮分析用）。"""
    import requests

    # 各模型走各自 API 供应商（glm 走智谱，其余走 deepseek）
    if model.startswith("glm"):
        base = os.getenv("GLM_API_BASE", "").rstrip("/")
        key = os.getenv("GLM_API_KEY", "")
    else:
        base = os.getenv("OPENAI_API_BASE", "").rstrip("/")
        key = os.getenv("OPENAI_API_KEY", "")
    if not base or not key:
        return "", None
    base_prompt = system_prompt or (
        f"你是 {model} 模型驱动的 A股 实盘交易助手盘中持仓分析师。"
        "分析冷静客观，给可执行的操作建议，注意 A股 T+1 规则与风险。输出中文 markdown。")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": base_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    # 推理模型: content 可能为空，回退 reasoning_content
    content = str(msg.get("content") or "").strip() or str(msg.get("reasoning_content") or "").strip()
    usage = data.get("usage") or None
    if usage:
        usage = {k: int(usage.get(k) or 0)
                 for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return content, usage


def record_equity(broker, asset: float, cash: float) -> None:
    """实盘净值记录（logs/live_equity.jsonl，按北京 分钟级 key + agent 去重）。
    两条口径，供 ARENA 总账户净值图：
      - 总账户（asset）：通达信桥实时总资产，一行
      - 每 agent 分账虚拟净值：虚拟现金 + 名下持仓 × 桥实时价（用户要求按 ¥10 万口径+现通达信行情）
    并发安全：fcntl 文件锁内重新扫描去重（每分钟采样 cron 与整点分析 cron 可能同时写，
    只查一次 existing_keys 会双写同一分钟 → 13:00 曾出现重复点）。
    """
    import fcntl

    now = now_cn()
    date_key = now.strftime("%Y-%m-%d %H:%M")  # 分钟级采样，分钟级去重
    path = ROOT / "logs" / "live_equity.jsonl"
    lock_path = ROOT / "logs" / ".live_equity.lock"
    path.parent.mkdir(parents=True, exist_ok=True)

    # 先算好全部条目（含网络取价），锁内只做去重 + 落盘
    entries = [
        {"key": date_key, "agent": None, "date": now.strftime("%Y-%m-%d"),
         "ts": now.isoformat(), "value": round(asset, 2),
         "asset": round(asset, 2), "cash": round(cash, 2)}
    ]
    from live_ledger import agent_virtual_cash, load_ledger

    ledger = load_ledger()
    for agent, rec in (ledger.get("agents") or {}).items():
        mkt = 0.0
        for code, p in (rec.get("positions") or {}).items():
            price = float(p.get("cost_price") or 0)
            try:
                quote = broker.get_quote(code, "")
                px = float((quote or {}).get("close") or 0)
                if px > 0:
                    price = px
            except Exception:  # noqa: BLE001
                pass
            mkt += price * float(p.get("volume") or 0)
        entries.append({"key": date_key, "agent": agent, "date": now.strftime("%Y-%m-%d"),
                        "ts": now.isoformat(),
                        "value": round(agent_virtual_cash(ledger, agent) + mkt, 2)})

    with lock_path.open("a", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        existing_keys = set()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    existing_keys.add((json.loads(line).get("key"), json.loads(line).get("agent")))
                except json.JSONDecodeError:
                    continue
        with path.open("a", encoding="utf-8") as f:
            for e in entries:
                if (e["key"], e["agent"]) in existing_keys:
                    continue
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
                existing_keys.add((e["key"], e["agent"]))
        fcntl.flock(lf, fcntl.LOCK_UN)


def build_leaderboard() -> str:
    """今日各 agent 虚拟净值排行榜（读 live_equity.jsonl 今日最后一条/agent，零网络开销）。
    供「情境感知」配置注入——让模型知道自己是领先还是落后。"""
    path = ROOT / "logs" / "live_equity.jsonl"
    if not path.is_file():
        return ""
    today = now_cn().strftime("%Y-%m-%d")
    best: dict = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("agent") and e.get("date") == today:
                best[e["agent"]] = e["value"]
    except (json.JSONDecodeError, OSError):
        return ""
    if not best:
        return ""
    items = sorted(best.items(), key=lambda kv: -float(kv[1]))
    return "\n".join(f"- {a}: ¥{float(v):,.0f}" for a, v in items)


def system_prompt_for(model: str, mode: dict) -> str:
    """比赛配置的系统提示词：基础分析师人设 + 该配置的中文分析要求。"""
    base = (f"你是 {model} 模型驱动的 A股 实盘交易助手盘中持仓分析师。"
            "分析冷静客观，给可执行的操作建议，注意 A股 T+1 规则与风险。输出中文 markdown。")
    return base + f"\n\n【本次分析配置：{mode['name']}】\n{mode['prompt']}"


def append_log(user_content: str, content: str, sig: str, usage: dict | None = None) -> Path:
    """写入模型对话日志(agent_data_astock/{sig}/log/{date}/log.jsonl)。
    usage 记录本次 LLM 调用的真实 token 消耗(供前端累计统计)。"""
    now = now_cn()
    log_dir = ROOT / Path("data/agent_data_astock") / sig / "log" / now.strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": now.isoformat(),
        "signature": sig,
        "new_messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": content},
        ],
    }
    if usage:
        entry["usage"] = usage
    path = log_dir / "log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def record_window(now: datetime) -> bool:
    """净值采样时段：9:25-11:30 / 13:00-15:10 工作日（跳过午休 11:31-12:59，
    桥价在午休冻结，采样只会写出与 11:30 相同的平线或残留下午价假折）。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 25 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60 + 10)


def load_state() -> dict:
    """上次完整分析的持仓基线（每次分析后更新）。"""
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(baseline: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError as exc:
        print(f"[{now_cn():%F %T}] 波动基线写入失败: {exc}")


def check_volatility(broker, positions: list) -> str | None:
    """波动触发检测（方案 C）：
      - 任一持仓盈亏% 较上次分析变化 ≥3pp（浮盈转亏/加速亏损都算）
      - 任一持仓个股当日涨跌首次达到 ±5%
    返回触发原因（供日志），未触发返回 None。节流：距上次分析 <20 分钟不触发。"""
    now = now_cn()
    state = load_state()
    last_ts = state.get("last_ts")
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts)
            if (now - dt).total_seconds() < MIN_ANALYSIS_INTERVAL_MIN * 60:
                return None  # 节流内，等整点分析
        except ValueError:
            pass
    rows = build_rows(broker, positions, load_names())
    if not rows:
        return None
    last_pnl = state.get("last_pnl") or {}
    last_day = state.get("last_day") or {}
    triggers = []
    for r in rows:
        prev_pnl = last_pnl.get(r["code"])
        if prev_pnl is not None and abs(r["pnl_pct"] - prev_pnl) >= TRIGGER_PNL_PP:
            triggers.append(f"{r['name']}({r['code']}) 盈亏 {prev_pnl:+.2f}%→{r['pnl_pct']:+.2f}%")
        if r["day_chg"] is not None:
            prev_day = last_day.get(r["code"])
            if (prev_day is None or abs(prev_day) < TRIGGER_DAY_CHG) and abs(r["day_chg"]) >= TRIGGER_DAY_CHG:
                triggers.append(f"{r['name']}({r['code']}) 今日涨跌 {r['day_chg']:+.2f}%")
    if not triggers:
        return None
    return "；".join(triggers[:5]) + ("…" if len(triggers) > 5 else "")


def run_analysis(broker, reason: str) -> int:
    """完整盘中分析（每小时 cron + 9:30 开盘 + 波动触发共用）：
    净值记录 → 各分账 agent 名下持仓 LLM 简评落盘 → 更新波动基线 state。"""
    now = now_cn()
    acct = broker._account_query()
    asset = float((acct.get("asset") or {}).get("asset") or 0)
    cash = float((acct.get("asset") or {}).get("cash") or 0)
    # 净值记录：每次分析一条（空仓也记，曲线不中断）
    record_equity(broker, asset, cash)
    positions = [p for p in (acct.get("positions") or [])
                 if float(p.get("total_volume") or 0) > 0]
    if not positions:
        print(f"[{now:%F %T}] 无实盘持仓，跳过")
        return 0

    names = load_names()
    rows = build_rows(broker, positions, names)

    # 每个分账 agent 只分析自己名下的持仓（ledger 归属），各自落盘（对话 tab 按模型切换）
    from live_ledger import agent_virtual_cash, load_ledger
    from live_trade_picks import enabled_agents

    ledger = load_ledger()
    ok = 0
    for agent in enabled_agents():
        rec = (ledger.get("agents") or {}).get(agent) or {}
        mine_codes = set((rec.get("positions") or {}).keys())
        my_rows = [r for r in rows if r["code"] in mine_codes]
        if not my_rows:
            print(f"[{now:%F %T}] {agent} 名下无持仓，跳过")
            continue
        virtual_cash = agent_virtual_cash(ledger, agent)
        virtual_asset = virtual_cash + sum(r["price"] * r["volume"] for r in my_rows)
        user_content = build_user_content(my_rows, virtual_asset, virtual_cash, agent)
        # 比赛配置多选：选中 N 个配置 → 本轮按 N 个配置各做一轮独立分析（各自落盘一轮对话）
        from prompts.analysis_modes import selected_modes

        for mode in selected_modes(agent):
            mode_prompt = mode["prompt"]
            if mode["id"] == "awareness":  # 情境感知: 注入今日排行榜上下文
                lb = build_leaderboard()
                if lb:
                    mode_prompt = mode_prompt + f"\n今日各 agent 虚拟净值排行榜（¥10 万起点）：\n{lb}"
            labeled_content = f"【分析配置：{mode['name']}】\n\n" + user_content
            usage = None
            try:
                content, usage = call_llm(labeled_content, agent, system_prompt_for(agent, {**mode, "prompt": mode_prompt}))
            except Exception as exc:  # noqa: BLE001
                print(f"[{now:%F %T}] {agent}·{mode['name']} LLM 调用失败: {exc}")
                content = ""
            if not content:
                # LLM 降级: 数据摘要（对话 tab 至少可看数据）—— 未调用 API，不计 token
                usage = None
                summary = [f"（{mode['name']} LLM 分析暂不可用，附实时数据）"]
                for r in my_rows:
                    summary.append(
                        f"- {r['name']} {r['code']}: 现价 ¥{r['price']} / 成本 ¥{r['cost']} "
                        f"/ {r['volume']}股 / 盈亏 ¥{r['pnl']:+,.0f}({r['pnl_pct']:+.2f}%)")
                content = "\n".join(summary)
            path = append_log(labeled_content, content, agent, usage)
            tok = f", token {usage.get('total_tokens')} (入{usage.get('prompt_tokens')}/出{usage.get('completion_tokens')})" if usage else ""
            print(f"[{now:%F %T}] {agent}·{mode['name']} 分析完成 {len(my_rows)} 只名下持仓{tok} → {path.relative_to(ROOT)}")
            ok += 1

    # 更新波动基线（全部持仓，跨 agent 汇总）
    save_state({
        "last_ts": now.isoformat(),
        "last_reason": reason,
        "last_pnl": {r["code"]: r["pnl_pct"] for r in rows},
        "last_day": {r["code"]: r["day_chg"] for r in rows if r["day_chg"] is not None},
    })
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="盘中实盘持仓分析（每小时）+ 净值采样（5 分钟）")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--record-only", action="store_true",
                        help="只采样净值（cron 每 5 分钟跑），不做 LLM 分析")
    args = parser.parse_args()

    now = now_cn()
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    if args.record_only:
        # 轻量净值采样：交易时段每分钟（cron * 9-15），只查账户+写文件
        if not args.force and not record_window(now):
            print(f"[{now:%F %T}] 非采样时段（9:25-15:10 工作日），跳过")
            return 0
        broker = TdxBridgeBroker()
        acct = broker._account_query()
        asset = float((acct.get("asset") or {}).get("asset") or 0)
        cash = float((acct.get("asset") or {}).get("cash") or 0)
        record_equity(broker, asset, cash)
        print(f"[{now:%F %T}] 净值采样完成 资产 ¥{asset:,.2f}")
        # 方案 C: 波动触发 — 交易时段内持仓盈亏 ±3pp 或个股涨跌 ±5% → 立即加跑完整分析
        if args.force or in_trading_window(now):
            positions = [p for p in (acct.get("positions") or [])
                         if float(p.get("total_volume") or 0) > 0]
            if positions:
                reason = check_volatility(broker, positions)
                if reason:
                    print(f"[{now:%F %T}] ⚡ 波动触发完整分析: {reason}")
                    return run_analysis(broker, f"波动触发: {reason}")
        return 0

    if not args.force and not in_trading_window(now):
        print(f"[{now:%F %T}] 非交易时段（北京 9:30-11:30/13:00-15:00 工作日），跳过")
        return 0

    broker = TdxBridgeBroker()
    return run_analysis(broker, "每小时定时")


if __name__ == "__main__":
    sys.exit(main())
