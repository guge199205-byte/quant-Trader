#!/usr/bin/env python3
"""港股盘中分析：交易时段每小时分析富途模拟账户持仓 + 候选池，写入模型对话日志。

- 账户: 富途 OpenD 模拟盘（经 BayMax /api/futu/*，nginx 8092 反代自动注入 token，
  脚本只走 HTTP，不依赖 futu SDK）
- 数据: account-both → SIM 持仓；snapshot → 现价/昨收（当日涨跌）；
  候选池 data/hk_picks.json（hk_picks.py 每日盘前产出）
- 分析: 每个 enabled agent（configs/hk_config.json）共享同一富途模拟账户视角，
  按 comp-config 选中配置各做一轮独立分析；无持仓时降级为候选池复盘（对话不断流）
- 落盘: data/agent_data_hk/{agent}/log/{北京日期}/log.jsonl (append)
        → ARENA(8092) 模型对话 tab（港股）直接可读
- 时段: 北京 9:30-12:00 / 13:00-16:00 工作日（港股交易时段，本机 JST 不依赖系统时区）

用法:
  python scripts/live_hourly_analysis_hk.py            # 交易时段才执行
  python scripts/live_hourly_analysis_hk.py --force    # 忽略时段检查（调试/补跑）
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

API = "http://127.0.0.1:8092"  # nginx 反代（自动注入 X-API-Token）
POOL_FILE = ROOT / "data" / "hk_picks.json"


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
    """港股交易日交易时段（北京）：9:30-12:00 / 13:00-16:00。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 12 * 60) or (13 * 60 <= hm <= 16 * 60)


def _to_futu_code(code: str) -> str:
    """00700.HK → HK.00700（富途代码格式）。"""
    return "HK." + code.split(".")[0].zfill(5)


