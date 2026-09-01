#!/usr/bin/env python3
"""美股实盘分析执行（IBKR）：账户 → 每 agent 分析 → 决策 → 闸门 → IBKR 下单 → us_ledger 记账。

- 数据: IbkrBridgeBroker（凭据 config/brokers.json ib；宿主脚本强制 127.0.0.1）
- 时段: America/New_York 9:30-16:00 工作日（脚本内时区判断，cron 只给宽松窗口）
- 闸门(US): T+0（当天买当天可卖）、无涨跌停、1 股整数倍（不是 100 股）、
  虚拟现金红线($10k)、单票 ≤ 剩余额度 20%、账户现金兜底、取价失败跳过、限价单 ±1%
- 执行开关: configs/us_exec.json {"enabled": false}（默认关，仿 hk_exec）
- 记账: us_ledger.json + logs/live_trade_us_*.jsonl + 模型对话日志 agent_data_us/

用法:
  python scripts/live_hourly_analysis_us.py            # 美股时段单轮
  python scripts/live_hourly_analysis_us.py --force    # 忽略时段（测试）
  python scripts/live_hourly_analysis_us.py --execute  # 分析后真下单（默认 dry-run）
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
NY_TZ = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

AGENT_QUOTA = 10_000.0      # per-agent 虚拟美元
MAX_NEW_BUYS = 3            # 单轮建仓上限
PER_STOCK_PCT = 0.2         # 单票 ≤ 剩余额度 20%


def _load_dotenv() -> None:
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


def in_window(now_ny: datetime | None = None) -> bool:
    """美股交易时段（America/New_York 9:30-16:00 工作日）。"""
    now = now_ny or datetime.now(NY_TZ)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 930 <= hm <= 1600


def _broker():
    """按市场→交易所映射实例化（us=ibkr/tiger）；宿主脚本强制 127.0.0.1。"""
    try:
        data = json.loads((ROOT / "config" / "broker_market.json").read_text(encoding="utf-8"))
        brk = (data or {}).get("us", "ibkr")
    except (OSError, json.JSONDecodeError):
        brk = "ibkr"
    cfg = {}
    try:
        data2 = json.loads((ROOT / "config" / "brokers.json").read_text(encoding="utf-8"))
        key = "ib" if brk == "ibkr" else brk  # brokers.json 键是 ib，映射是 ibkr
        cfg = dict((data2 or {}).get(key) or {})
    except (OSError, json.JSONDecodeError):
        pass
    if brk == "tiger":
        from agent_tools.brokers.tiger_bridge import TigerBridgeBroker

        return TigerBridgeBroker(cfg)
    if brk == "futu":
        from futu_api_broker import FutuApiBroker

        return FutuApiBroker(market="us")
    from agent_tools.brokers.ibkr_bridge import IbkrBridgeBroker

    cfg["gateway_host"] = "127.0.0.1"
    return IbkrBridgeBroker(cfg)


# ---------- 账户/持仓 ----------

def fetch_account(broker) -> tuple[float, dict]:
    """IBKR 实盘账户：现金 + 持仓 {symbol: {volume, cost_price, market_value}}。"""
    cash = broker.get_cash(None, "")
    positions = broker.get_positions(None, "")
    return cash, positions


def _price_of(broker, symbol: str) -> float:
    """取价：get_quote（行情订阅后实时）→ 本地 quantus 日线 → IBKR 日线 → 0。"""
    try:
        q = broker.get_quote(symbol, "")
        if q and float(q.get("buy price") or 0) > 0:
            return float(q["buy price"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from local_klines import get_daily

        bars = get_daily(symbol, "us", days=3)
        if bars:
            return float(bars[-1]["close"])
    except Exception:  # noqa: BLE001
        pass
    try:
        bars = broker.get_klines(symbol, "", "", interval="daily")
        if bars:
            return float(bars[-1]["close"])
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def build_rows(broker, positions: dict) -> list:
    """持仓 → 分析行（现价/成本/盈亏/T+0 可卖=全部）。"""
    rows = []
    for sym, p in positions.items():
        vol = float(p.get("volume") or 0)
        cost = float(p.get("cost_price") or 0)
        price = float(p.get("market_value") or 0) / vol if vol else _price_of(broker, sym)
        if price <= 0:
            price = _price_of(broker, sym)
        pnl = (price - cost) * vol
        pnl_pct = (price / cost - 1) * 100 if cost else 0
        rows.append({"code": sym, "name": sym, "price": price, "cost": cost,
                     "volume": vol, "pnl": pnl, "pnl_pct": pnl_pct, "day_chg": None})
    return rows


# ---------- 美股新闻（quantmind Huntly/RSS 聚合 + 情感标注，同 A股源） ----------

def load_news(codes: list, hours: int = 12, limit: int = 15) -> list:
    """按美股代码（AAPL/0700.HK 等）查新闻；失败返回空（不阻塞分析）。"""
    import requests
    from datetime import datetime, timedelta, timezone

    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = requests.get(
            "http://127.0.0.1:8000/api/v1/news/articles",
            params={"tickers": ",".join(codes), "since": since,
                    "page_size": limit, "sort": "time_desc"},
            timeout=5,
        )
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in (data.get("articles") or []):
        en = a.get("enrichment") or {}
        label = (en.get("sentiment_label") or "neutral").lower()
        if label not in ("bullish", "bearish", "neutral"):
            label = "neutral"
        out.append({"title": str(a.get("title") or "").strip()[:140],
                    "source": a.get("source_name") or "",
                    "time": str(a.get("published_at") or "")[:16],
                    "sentiment": label})
    return out[:limit]


# ---------- 提示词 ----------

def load_pool() -> dict:
    """候选池（us_picks.json，date=美东今日 才有效）。"""
    try:
        doc = json.loads((ROOT / "data" / "us_picks.json").read_text(encoding="utf-8"))
        if doc.get("date") != datetime.now(NY_TZ).strftime("%Y-%m-%d"):
            return {}
        return doc if doc.get("picks") else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_flat_content(pool: dict, cash: float, agent: str) -> str:
    """空仓 agent 候选池复盘：从池里决定建仓（≤3 只）或继续空仓。"""
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（美股盘中）。你是 {agent}（美股实盘分账账户，"
        f"初始额度 ${AGENT_QUOTA:,.0f}）。你名下**没有持仓（空仓）**，可用现金 ${cash:,.0f}。",
        f"候选池（{pool.get('date')} 动量评分 top {len(pool.get('picks') or [])}，"
        f"大盘方向 {pool.get('market_direction')}）：",
        "",
        "| # | 代码 | score | 近20日 | 近60日 | 趋势 | 波动 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in pool.get("picks") or []:
        lines.append(
            f"| {p.get('rank', '')} | {p['code']} | {p.get('score', 0):.3f} "
            f"| {p.get('mom20', 0):+.2f}% | {p.get('mom60', 0):+.2f}% "
            f"| {p.get('trend', 0):+.2f}% | {p.get('vol20', 0):.2f}% |")
    lines += [
        "",
        "请给出：①一句话简评（结合候选池与美股新闻）②是否建仓及建哪些③理由。输出简洁 markdown。",
        "",
        "【最后必须附一个 JSON 决策块】（```json 围栏包住）：",
        "```json",
        '{"decisions": [{"action": "hold|buy", "code": "AAPL", "pct": 0.2, "reason": "一句话理由"}]}',
        "```",
        "规则：buy 的 pct=剩余额度比例（≤0.2），最多建仓 3 只；候选池整体不吸引人时"
        "全部 hold 保持空仓观望；你无持仓，不要输出 sell。",
    ]
    return "\n".join(lines)


def build_user_content(rows: list, cash: float, agent: str) -> str:
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（美股盘中，America/New_York {datetime.now(NY_TZ):%F %H:%M}）。"
        f"你是 {agent}（美股实盘分账账户，初始额度 ${AGENT_QUOTA:,.0f}）。"
        f"你名下虚拟资产 = 现金 ${cash:,.0f}（IBKR 实盘账户现金兜底）+ 持仓。名下实盘持仓：",
        "",
        "| 代码 | 现价 | 成本 | 数量 | 持仓金额 | 盈亏 | 盈亏% |",
        "|------|------|------|------|----------|------|-------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | ${r['price']:.2f} | ${r['cost']:.2f} | {r['volume']:,.0f} "
            f"| ${r['price'] * r['volume']:,.0f} | ${r['pnl']:+,.0f} | {r['pnl_pct']:+.2f}% |")
    # 美股新闻（近 12 小时，情感标注）
    news = load_news([r["code"] for r in rows])
    if news:
        tag = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
        lines += ["", "相关新闻（近 12 小时，情感标注：利好/利空/中性）：", ""]
        for n in news:
            lines.append(f"- [{tag[n['sentiment']]}] {n['title']}（{n['source']} {n['time']}）")
    lines += [
        "",
        "美股规则：T+0（当天可卖）、无涨跌停；账户 <$25k 有 PDT 日内交易限制（5 日 3 次），"
        "注意别频繁日内买卖。",
        "请逐只给出：①一句话简评（结合上面新闻）②操作建议（持有/加仓/减仓/止损）③理由。输出简洁 markdown。",
        "",
        "【最后必须附一个 JSON 决策块】（```json 围栏包住）：",
        "```json",
        '{"decisions": [{"action": "hold|sell|buy", "code": "AAPL", "pct": 0.2, "reason": "一句话理由"}]}',
        "```",
        "规则：sell 的 pct=持仓比例（清仓=1.0）；buy 的 pct=剩余额度比例（≤0.2，最多 3 只）；"
        "美股 1 股起买（不需要 100 股整手）。",
    ]
    return "\n".join(lines)


# ---------- LLM（与 A股/港股同源：glm 走智谱，其余走 deepseek） ----------

def call_llm(user_content: str, model: str) -> str:
    import requests

    if model.startswith("glm"):
        base = os.getenv("GLM_API_BASE", "").rstrip("/")
        key = os.getenv("GLM_API_KEY", "")
    else:
        base = os.getenv("OPENAI_API_BASE", "").rstrip("/")
        key = os.getenv("OPENAI_API_KEY", "")
    if not base or not key:
        raise RuntimeError("LLM 未配置")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "system", "content": "你是美股实盘交易助手，冷静客观，注意 PDT 与 T+0 规则。输出中文。"},
                           {"role": "user", "content": user_content}],
              "temperature": 0.3, "max_tokens": 3000},
        timeout=120)
    resp.raise_for_status()
    msg = (resp.json().get("choices") or [{}])[0].get("message", {}) or {}
    return str(msg.get("content") or "").strip() or str(msg.get("reasoning_content") or "").strip()


def append_log(user_content: str, content: str, agent: str) -> Path:
    """模型对话日志 → data/agent_data_us/{agent}/log/{date}/log.jsonl。"""
    now = now_cn()
    log_dir = ROOT / Path("data/agent_data_us") / agent / "log" / now.strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": now.isoformat(), "signature": agent,
                            "new_messages": [{"role": "user", "content": user_content},
                                             {"role": "assistant", "content": content}]},
                           ensure_ascii=False) + "\n")
    return path


# ---------- 执行（闸门 → IBKR 下单 → us_ledger 记账） ----------

def _us_log(rec: dict) -> None:
    LOG_DIR = ROOT / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"live_trade_us_{now_cn():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def execute_us_decisions(broker, agent: str, decisions: list, rows: list,
                         cash: float, dry_run: bool = True,
                         pool_codes: set | None = None) -> list:
    """美股决策 → 闸门 → IBKR 下单 → us_ledger 记账。

    闸门(US)：T+0 无 T+1、无涨跌停、1 股整数倍、虚拟现金红线、单票≤20%、
    账户现金兜底、取价失败跳过。
    """
    import time

    from us_ledger import (agent_remaining, agent_virtual_cash, ensure_agent,
                           load_ledger, record_buy, record_sell, save_ledger)
    from live_hourly_analysis import parse_intraday_decision  # noqa: F401  (格式同源)

    ledger = ensure_agent(load_ledger(), agent)
    held = {r["code"]: r for r in rows}
    executed: list = []
    new_buys = 0
    for d in decisions or []:
        action = d.get("action")
        if action not in ("sell", "buy"):
            continue
        code = str(d.get("code") or "").strip()
        if not code:
            continue
        if action == "sell":
            h = held.get(code)
            if not h:
                print(f"  ⏭️ [{agent}] 卖出 {code}: IBKR 无持仓，跳过")
                continue
            vol = int(float(h["volume"]) * min(max(d.get("pct", 0), 0), 1))
            if vol <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 比例不足 1 股，跳过")
                continue
            print(f"  📉 [{agent}] 卖出 {code} {vol}股（T+0）: {d.get('reason', '')}")
            if dry_run:
                executed.append({"action": "sell", "code": code, "volume": vol,
                                 "price": 0, "reason": "dry-run"})
                continue
            price = _price_of(broker, code)
            if price <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 取价失败，跳过")
                continue
            try:
                result = broker.sell(None, "", code, vol, price=round(price * 0.99, 2))
                print(f"  ✅ [{agent}] 卖出 {code} 已受理: {result}")
                ledger = record_sell(load_ledger(), agent, code, vol, price,
                                     now_cn().isoformat())
                save_ledger(ledger)
                _us_log({"ts": now_cn().isoformat(), "mode": "execute_us", "agent": agent,
                         "code": code, "side": "sell", "volume": vol, "price": price,
                         "result": result})
                executed.append({"action": "sell", "code": code, "volume": vol,
                                 "price": price, "reason": d.get("reason", "")})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
                _us_log({"ts": now_cn().isoformat(), "mode": "execute_us", "agent": agent,
                         "code": code, "side": "sell", "volume": vol, "error": str(exc)})
            time.sleep(1)
        elif action == "buy":
            pct = min(max(d.get("pct", 0), 0), PER_STOCK_PCT)
            if pct <= 0:
                continue
            vcash = agent_virtual_cash(ledger, agent)
            remaining = agent_remaining(ledger, agent)
            if held.get(code) is None:
                if new_buys >= MAX_NEW_BUYS:
                    print(f"  ⏭️ [{agent}] 买入 {code}: 单轮建仓已达上限 {MAX_NEW_BUYS} 只，跳过")
                    continue
                if pool_codes is not None and code not in pool_codes:
                    print(f"  ⏭️ [{agent}] 买入 {code}: 非持仓且不在候选池，跳过")
                    continue
            price = _price_of(broker, code)
            if price <= 0:
                print(f"  ⏭️ [{agent}] 买入 {code}: 取价失败，跳过")
                continue
            budget = min(remaining, vcash) * pct  # 双口径取小（额度 + 虚拟现金红线）
            vol = int(budget / price)
            if vol <= 0:
                print(f"  ⏭️ [{agent}] 买入 {code}: 预算 ${budget:,.0f} 不足 1 股，跳过")
                continue
            cost = vol * price
            if cost > cash:
                print(f"  ⏭️ [{agent}] 买入 {code}: IBKR 账户现金不足 ${cost:,.0f} > ${cash:,.0f}")
                continue
            if held.get(code) is None:
                new_buys += 1
            print(f"  📈 [{agent}] 买入 {code} {vol}股 @${price:.2f}（≤20% 剩余额度）: {d.get('reason', '')}")
            if dry_run:
                executed.append({"action": "buy", "code": code, "volume": vol,
                                 "price": price, "reason": "dry-run"})
                continue
            try:
                result = broker.buy(None, "", code, vol, price=round(price * 1.01, 2))
                print(f"  ✅ [{agent}] 买入 {code} 已受理: {result}")
                ledger = record_buy(load_ledger(), agent, code, vol, price,
                                    now_cn().isoformat())
                save_ledger(ledger)
                _us_log({"ts": now_cn().isoformat(), "mode": "execute_us", "agent": agent,
                         "code": code, "side": "buy", "volume": vol, "price": price,
                         "result": result})
                executed.append({"action": "buy", "code": code, "volume": vol,
                                 "price": price, "reason": d.get("reason", "")})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
                _us_log({"ts": now_cn().isoformat(), "mode": "execute_us", "agent": agent,
                         "code": code, "side": "buy", "volume": vol, "error": str(exc)})
            time.sleep(1)
    return executed


def us_enabled_agents() -> list:
    """configs/us_config.json 启用的 agent（默认 3 模型）。"""
    try:
        cfg = json.loads((ROOT / "configs" / "us_config.json").read_text(encoding="utf-8"))
        return list(cfg.get("enabled") or [])
    except (OSError, json.JSONDecodeError):
        return ["deepseek-v4-flash", "deepseek-v4-pro", "glm-5.3-flash"]


def run_analysis(dry_run: bool = True) -> int:
    """美股完整分析：账户 → 每 agent 分析 → 决策 → 闸门 → IBKR 下单（dry_run=False 才真下单）。"""
    now = now_cn()
    try:
        broker = _broker()
        cash, positions = fetch_account(broker)
    except Exception as exc:  # noqa: BLE001
        print(f"[{now:%F %T}] IBKR 账户查询失败: {exc}")
        return 1
    rows = build_rows(broker, positions)
    print(f"[{now:%F %T}] {broker.name} 现金 ${cash:,.2f} 持仓 {len(rows)} 只")
    pool = load_pool()
    if not rows and not pool:
        print(f"[{now:%F %T}] 无持仓且候选池未生成，跳过")
        return 0
    ok = 0
    for agent in us_enabled_agents():
        if rows:
            user_content = build_user_content(rows, cash, agent)
            pool_codes = None
        else:
            user_content = build_flat_content(pool, cash, agent)
            pool_codes = {p.get("code") for p in (pool.get("picks") or [])}
            print(f"[{now:%F %T}] {agent} 空仓，候选池复盘（可建仓 ${cash:,.0f}）")
        try:
            content = call_llm(user_content, agent)
        except Exception as exc:  # noqa: BLE001
            print(f"[{now:%F %T}] {agent} LLM 调用失败: {exc}")
            content = ""
        if not content:
            summary = [f"（{agent} LLM 分析暂不可用，附实时数据）"]
            for r in rows:
                summary.append(f"- {r['name']}: 现价 ${r['price']:.2f} / 成本 ${r['cost']:.2f}"
                               f" / {r['volume']:,.0f}股 / 盈亏 ${r['pnl']:+,.0f}({r['pnl_pct']:+.2f}%)")
            content = "\n".join(summary)
        path = append_log(user_content, content, agent)
        print(f"[{now:%F %T}] {agent} 分析完成（持仓 {len(rows)} 只）→ {path.relative_to(ROOT)}")
        ok += 1
        # —— 执行：解析决策 → 闸门 → IBKR 下单（每 agent 每轮一次）——
        from live_hourly_analysis import parse_intraday_decision

        decisions = parse_intraday_decision(content)
        if decisions:
            exec_list = [d for d in decisions if d["action"] in ("sell", "buy")]
            if exec_list:
                executed = execute_us_decisions(broker, agent, exec_list, rows,
                                                cash, dry_run=dry_run,
                                                pool_codes=pool_codes)
                if executed:
                    tag = "🟡 DRY-RUN 决策" if dry_run else "✅ 已执行"
                    acts = "/".join(f"{e['action']} {e['code']}" for e in executed)
                    print(f"[{now:%F %T}] {tag} [{agent}] 美股: {len(executed)} 笔（{acts}）")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="美股实盘分析执行（IBKR，每小时）")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--execute", action="store_true", help="分析后经 IBKR 真下单")
    parser.add_argument("--dry-run", action="store_true", help="强制只打印不下单")
    args = parser.parse_args()

    if not args.force and not in_window():
        print(f"[{now_cn():%F %T}] 非美股交易时段（NY 9:30-16:00 工作日），跳过")
        return 0

    def _us_exec_enabled() -> bool:
        try:
            cfg = json.loads((ROOT / "configs" / "us_exec.json").read_text(encoding="utf-8"))
            return bool(cfg.get("enabled"))
        except (OSError, json.JSONDecodeError):
            return False

    do_execute = args.execute or (_us_exec_enabled() and not args.force and not args.dry_run)
    if args.dry_run:
        do_execute = False
    return run_analysis(dry_run=not do_execute)


if __name__ == "__main__":
    sys.exit(main())
