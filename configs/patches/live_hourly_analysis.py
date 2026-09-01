#!/usr/bin/env python3
"""盘中持股分析：交易时段每小时分析实盘持仓（通达信桥），写入模型对话日志。

- 数据: 桥 _account_query(持仓+实时价) + 日K昨收(涨跌) + quantdb/静态表(股票名称)
- 分析: 每个 enabled agent 各自逐只简评 + 操作建议（LLM 失败时降级为数据摘要，不崩溃）
- 落盘: data/agent_data_astock/deepseek-v4-flash/log/{北京日期}/log.jsonl (append)
        → ARENA(8092) 模型对话 tab 直接可读
- 执行: LLM 决策 JSON → 闸门校验 → 桥下单 → 分账记账（每 agent 每小时一轮；
        sell/buy 即时执行，watch 条件位交给 live_price_watch.py 分钟级哨兵）
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

# ---- 盘中执行参数（分析建议 → 实际买卖）----
# 与 live_llm_trade.py 保持同一套闸门口径：
#   sell: 只卖可卖量（T+1 当日买入跳过）、跌停不接、100 股整数倍
#   buy:  只买候选池内标的、涨停不追、单票 ≤ 剩余额度 20%、
#         子账户虚拟现金不透支（分账额度红线）、账户现金兜底
PER_STOCK_PCT = 0.2      # 单票买入 ≤ 剩余额度 20%
BUY_LIMIT_UP = 9.9       # 涨停不追
SELL_LIMIT_DOWN = -9.9   # 跌停不接
# 默认 dry-run（只打印决策不下单）；调用 --execute 才真下单

# 决策 JSON 格式（与 live_llm_trade.py 的 DECISION_SCHEMA 一致）
INTRA_DAY_SCHEMA = (
    '{"decisions": [{"action": "hold|sell|buy|watch", "code": "600519.SH", '
    '"pct": 0.2, "stop_loss": 1500.0, "take_profit": 1650.0, '
    '"reason": "一句话理由"}]}'
)



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
    """持仓 → 分析行: 名称/代码/现价/成本/数量/盈亏/盈亏%/今日涨跌/可卖量(T+1)。

    可卖量来自桥 available_volume：今日买入 T+1 不可卖 → 可卖量 0，
    供 agent 判断哪些持仓今天买入不能卖。"""
    rows = []
    for p in positions:
        code = p.get("stock_code") or ""
        if not code:
            continue
        cost = float(p.get("cost_price") or 0)
        price = float(p.get("last_price") or 0)
        volume = float(p.get("total_volume") or 0)
        avail = int(p.get("available_volume") or 0)
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
            "avail": avail,
        })
    return rows


def load_l2_factors(codes: list[str]) -> dict:
    """从 Quant-Trader api（nginx 8092 反代，自动注入 token）读 L2 因子，
    按持仓 code（后缀式，如 300308.SZ）匹配最近一条；失败返回空 dict（不阻塞分析）。"""
    import requests

    try:
        resp = requests.get(
            "http://127.0.0.1:8092/api/live/l2-factors", params={"limit": 500}, timeout=5
        )
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    if not (data or {}).get("success"):
        return {}
    out: dict = {}
    for r in (data.get("data") or []):
        code = (r.get("stock_code") or "").strip()
        if code and code not in out:
            out[code] = r
    return {c: out[c] for c in codes if c in out}


def load_news(codes: list[str], hours: int = 8, limit: int = 30) -> list:
    """盘中新闻（quantmind Huntly/RSS 聚合 + LLM 情感标注）→ 持仓相关条目。
    按 ticker 过滤 + 北京时间转换；失败返回空列表（不阻塞分析，同 load_l2_factors）。"""
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
        bj = ""
        try:
            ts = datetime.fromisoformat((a.get("published_at") or "").replace("Z", "+00:00"))
            bj = (ts + timedelta(hours=8)).strftime("%m-%d %H:%M")
        except ValueError:
            pass
        out.append({
            "title": str(a.get("title") or "").strip()[:120],
            "source": a.get("source_name") or "",
            "time": bj,
            "sentiment": label,
            "codes": [c for c in (en.get("tickers") or []) if c in codes],
        })
    return out[:limit]


def _fmt_factor(v) -> str:
    return "—" if v is None else f"{v:.3f}"


def build_user_content(rows: list, asset: float, cash: float, agent: str) -> str:
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（A股{('盘中' if in_trading_window(now_cn()) else '盘前/盘后')}）。"
        f"你是 {agent}（实盘分账账户，初始额度 ¥10 万）。你名下虚拟资产 ¥{asset:,.0f}、"
        f"可用虚拟现金 ¥{cash:,.0f}。你名下实盘持仓：",
        "",
        "| 股票 | 代码 | 现价 | 成本 | 数量 | 持仓金额 | 盈亏 | 盈亏% | 今日涨跌% | 可卖量 |",
        "|------|------|------|------|------|----------|------|-------|-----------|--------|",
    ]
    for r in rows:
        day = f"{r['day_chg']:+.2f}" if r["day_chg"] is not None else "—"
        lines.append(
            f"| {r['name']} | {r['code']} | {r['price']} | {r['cost']} | {r['volume']} "
            f"| ¥{r['price'] * r['volume']:,.0f} | ¥{r['pnl']:+,.0f} | {r['pnl_pct']:+.2f}% | {day} | {r['avail']} |"
        )
    lines += [
        "",
        "⚠️ T+1 规则：**可卖量为 0 的持仓 = 今日买入，今天不能卖出**；"
        "可卖量 = 总数量 的持仓是昨天或更早买入，可正常卖出。"
        "做减仓/清仓决策前先核对可卖量，不要对可卖量 0 的持仓给 sell。",
    ]
    # L2 微观结构因子（通达信实时采集，quantmind PG 同步）
    l2 = load_l2_factors([r["code"] for r in rows])
    if l2:
        lines += [
            "",
            "L2 微观结构因子（通达信实时采集，最近一次）：",
            "",
            "| 代码 | VPIN(量) | 分时区分布 | 价量背离 | 冲击半衰 | 资金流失衡 |",
            "|---|---|---|---|---|---|",
        ]
        for r in rows:
            row = l2.get(r["code"])
            if not row:
                continue
            f = row.get("factors") or {}
            lines.append(
                f"| {r['code']} | {_fmt_factor(f.get('micro_vpin_vol_ratio'))} | "
                f"{_fmt_factor(f.get('micro_zone_distribution'))} | "
                f"{_fmt_factor(f.get('vol_price_divergence'))} | "
                f"{_fmt_factor(f.get('micro_impact_decay_half_life'))} | "
                f"{_fmt_factor(f.get('flow_imbalance_revert_speed'))} |"
            )
        lines += [
            "",
            "说明：VPIN=委托量失衡（越高买方/卖方压力越强），分时区分布=成交时段集中度，"
            "价量背离=价格与成交量方向背离，冲击半衰=价格冲击衰减速度，资金流失衡=主动买卖失衡。"
            "L2 因子辅助判断盘中买卖压力，操作建议时可参考。",
        ]
    # 盘中新闻（Huntly/RSS 聚合 + LLM 情感标注），近 8 小时持仓相关
    news = load_news([r["code"] for r in rows])
    if news:
        tag = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
        lines += ["", "盘中新闻（近 8 小时，情感标注：利好/利空/中性）：", ""]
        for n in news:
            lines.append(
                f"- [{tag[n['sentiment']]}] {n['title']}（{n['source']} {n['time']}）")
    lines += [
        "",
        "请逐只给出：①一句话简评（行情/基本面/消息面角度）②操作建议（持有/加仓/减仓/止损）③理由。"
        "简评请结合上面的 L2 因子与盘中新闻——新闻明显利好/利空时要明确提示风险与机会。"
        "今天买入的股票 T+1 明天才能卖，建议时注意这一点。输出简洁 markdown，不用复述表格。",
        "",
        "【最后必须附一个 JSON 决策块】（紧跟在 markdown 之后，用 ```json 围栏包住，"
        "这是程序要执行的指令，务必真实反映你的操作建议）：",
        "```json",
        INTRA_DAY_SCHEMA,
        "```",
        "规则：sell 的 pct=卖出可卖量的比例（0~1，清仓=1.0）；buy 的 pct=使用剩余额度的比例（≤0.2）；"
        "watch=挂条件位：stop_loss=跌破此价自动减仓 pct 比例、take_profit=涨到此价自动止盈 pct"
        "（至少给一个，由分钟级价格哨兵实时监控执行，不用等下一个整点）；"
        "只对「②操作建议」为加仓/减仓/止损的持仓给出 sell/buy；"
        "持有但想设防守位/目标位的用 watch——简评里写了具体价位就必须挂上，否则不会被执行；其余一律 hold。",
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


def parse_intraday_decision(text: str) -> list | None:
    """LLM 输出 → 盘中决策列表（与 live_llm_trade.parse_decision 同构）。
    依次尝试：整段 JSON → ```json 围栏 → 括号平衡块；
    只解析 decisions 数组；未知 action 忽略。
    返回 [{"action","code","pct","stop_loss","take_profit","reason"}]
    （stop_loss/take_profit 仅 watch 有，其余 None），解析失败返回 None。"""
    import re

    if not text:
        return None

    def _num(x) -> float | None:
        """价位解析：None/空/N/A → None；非数字 → None（不炸）。"""
        if x is None or x in ("", "N/A"):
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    def _extract(payload: str) -> list | None:
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            return None
        out = []
        for x in (d.get("decisions") if isinstance(d, dict) else None) or []:
            action = (x.get("action") or "").lower()
            if action not in ("hold", "sell", "buy", "watch"):
                continue
            out.append({
                "action": action,
                "code": str(x.get("code") or "").strip(),
                "pct": float(x.get("pct") or 0),
                "stop_loss": _num(x.get("stop_loss")),
                "take_profit": _num(x.get("take_profit")),
                "reason": str(x.get("reason") or ""),
            })
        return out or None

    # 1) 整段即 JSON（模型只输出决策块）
    r = _extract(text.strip())
    if r:
        return r
    # 2) ```json 围栏
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        r = _extract(m.group(1).strip())
        if r:
            return r
    # 3) 括号平衡块：找所有 {…} 平衡片段，逐个尝试
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    r = _extract(text[i : j + 1])
                    if r:
                        return r
                    break
    return None