def _api_get(path: str, params: dict | None = None, timeout: int = 20) -> dict:
    import requests

    resp = requests.get(f"{API}{path}", params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_futu_sim() -> dict:
    """富途模拟盘账户 {total_asset, cash, market_value, positions}；失败抛异常。"""
    data = _api_get("/api/futu/account-both", timeout=30)
    if not data.get("success"):
        raise RuntimeError(data.get("error") or "account-both 返回失败")
    sim = ((data.get("data") or {}).get("simulate")) or {}
    if not sim:
        raise RuntimeError("account-both 无 simulate 数据")
    return sim


def fetch_snapshot(futu_codes: list) -> dict:
    """实时快照 {HK.00700: {last_price, prev_close, day_chg, ...}}；失败返回空。"""
    if not futu_codes:
        return {}
    try:
        data = _api_get("/api/futu/snapshot", {"codes": ",".join(futu_codes)}, timeout=30)
    except Exception:  # noqa: BLE001
        return {}
    if not data.get("success"):
        return {}
    return (data.get("data") or {}).get("snapshot") or {}


def load_news(names: list, hours: int = 8, per_keyword: int = 5) -> list:
    """盘中新闻（quantmind Huntly/RSS 聚合，BayMax /api/live/news 代理）→ 港股关键词搜。
    港股无 ticker 标签库，按公司名关键词全文搜；失败返回空列表（不阻塞分析）。"""
    keywords = []
    for name in names:
        base = str(name).split("-")[0].strip()  # 阿里巴巴-SW → 阿里巴巴
        if base and base not in keywords:
            keywords.append(base)
    keywords.append("港股")  # 大盘面新闻兜底
    out, seen = [], set()
    from datetime import datetime, timedelta, timezone

    for kw in keywords[:5]:  # 最多 5 个关键词，避免拖慢分析
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            data = _api_get("/api/live/news", {"keyword": kw, "since": since,
                                               "limit": per_keyword, "hours": hours},
                            timeout=8)
        except Exception:  # noqa: BLE001
            continue
        for a in (((data.get("data") or {}).get("articles")) or []):
            title = str(a.get("title") or "").strip()[:120]
            if not title or title in seen:
                continue
            seen.add(title)
            label = ((a.get("enrichment") or {}).get("sentiment_label") or "neutral").lower()
            if label not in ("bullish", "bearish", "neutral"):
                label = "neutral"
            bj = ""
            try:
                ts = datetime.fromisoformat((a.get("published_at") or "").replace("Z", "+00:00"))
                bj = (ts + timedelta(hours=8)).strftime("%m-%d %H:%M")
            except ValueError:
                pass
            out.append({"title": title, "source": a.get("source_name") or "",
                        "time": bj, "sentiment": label, "keyword": kw})
    return out[:15]


def load_pool() -> dict:
    """候选池 {date, market_direction, picks: [...]}；缺失/过期返回空 dict。"""
    if not POOL_FILE.is_file():
        return {}
    try:
        doc = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    today = now_cn().strftime("%Y-%m-%d")
    if doc.get("date") != today:
        return {}  # 隔日池作废（hk_picks.py 盘前重算）
    return doc if doc.get("picks") else {}


def build_rows(sim: dict) -> list:
    """富途 SIM 持仓 → 分析行（现价/成本/盈亏/当日涨跌，价取快照优先）。"""
    snaps = fetch_snapshot([c for c in (sim.get("positions") or {}) if c])
    rows = []
    for code, p in (sim.get("positions") or {}).items():
        volume = float(p.get("volume") or 0)
        if volume <= 0:
            continue
        cost = float(p.get("cost") or 0)
        price = float(p.get("price") or 0)
        snap = snaps.get(code) or {}
        if snap.get("last_price"):
            price = float(snap["last_price"])
        pnl = (price - cost) * volume
        pnl_pct = (price - cost) / cost * 100 if cost else 0.0
        day_chg = round(float(snap.get("day_chg") or 0), 2) if snap else None
        rows.append({
            "code": code, "name": p.get("name") or code,
            "price": round(price, 2), "cost": round(cost, 2), "volume": int(volume),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "day_chg": day_chg,
        })
    return rows


def build_user_content(rows: list, asset: float, cash: float, agent: str, pool: dict) -> str:
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（港股{('盘中' if in_trading_window(now_cn()) else '盘前/盘后')}）。"
        f"你是 {agent}。你们共用一个富途港股模拟账户：总资产 HK${asset:,.0f}、"
        f"可用现金 HK${cash:,.0f}。",
    ]
    if rows:
        lines += [
            "当前持仓：",
            "",
            "| 股票 | 代码 | 现价 | 成本 | 数量 | 市值 | 盈亏 | 盈亏% | 今日涨跌% |",
            "|------|------|------|------|------|------|------|-------|-----------|",
        ]
        for r in rows:
            day = f"{r['day_chg']:+.2f}" if r["day_chg"] is not None else "—"
            lines.append(
                f"| {r['name']} | {r['code']} | {r['price']} | {r['cost']} | {r['volume']} "
                f"| HK${r['price'] * r['volume']:,.0f} | HK${r['pnl']:+,.0f} "
                f"| {r['pnl_pct']:+.2f}% | {day} |"
            )
    else:
        lines.append("当前无持仓。")
    if pool:
        lines += [
            "",
            f"候选池（{pool.get('date')} 动量评分 top {len(pool.get('picks') or [])}，"
            f"大盘方向 {pool.get('market_direction')}）：",
            "",
            "| 股票 | 代码 | 现价 | 20日动量% | 60日动量% | 评分 |",
            "|------|------|------|-----------|-----------|------|",
        ]
        for p in (pool.get("picks") or [])[:15]:
            lines.append(
                f"| {p.get('name')} | {p.get('code')} | {p.get('last_close')} "
                f"| {p.get('mom20', 0):+.1f} | {p.get('mom60', 0):+.1f} | {p.get('score')} |"
            )
    news = load_news([r["name"] for r in rows])
    if news:
        tag = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
        lines += ["", "盘中新闻（近 8 小时，情感标注）：", ""]
        for n in news:
            lines.append(f"- [{tag[n['sentiment']]}] {n['title']}（{n['source']} {n['time']}）")
    if rows:
        lines += [
            "",
            "请逐只给出：①一句话简评（行情/基本面/消息面角度）②操作建议（持有/加仓/减仓/止损）③理由。",
        ]
    else:
        lines += [
            "",
            "请从候选池挑 3 只最值得关注的：①为什么关注 ②建议的观察/介入价位 ③主要风险。",
        ]
    lines.append("结合候选池与新闻，可顺带评估是否值得用现金换仓。输出简洁 markdown，不用复述表格。")
    return "\n".join(lines)


def call_llm(user_content: str, model: str, system_prompt: str | None = None) -> tuple[str, dict | None]:
    """OpenAI 兼容接口调用指定模型；失败抛异常。返回 (content, usage)。"""
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
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt or ""},
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


def system_prompt_for(model: str, mode: dict) -> str:
    """系统提示词：港股分析师人设 + 市场规则 + 本次配置要求。"""
    from prompts.analysis_modes import market_rules

    base = (f"你是 {model} 模型驱动的港股交易助手盘中持仓分析师（富途模拟账户）。"
            "分析冷静客观，给可执行的操作建议。输出中文 markdown。"
            f"\n交易规则：{market_rules('hk')}")
    return base + f"\n\n【本次分析配置：{mode['name']}】\n{mode['prompt']}"


def append_log(user_content: str, content: str, sig: str, usage: dict | None = None) -> Path:
    """写入模型对话日志(agent_data_hk/{sig}/log/{date}/log.jsonl)，与 A股 同构。"""
    now = now_cn()
    log_dir = ROOT / Path("data/agent_data_hk") / sig / "log" / now.strftime("%Y-%m-%d")
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


def _tiger_symbol(code: str) -> str:
    """HK.00700 → 00700（Tiger 用纯代码）。"""
    return str(code or "").split(".")[-1]