def execute_intraday_decision(broker, agent: str, decisions: list,
                              holdings: list, cash: float, dry_run: bool = True) -> list:
    """盘中决策 → 闸门校验 → 桥下单 → 分账记账 → 交易日志。

    与 live_llm_trade.py 同一套安全闸门：
      - sell: 只卖可卖量（T+1 当日买入跳过）、跌停不接、100 股整数倍
      - buy:  只买候选池内标的（此处不拉池，仅在已有持仓上加仓，避免盘中追新票）、
              涨停不追、单票 ≤ 剩余额度 20%、子账户虚拟现金不透支（分账额度红线）、
              账户现金兜底
    返回已执行/将执行的动作列表 [{action, code, volume, price, reason}]。
    """
    import time

    from live_ledger import (agent_remaining, agent_virtual_cash, load_ledger,
                             record_buy, record_sell, save_ledger)

    executed: list = []
    sells, buys = [], []
    for d in decisions:
        code = d["code"]
        h = next((x for x in holdings if x["code"] == code), None)
        if d["action"] == "sell":
            if not h:
                print(f"  ⚠️ [{agent}] 卖出 {code}: 非持仓，跳过")
                continue
            avail = h["avail"]
            if avail <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: T+1 不可卖（可卖量 0），跳过")
                continue
            vol = int(avail * min(max(d["pct"], 0), 1) / 100) * 100
            if vol <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 比例 {d['pct']:.0%} 不足 1 手，跳过")
                continue
            if h["day_chg"] is not None and h["day_chg"] <= SELL_LIMIT_DOWN:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 跌停（{h['day_chg']:+.2f}%），不接")
                continue
            sells.append((code, vol, d["reason"]))
            print(f"  📉 [{agent}] 卖出 {code} {vol}/{avail}股 ({d['pct']:.0%}): {d['reason']}")
        elif d["action"] == "buy":
            if not h:
                # 盘中不拉候选池：只允许对已有持仓加仓，避免盘中追新票
                print(f"  ⚠️ [{agent}] 买入 {code}: 非当前持仓（盘中仅支持持仓加仓），跳过")
                continue
            pct = min(max(d["pct"], 0), PER_STOCK_PCT)
            if pct <= 0:
                print(f"  ⏭️ [{agent}] 买入 {code}: pct=0，跳过")
                continue
            if h["day_chg"] is not None and h["day_chg"] >= BUY_LIMIT_UP:
                print(f"  ⏭️ [{agent}] 买入 {code}: 涨停（{h['day_chg']:+.2f}%），不追")
                continue
            buys.append((code, pct, d["reason"]))
            print(f"  📈 [{agent}] 买入 {code} 用剩余额度 {pct:.0%}（≤{PER_STOCK_PCT:.0%}）: {d['reason']}")

    if dry_run:
        for code, vol, _ in sells:
            executed.append({"action": "sell", "code": code, "volume": vol,
                             "price": 0, "reason": "dry-run"})
        for code, pct, _ in buys:
            executed.append({"action": "buy", "code": code, "volume": 0,
                             "price": 0, "pct": pct, "reason": "dry-run"})
        return executed

    # 执行：先卖后买（同 live_llm_trade 顺序）
    from live_trade_picks import compute_order

    for code, vol, reason in sells:
        try:
            klines = broker.get_klines(code, interval="daily")[-5:]
        except Exception:  # noqa: BLE001
            klines = []
        if len(klines) < 2:
            print(f"  ⚠️ [{agent}] 卖出 {code}: 行情不足，跳过")
            continue
        try:
            price = float(klines[-1].get("close") or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            continue
        limit = round(price * 0.99, 2)  # 限价卖：现价 -1%
        try:
            result = broker.sell(None, None, code, vol, price=limit)
            print(f"  ✅ [{agent}] 卖出 {code} 已受理: {result}")
            ledger = load_ledger()
            ledger = record_sell(ledger, agent, code, vol, limit, now_cn().isoformat())
            save_ledger(ledger)
            from live_trade_picks import log_line

            log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday", "agent": agent,
                      "code": code, "volume": vol, "price": limit, "result": result})
            executed.append({"action": "sell", "code": code, "volume": vol,
                             "price": limit, "reason": reason})
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
            from live_trade_picks import log_line

            log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday", "agent": agent,
                      "code": code, "volume": vol, "error": str(exc)})
        time.sleep(1)  # 桥限流

    ledger = load_ledger()
    for code, pct, reason in buys:
        remaining = agent_remaining(ledger, agent)
        try:
            klines = broker.get_klines(code, interval="daily")[-5:]
        except Exception:  # noqa: BLE001
            klines = []
        o = compute_order(klines, remaining, pct)
        if not o["ok"]:
            print(f"  ⏭️ [{agent}] 买入 {code}: {o['reason']}")
            continue
        # 分账额度红线：子 agent 买入不能超自己 ¥10 万虚拟子账户的现金
        # （remaining 是额度口径、cash 是桥总账户真实现金，两者都拦不住已实现亏损
        #   造成的透支——虚拟现金才是子账户真正买得起的钱）
        vcash = agent_virtual_cash(ledger, agent)
        if o["cost"] > vcash:
            print(f"  ⏭️ [{agent}] 买入 {code}: 子账户虚拟现金不足 "
                  f"¥{o['cost']:,.0f} > ¥{vcash:,.0f}（分账额度不透支，跳过）")
            continue
        if o["cost"] > cash:
            print(f"  ⏭️ [{agent}] 买入 {code}: 账户现金不足 ¥{o['cost']:,.0f} > ¥{cash:,.0f}")
            continue
        cash -= o["cost"]
        try:
            result = broker.buy(None, None, code, o["volume"], price=o["limit_price"])
            print(f"  ✅ [{agent}] 买入 {code} {o['volume']}股 "
                  f"限价 ¥{o['limit_price']:.2f} 已受理: {result}")
            ledger = record_buy(ledger, agent, code, o["volume"], o["price"],
                                now_cn().isoformat())
            save_ledger(ledger)
            from live_trade_picks import log_line

            log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday", "agent": agent,
                      "code": code, "volume": o["volume"], "price": o["price"],
                      "result": result})
            executed.append({"action": "buy", "code": code, "volume": o["volume"],
                             "price": o["price"], "reason": reason})
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
            from live_trade_picks import log_line

            log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday", "agent": agent,
                      "code": code, "error": str(exc)})
        time.sleep(1)
    return executed