def _tiger_creds() -> dict:
    """老虎凭据：config/brokers.json tiger 段（UI 设置页保存）。"""
    try:
        data = json.loads((ROOT / "config" / "brokers.json").read_text(encoding="utf-8"))
        return (data or {}).get("tiger") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _hk_log(rec: dict) -> None:
    """港股成交日志 → logs/live_trade_hk_YYYYMMDD.jsonl。"""
    from datetime import datetime

    LOG_DIR = ROOT / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"live_trade_hk_{datetime.now():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def execute_hk_decisions(agent: str, decisions: list, dry_run: bool = True) -> list:
    """港股决策 → 闸门（HK 规则）→ Tiger 下单 → hk_ledger 记账。

    与 A股闸门同构，HK 差异：
      - T+0：当天买入当天可卖（无 T+1 可卖量限制）
      - 无涨跌停板（HK 无此限制）
      - 单票 ≤ 剩余额度 20%；虚拟现金红线（per-agent HK$10 万）
      - 执行依据 Tiger 实盘持仓（分析表是富途 SIM，执行以 Tiger 为准）
      - v1 只做已有持仓加仓/减仓（不建新仓），100 股整数倍
    """
    import time

    from hk_ledger import (agent_remaining, agent_virtual_cash, ensure_agent,
                           load_ledger, record_buy, record_sell, save_ledger)
    from agent_tools.brokers.tiger_bridge import TigerBridgeBroker

    ledger = ensure_agent(load_ledger(), agent)
    try:
        broker = TigerBridgeBroker(_tiger_creds())
        positions = broker.get_positions(None, "", market="hk")
        cash = broker.get_cash(None, "")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ [{agent}] Tiger 不可用（检查 brokers.json tiger 凭据）: {exc}")
        return []
    executed: list = []
    for d in decisions or []:
        action = d.get("action")
        if action not in ("sell", "buy"):
            continue
        code = str(d.get("code") or "")
        sym = _tiger_symbol(code)
        pos = positions.get(sym) or positions.get(code)
        if action == "sell":
            if not pos:
                print(f"  ⏭️ [{agent}] 卖出 {code}: Tiger 实盘无持仓，跳过")
                continue
            vol = int(float(pos.get("volume") or 0) * min(max(d.get("pct", 0), 0), 1) / 100) * 100
            if vol <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 比例不足 1 手，跳过")
                continue
            print(f"  📉 [{agent}] 卖出 {code} {vol}股（HK T+0）: {d.get('reason', '')}")
            if dry_run:
                executed.append({"action": "sell", "code": code, "volume": vol,
                                 "price": 0, "reason": "dry-run"})
                continue
            try:
                result = broker.sell(None, "", sym, vol)
                print(f"  ✅ [{agent}] 卖出 {code} 已受理: {result}")
                # 记账价用 Tiger 现价兜底成本价（成交回报在 Tiger 侧，简化 v1）
                price = float(pos.get("cost_price") or 0)
                try:
                    q = broker.get_quote(sym, "", market="hk") or {}
                    price = float(q.get("buy price") or price)
                except Exception:  # noqa: BLE001
                    pass
                ledger = record_sell(load_ledger(), agent, code, vol, price,
                                     now_cn().isoformat())
                save_ledger(ledger)
                _hk_log({"ts": now_cn().isoformat(), "mode": "execute_hk", "agent": agent,
                         "code": code, "side": "sell", "volume": vol, "price": price,
                         "result": result})
                executed.append({"action": "sell", "code": code, "volume": vol,
                                 "price": price, "reason": d.get("reason", "")})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
                _hk_log({"ts": now_cn().isoformat(), "mode": "execute_hk", "agent": agent,
                         "code": code, "side": "sell", "volume": vol, "error": str(exc)})
            time.sleep(1)  # Tiger 限流礼让
        elif action == "buy":
            if not pos:
                print(f"  ⏭️ [{agent}] 买入 {code}: HK v1 仅支持已有持仓加仓，跳过")
                continue
            pct = min(max(d.get("pct", 0), 0), 0.2)
            if pct <= 0:
                continue
            vcash = agent_virtual_cash(ledger, agent)
            remaining = agent_remaining(ledger, agent)
            try:
                q = broker.get_quote(sym, "", market="hk") or {}
                price = float(q.get("buy price") or 0)
            except Exception:  # noqa: BLE001
                price = 0.0
            if price <= 0:
                print(f"  ⏭️ [{agent}] 买入 {code}: 取价失败，跳过")
                continue
            budget = min(remaining, vcash) * pct  # 双口径取小（额度 + 虚拟现金红线）
            vol = int(budget / price / 100) * 100
            if vol <= 0:
                print(f"  ⏭️ [{agent}] 买入 {code}: 预算 HK${budget:,.0f} 不足 1 手，跳过")
                continue
            cost = vol * price
            if cost > cash:
                print(f"  ⏭️ [{agent}] 买入 {code}: Tiger 账户现金不足 HK${cost:,.0f} > HK${cash:,.0f}")
                continue
            print(f"  📈 [{agent}] 买入 {code} {vol}股 @HK${price:.2f}（≤20% 剩余额度）: {d.get('reason', '')}")
            if dry_run:
                executed.append({"action": "buy", "code": code, "volume": vol,
                                 "price": price, "reason": "dry-run"})
                continue
            try:
                result = broker.buy(None, "", sym, vol, price=round(price * 1.005, 2))
                print(f"  ✅ [{agent}] 买入 {code} 已受理: {result}")
                ledger = record_buy(load_ledger(), agent, code, vol, price,
                                    now_cn().isoformat())
                save_ledger(ledger)
                _hk_log({"ts": now_cn().isoformat(), "mode": "execute_hk", "agent": agent,
                         "code": code, "side": "buy", "volume": vol, "price": price,
                         "result": result})
                executed.append({"action": "buy", "code": code, "volume": vol,
                                 "price": price, "reason": d.get("reason", "")})
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
                _hk_log({"ts": now_cn().isoformat(), "mode": "execute_hk", "agent": agent,
                         "code": code, "side": "buy", "volume": vol, "error": str(exc)})
            time.sleep(1)
    return executed


def run_analysis(dry_run: bool = True) -> int:
    """完整盘中分析：取账户/快照/池 → 各 enabled agent 按选中配置分析落盘
    → 解析决策（sell/buy）→ 闸门 → Tiger 下单（dry_run=False 才真下单）→ hk_ledger 记账。"""
    now = now_cn()
    from prompts.analysis_modes import selected_modes

    try:
        sim = fetch_futu_sim()
    except Exception as exc:  # noqa: BLE001
        print(f"[{now:%F %T}] 富途账户查询失败: {exc}")
        return 1
    asset = float(sim.get("total_asset") or 0)
    cash = float(sim.get("cash") or 0)
    rows = build_rows(sim)
    pool = load_pool()
    print(f"[{now:%F %T}] 富途SIM 资产 HK${asset:,.0f} 持仓 {len(rows)} 只；"
          f"候选池 {'top ' + str(len(pool.get('picks') or [])) if pool else '无(今日未产出)'}")

    from hk_picks import hk_enabled_agents

    ok = 0
    for agent in hk_enabled_agents():
        user_content = build_user_content(rows, asset, cash, agent, pool)
        agent_decided = None
        for mode in selected_modes(agent, market="hk"):
            labeled_content = f"【分析配置：{mode['name']}】\n\n" + user_content
            usage = None
            try:
                content, usage = call_llm(labeled_content, agent,
                                          system_prompt_for(agent, mode))
            except Exception as exc:  # noqa: BLE001
                print(f"[{now:%F %T}] {agent}·{mode['name']} LLM 调用失败: {exc}")
                content = ""
            if not content:
                # LLM 降级: 数据摘要（对话 tab 至少可看数据）—— 未调用 API，不计 token
                usage = None
                summary = [f"（{mode['name']} LLM 分析暂不可用，附实时数据）"]
                for r in rows:
                    summary.append(
                        f"- {r['name']} {r['code']}: 现价 HK${r['price']} / 成本 HK${r['cost']}"
                        f" / {r['volume']}股 / 盈亏 HK${r['pnl']:+,.0f}({r['pnl_pct']:+.2f}%)")
                if pool:
                    top3 = (pool.get("picks") or [])[:3]
                    summary.append("候选池 top3: " + "、".join(
                        f"{p.get('name')}({p.get('code')})" for p in top3))
                content = "\n".join(summary)
            path = append_log(labeled_content, content, agent, usage)
            tok = (f", token {usage.get('total_tokens')}"
                   f" (入{usage.get('prompt_tokens')}/出{usage.get('completion_tokens')})") if usage else ""
            print(f"[{now:%F %T}] {agent}·{mode['name']} 分析完成"
                  f"（持仓 {len(rows)} 只）{tok} → {path.relative_to(ROOT)}")
            ok += 1
            agent_decided = content
        # —— 港股执行：解析决策 → 闸门 → Tiger 下单（每 agent 每轮只执行一次）——
        if agent_decided:
            from live_hourly_analysis import parse_intraday_decision

            decisions = parse_intraday_decision(agent_decided)
            if decisions:
                exec_list = [d for d in decisions if d["action"] in ("sell", "buy")]
                if exec_list:
                    executed = execute_hk_decisions(agent, exec_list, dry_run=dry_run)
                    if executed:
                        tag = "🟡 DRY-RUN 决策" if dry_run else "✅ 已执行"
                        acts = "/".join(f"{e['action']} {e['code']}" for e in executed)
                        print(f"[{now:%F %T}] {tag} [{agent}] 港股: "
                              f"{len(executed)} 笔（{acts}）")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="港股盘中持仓/候选池分析（每小时）")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--execute", action="store_true",
                        help="分析后经 Tiger 真下单（默认 dry-run 只打印决策）")
    parser.add_argument("--dry-run", action="store_true",
                        help="强制只打印不下单（覆盖配置开关；验证测试用）")
    args = parser.parse_args()

    now = now_cn()
    if not args.force and not in_trading_window(now):
        print(f"[{now:%F %T}] 非交易时段（北京 9:30-12:00/13:00-16:00 工作日），跳过")
        return 0
    # 配置开关：外部调度不传 --execute 时也可自动执行。
    # HK 用独立开关 hk_exec.json（默认关）——Tiger 实盘通道，凭据填好并手动
    # 打开前绝不自动下单（与 A股 intraday_exec.json 隔离）。
    def _hk_exec_enabled() -> bool:
        try:
            cfg = json.loads((ROOT / "configs" / "hk_exec.json").read_text(encoding="utf-8"))
            return bool(cfg.get("enabled"))
        except (OSError, json.JSONDecodeError):
            return False

    cfg_exec = _hk_exec_enabled()
    do_execute = args.execute or (cfg_exec and not args.force and not args.dry_run)
    if args.dry_run:
        do_execute = False
    return run_analysis(dry_run=not do_execute)


if __name__ == "__main__":
    sys.exit(main())