def run_analysis(broker, reason: str, dry_run: bool = True) -> int:
    """完整盘中分析（每小时 cron + 9:30 开盘 + 波动触发共用）：
    净值记录 → 各分账 agent 名下持仓 LLM 简评落盘 → 解析决策（sell/buy）
    → 闸门校验 → 桥执行（dry_run=False 才真下单）→ 分账记账 → 更新波动基线 state。"""
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
    # 桥持仓含可卖量（T+1），供 sell 闸门
    avail_map = {p.get("stock_code"): int(p.get("available_volume") or 0)
                 for p in positions}
    holdings = [
        {"code": r["code"], "name": r["name"], "price": r["price"], "cost": r["cost"],
         "volume": r["volume"], "pnl_pct": r["pnl_pct"], "day_chg": r["day_chg"],
         "avail": avail_map.get(r["code"], 0)}
        for r in rows
    ]

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
        my_holdings = [h for h in holdings if h["code"] in mine_codes]
        virtual_cash = agent_virtual_cash(ledger, agent)
        virtual_asset = virtual_cash + sum(r["price"] * r["volume"] for r in my_rows)
        user_content = build_user_content(my_rows, virtual_asset, virtual_cash, agent)
        # 比赛配置多选：选中 N 个配置 → 本轮按 N 个配置各做一轮独立分析（各自落盘一轮对话）
        from prompts.analysis_modes import selected_modes

        agent_exec_done = False  # 每 agent 每小时只执行一轮（多模式多轮会叠加买入突破分账额度）
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

            # —— 盘中执行：解析 LLM 决策 → 闸门 → 桥下单（dry_run=False）——
            # 多模式只做一轮执行（首个含决策的模式）：分析可以多视角，执行权只有一个
            decisions = parse_intraday_decision(content)
            if not decisions:
                continue  # 该 mode 无结构化决策，不执行
            if agent_exec_done:
                continue
            agent_exec_done = True
            if not dry_run:
                # watch 条件位（跌破止损/到位止盈）→ 分钟级价格哨兵接管，不等整点；
                # 该 agent 的旧规则整组替换，最新分析说了算
                from live_price_watch import save_watch_rules

                n_watch = save_watch_rules(agent, decisions)
                if n_watch:
                    print(f"[{now:%F %T}] 👁️ [{agent}] 挂 {n_watch} 个 watch 条件位"
                          f"（分钟级哨兵监控）")
                exec_list = [d for d in decisions if d["action"] in ("sell", "buy")]
                if exec_list:
                    print(f"[{now:%F %T}] 🔴 [{agent}] {mode['name']} 决策执行：")
                    executed = execute_intraday_decision(broker, agent, exec_list,
                                                         my_holdings, cash, dry_run=dry_run)
                    if executed:
                        tag = "✅ 已执行"
                        acts = "/".join(f"{e['action']} {e['code']}" for e in executed)
                        print(f"[{now:%F %T}] {tag} [{agent}] {mode['name']}: "
                              f"{len(executed)} 笔（{acts}）")

    # 更新波动基线（全部持仓，跨 agent 汇总）
    save_state({
        "last_ts": now.isoformat(),
        "last_reason": reason,
        "last_pnl": {r["code"]: r["pnl_pct"] for r in rows},
        "last_day": {r["code"]: r["day_chg"] for r in rows if r["day_chg"] is not None},
    })
    return 0 if ok else 1


def intraday_exec_enabled() -> bool:
    """盘中自动执行开关：configs/intraday_exec.json {"enabled": true}。
    外部调度（cron/面板）不传 --execute 时，若开关开启则同样自动执行买卖。"""
    try:
        cfg = json.loads((ROOT / "configs" / "intraday_exec.json").read_text(encoding="utf-8"))
        return bool(cfg.get("enabled"))
    except (OSError, json.JSONDecodeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="盘中实盘持仓分析（每小时）+ 净值采样（5 分钟）")
    parser.add_argument("--force", action="store_true", help="忽略交易时段检查")
    parser.add_argument("--execute", action="store_true",
                        help="分析后真下单（默认 dry-run：只打印买卖决策不下单）")
    parser.add_argument("--record-only", action="store_true",
                        help="只采样净值（cron 每 5 分钟跑），不做 LLM 分析")
    args = parser.parse_args()

    now = now_cn()
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    # 配置开关：外部调度不传 --execute 时也可自动执行
    cfg_exec = intraday_exec_enabled()
    do_execute = args.execute or cfg_exec

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
                    return run_analysis(broker, f"波动触发: {reason}",
                                        dry_run=not do_execute)
        return 0

    if not args.force and not in_trading_window(now):
        print(f"[{now:%F %T}] 非交易时段（北京 9:30-11:30/13:00-15:00 工作日），跳过")
        return 0

    broker = TdxBridgeBroker()
    return run_analysis(broker, "每小时定时", dry_run=not do_execute)


if __name__ == "__main__":
    sys.exit(main())
