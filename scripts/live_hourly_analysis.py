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
import fcntl
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent_tools"))
sys.path.insert(0, str(ROOT / "scripts"))

from trading_cal import is_trading_day, why_not  # noqa: E402
from ashare_rules import at_limit_down, at_limit_up, board_of  # noqa: E402
from live_fills import round_sell_qty  # noqa: E402
import live_quote  # noqa: E402 取价备胎唯一入口（Fuyao/腾讯），quote_fallback 委托它

# ---- 方案 C: 波动触发参数 ----
STATE_PATH = ROOT / "logs" / "live_analysis_state.json"  # 上次分析的持仓基线
# 状态文件的写锁：**独立于 live_analysis.lock**（整点轮整轮持有那把锁，而 record-only
# 采样每分钟都要写状态，复用会互相饿死）。从 STATE_PATH 派生 → 测试兜底网自动重定向。
STATE_LOCK_PATH = STATE_PATH.with_name(STATE_PATH.name + ".lock")
MIN_ANALYSIS_INTERVAL_MIN = 20  # 波动触发节流: 距上次完整分析不足 20 分钟不重复触发
TRIGGER_PNL_PP = 3.0            # 任一持仓盈亏% 较上次分析变化 ≥3pp → 触发
TRIGGER_DAY_CHG = 5.0           # 任一持仓个股当日涨跌 ≥5% → 触发
RECOVERY_DOWN_MIN = 10          # 断线恢复补跑: 错失 ≥10 个"应采样分钟"（午休/隔夜不计），
                                # 恢复首分钟即整窗口补跑（2026-09-07 加固：原判据依赖
                                # "上一分钟恰好记到 asset≤0"，硬断线崩溃留旧正值 →
                                # 短中断恢复后傻等到下一整点）
TRIGGER_SAME_DIR_COOLDOWN_MIN = 60  # 波动触发同向冷却: 同标的同向 60 分钟内不重复分析
# ---- 方案 C v2 触发源（2026-09-07）：板块联动 / L2 资金流 / 新闻持仓信号 ----
TRIGGER_SECTOR_PP = 3.0     # 持仓所属板块涨跌较上次分析变化 ≥3pp → 唤醒
TRIGGER_SECTOR_ABS = 5.0    # 或板块绝对涨跌 ≥5%（强势/弱势确认）
TRIGGER_INOUT_DELTA = 0.3   # 持仓内外盘失衡较上次分析变化 ≥0.3（-1..1 尺度）→ 唤醒
TRIGGER_NEWS_IMPACT = 1.0   # 新闻管线持仓信号新出现 |impact|≥1 → 唤醒

# ---- 锁仓降频（2026-09-08）：持仓全部 T+1 锁定且资金不足以建仓 → 整点轮跳过该 agent。
# 唤醒机制（波动/板块/L2/新闻）与尾盘轮不走此闸门，兜底不漏事；默认保守（宁多跑）。
LOCKED_SKIP_CASH = 2000    # 可用资金低于此值视为无法建仓（约最便宜一手×20%额度都难覆盖）
LOCKED_SKIP_BEFORE = 14 * 60 + 30  # 北京 14:30 前才允许跳过，尾盘轮永远全跑

# ---- 盘中执行参数（分析建议 → 实际买卖）----
# 与 live_llm_trade.py 保持同一套闸门口径：
#   sell: 只卖可卖量（T+1 当日买入跳过）、跌停不接、100 股整数倍
#   buy:  只买候选池内标的、涨停不追、单票 ≤ 剩余额度 20%、
#         子账户虚拟现金不透支（分账额度红线）、账户现金兜底
PER_STOCK_PCT = 0.2      # 单票买入 ≤ 剩余额度 20%
LEVERAGE_MAX = 1.5       # 杠杆硬约束：持仓市值 ≤ 权益(现金+市值)×1.5，超限强制减仓
LEVERAGE_TRIM_TO = 1.3   # 强制减仓目标：降到 1.3×
MAX_NEW_BUYS = 3         # 空仓 agent 单轮建仓上限（每只 ≤ 20% 剩余额度）


def _apply_risk_budget() -> None:
    """启动时读取风控档位覆盖常量（阶段4 meta-agent 输出；口径见 risk_budget_agent.load_limits）。
    文件缺失/损坏 → 保持模块默认。cron 每次调用都是新进程 → 每轮即最新预算。"""
    global LEVERAGE_MAX, PER_STOCK_PCT, MAX_NEW_BUYS, LEVERAGE_TRIM_TO
    try:
        from risk_budget_agent import load_limits

        lim = load_limits()
    except Exception:  # noqa: BLE001
        return
    LEVERAGE_MAX = lim.get("leverage_max", LEVERAGE_MAX)
    PER_STOCK_PCT = lim.get("per_stock_pct", PER_STOCK_PCT)
    MAX_NEW_BUYS = lim.get("max_new_buys", MAX_NEW_BUYS)
    LEVERAGE_TRIM_TO = lim.get("leverage_trim_to", LEVERAGE_TRIM_TO)


_apply_risk_budget()
FILL_POLL_TIMEOUT_S = 30 # 下单后等成交回报的轮询超时（限价单通常秒成）

# ---- 实时盘口（桥五档，弱 L2 信号）----
OB_IMB_STRONG = 0.30     # 五档失衡 ≥ ±30% → 强买/强卖信号
OB_IMB_WEAK = 0.15       # 五档失衡 ≥ ±15% → 偏买/偏卖信号
# 默认 dry-run（只打印决策不下单）；调用 --execute 才真下单

# 决策 JSON 格式（与 live_llm_trade.py 的 DECISION_SCHEMA 一致）
INTRA_DAY_SCHEMA = (
    '{"decisions": [{"action": "hold|sell|buy|watch", "code": "600519.SH", '
    '"name": "贵州茅台", "pct": 0.2, "stop_loss": 1500.0, '
    '"take_profit": 1650.0, "move_stop": 1520.0, '
    '"invalidation": "跌破1500或买入理由失效", "confidence": 0.8, '
    '"risk_amount": 2000.0, '
    '"reason": "一句话理由（必填：宏观/板块/技术证据+为什么现在动手）"}]}'
)



from live_prompt_context import (build_gap_note, build_trade_recap,  # noqa: E402
                             load_decision_scorecard, load_hypotheses_summary,
                             load_review_recap, parse_intraday_decision)


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
    """A股交易时段（北京）：9:30-11:30 / 13:00-15:00，且当日为交易日（日历感知）。

    法定节假日（含调休周末照交易所口径）由 configs/trading_days.json 判定，
    quantdb 每日同步只到昨天、覆盖不到今天，故盘中闸门必须以静态全年清单为主。"""
    if not is_trading_day(now.date()):
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
                price = quote_fallback(code)  # 桥瞬时不可用 → Fuyao → 腾讯
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


def self_minute_feats(code: str) -> dict:
    """TdxAiData 备胎：用自采分钟快照序列算 30min 动量 / 5min 波动。
    样本不足返回 {}（沿用现有降级）。"""
    try:
        rows = []
        for f in sorted((ROOT / "logs" / "min_snapshots").glob("*.jsonl"))[-2:]:
            for l in f.read_text(encoding="utf-8").splitlines():
                r = json.loads(l)
                if r.get("code") == code and r.get("px"):
                    rows.append(r)
    except (OSError, ValueError):
        return {}
    rows.sort(key=lambda r: r["ts"])
    if len(rows) < 8:
        return {}
    pxs = [float(r["px"]) for r in rows]
    out = {}
    try:
        from datetime import datetime as _dt, timedelta as _td

        t0 = _dt.fromisoformat(rows[-1]["ts"])
        base = None
        for r in rows:
            t = _dt.fromisoformat(r["ts"])
            if t0 - t >= _td(minutes=25):
                base = float(r["px"])
        if base:
            out["mom30m"] = round((pxs[-1] / base - 1) * 100, 2)
    except Exception:  # noqa: BLE001
        pass
    try:
        from datetime import datetime as _dt, timedelta as _td

        t0 = _dt.fromisoformat(rows[-1]["ts"])
        b5 = float(rows[-1].get("b5") or 0)
        if b5 <= 0:
            for r in rows:
                if t0 - _dt.fromisoformat(r["ts"]) >= _td(minutes=4):
                    b5 = float(r["px"])
                    break
        if b5 > 0:
            out["mom5m"] = round((pxs[-1] / b5 - 1) * 100, 2)
    except Exception:  # noqa: BLE001
        pass
    if len(pxs) >= 6:
        chgs = [pxs[i] / pxs[i - 1] - 1 for i in range(-5, 0)]
        out["vol5m"] = round((max(chgs) - min(chgs)) * 100, 2)
    try:
        last = rows[-1]
        ins, outs = float(last.get("inside") or 0), float(last.get("outside") or 0)
        if ins + outs > 0:
            out["inout"] = round((ins - outs) / (ins + outs), 3)
    except Exception:  # noqa: BLE001
        pass
    return out


L2_FRESH_MIN = 30  # L2 因子新鲜度：超出即视为陈旧丢弃（残留旧池票 ts 停在数天前）


def l2_fresh_filter(rows: dict, now: datetime) -> dict:
    """L2 因子新鲜度过滤：ts 距今 >L2_FRESH_MIN 分钟的丢弃（旧池票残留 ts
    停在数天前，直接进提示词会被当"新鲜微观结构"误用）。"""
    out: dict = {}
    for code, row in (rows or {}).items():
        try:
            ts = datetime.fromisoformat(str((row or {}).get("ts") or ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=CN_TZ)
            if (now - ts).total_seconds() <= L2_FRESH_MIN * 60:
                out[code] = row
        except (ValueError, TypeError):
            continue
    return out


def l2_prune_stale(factors: dict, codes: list) -> dict:
    """采集器落盘前清理：只保留当前 watchlist 的因子（旧池票残留在文件里
    会让下游误以为它们仍有实时微观结构数据）。"""
    keep = set(codes or [])
    return {c: v for c, v in (factors or {}).items() if c in keep}


def load_l2_factors(codes: list[str]) -> dict:
    """L2 因子：优先 BayMax 自有采集（data/l2_factors_live.json，live_l2_capture.py
    每 5 分钟一轮），无数据时回退 quantmind API（8092 反代注入 token）。
    按持仓 code（后缀式，如 300308.SZ）匹配；失败返回空 dict（不阻塞分析）。"""
    import requests

    try:
        from live_l2_capture import load_factors

        own = load_factors()
        if own:
            own = l2_fresh_filter(own, now_cn())
            return {c: own[c] for c in codes if c in own}
    except Exception:  # noqa: BLE001
        pass
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


def quote_fallback(code: str, ref: float = 0.0) -> float:
    """取价备胎：Fuyao 快照 → 腾讯行情（唯一实现在 live_quote，客户端按代码匹配）。
    用于桥单点瞬时不可用时让 agent 仍拿到实时价（交易闸门不受影响：桥未通不下单）。
    ref>0（通常传桥价）= 交叉参照：备胎与参照偏离 >40% 视为坏值丢弃。
    全源失败返回 0.0（沿用旧契约：调用方按 >0 判有效）。"""
    px, src = live_quote.fallback_quote(code, ref=ref or None)
    if px is None:
        return 0.0
    if src != "fuyao":      # 主源的备胎也降级了 → 留痕（来源可见性）
        print(f"[{now_cn():%F %T}] ⚠️ {code} 现价来源={src} 备胎（Fuyao 不可用）")
    return px


def load_orderbook(broker, codes: list) -> dict:
    """桥实时五档（弱 L2 信号）：{code: {bid1, bid1_v, ask1, ask1_v, imb1, imb5,
    spread, signal}}。单只失败跳过，不阻塞分析。

    imb1 = (买一量-卖一量)/(买一量+卖一量)，>0 买方堆积、<0 卖方堆积；
    imb5 = 五档合计同口径；spread = (卖一-买一)/中间价（bp）。
    """
    out: dict = {}
    for code in codes:
        try:
            res = broker.tdx_call("get_market_snapshot", {"stock_code": code})
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(res, dict):
            continue
        try:
            bp = [float(x) for x in res.get("Buyp") or []]
            bv = [float(x) for x in res.get("Buyv") or []]
            sp = [float(x) for x in res.get("Sellp") or []]
            sv = [float(x) for x in res.get("Sellv") or []]
        except (TypeError, ValueError):
            continue
        if not bp or not sp or not bv or not sv:
            continue
        b1, a1 = bp[0], sp[0]
        mid = (b1 + a1) / 2
        if b1 <= 0 or a1 <= 0 or mid <= 0:
            continue
        # 实时增量：隔夜跳空 / 5分钟涨速 / 量比(vs 5日均量)——便宜高价值
        def _f(key: str) -> float | None:
            try:
                v = float(res.get(key) or 0)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None

        last_close, open_px = _f("LastClose"), _f("Open")
        now_px, before5 = _f("Now"), _f("Before5MinNow")
        gap_pct = round((open_px / last_close - 1) * 100, 2) if open_px and last_close else None
        speed5 = round((now_px / before5 - 1) * 100, 2) if before5 and now_px else None
        vol_ratio = None
        try:
            k = broker.get_klines(code, interval="daily")
            vols = [float(x.get("volume") or 0) for x in (k or [])[-6:-1]]
            tvol = _f("Volume")
            if vols and tvol:
                avg5 = sum(vols) / len(vols)
                vol_ratio = round(tvol / avg5, 2) if avg5 > 0 else None
        except Exception:  # noqa: BLE001
            pass
        sum_b, sum_s = sum(bv[:5]), sum(sv[:5])
        imb1 = (bv[0] - sv[0]) / (bv[0] + sv[0]) if (bv[0] + sv[0]) > 0 else 0.0
        imb5 = (sum_b - sum_s) / (sum_b + sum_s) if (sum_b + sum_s) > 0 else 0.0
        if imb1 >= OB_IMB_STRONG:
            signal = "强买"
        elif imb1 >= OB_IMB_WEAK:
            signal = "偏买"
        elif imb1 <= -OB_IMB_STRONG:
            signal = "强卖"
        elif imb1 <= -OB_IMB_WEAK:
            signal = "偏卖"
        else:
            signal = "中性"
        out[code] = {"bid1": b1, "bid1_v": bv[0], "ask1": a1, "ask1_v": sv[0],
                     "imb1": round(imb1, 3), "imb5": round(imb5, 3),
                     "spread": round((a1 - b1) / mid * 100, 3), "signal": signal,
                     "gap_pct": gap_pct, "speed5": speed5, "vol_ratio": vol_ratio}
    return out


def _cross_agent_refs(last_decisions: dict, agent: str) -> str | None:
    """其他 agent 上轮决策摘要（共识参考注入提示词）+ 分歧检测（阶段3 v1）。"""
    lines = []
    mine = {}
    for d in ((last_decisions or {}).get(agent) or {}).get("decisions") or []:
        mine[str(d.get("code") or "")] = str(d.get("action") or "hold").lower()
    for other, rec in (last_decisions or {}).items():
        if other == agent or not (rec or {}).get("decisions"):
            continue
        acts = []
        for d in rec["decisions"][:4]:
            act = str(d.get("action") or "hold").upper()
            code = str(d.get("code") or "")
            pct = d.get("pct")
            extra = f"（{float(pct):.0%}）" if pct and act in ("SELL", "BUY", "WATCH") else ""
            acts.append(f"{act} {code}{extra}")
            # 分歧检测：同代码 sell vs buy/watch（与我相反）
            m = mine.get(code)
            if m and ((m == "sell" and act in ("BUY", "WATCH"))
                      or (m in ("buy", "watch") and act == "SELL")):
                lines.append(f"- ⚠️【分歧】{other} 对 {code} 方向与你相反（{m} vs {act.lower()}）："
                             "请在结论里明确回应其理由，证据覆盖则坚持，否则说明改变")
        lines.append(f"- {other}: {'、'.join(acts)}")
    return "\n".join(lines) if lines else None


def load_market_context() -> dict:
    """大盘/板块上下文（live_l2_capture 每 5 分钟写 data/market_snapshot.json）。"""
    try:
        d = json.loads((ROOT / "data" / "market_snapshot.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


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


def market_data_stale(broker) -> bool:
    """行情新鲜度探针：日K最后一根 bar 是否停在今天之前（北京）。

    2026-09-01 事故复盘：桥"假活"（HTTP 通、账户/快照全返 9/1 缓存）时
    LLM 还拿假"今日涨跌"做减仓推理——用沪深300 日K日期戳做硬闸，
    停更就禁止模型把价格/涨跌当决策依据。交易日盘中才判；周末/午休不误报。
    """
    import time

    from datetime import timedelta

    bj = now_cn()
    if not is_trading_day(bj.date()):
        return False
    mins = bj.hour * 60 + bj.minute
    if not ((9 * 60 + 25 <= mins < 11 * 60 + 30) or (13 * 60 <= mins < 15 * 60 + 10)):
        return False
    try:
        k = broker.tdx_call("get_market_data",
                            {"stock_list": ["000300.SH"], "period": "1d",
                             "count": 3, "dividend_type": "none"})
        bars = ((k or {}).get("Value") or {}).get("000300.SH") or {}
        dates = bars.get("Date") or []
        if not dates:
            return False
        last = str(dates[-1])
        anchor = bj.strftime("%Y%m%d")
        return last < anchor
    except Exception:  # noqa: BLE001 探针失败按新鲜处理（另有 alert 兜底）
        return False


def _holding_txt(pos: dict | None) -> str:
    """账本 buy_ts/last_ts → 持有账龄文案（给 AI 的仓位时间维度）。

    例：'09/01起4个交易日，最近加仓 09/03'；账本无记录返回 '—'。
    """
    from datetime import date as _date

    if not pos or not (pos.get("buy_ts") or pos.get("last_ts")):
        return "—"
    try:
        from trading_cal import trading_days_between

        bt = str(pos.get("buy_ts") or "")[:10]
        lt = str(pos.get("last_ts") or "")[:10]
        today = now_cn().date()
        if not bt:
            return f"最近操作 {lt[5:7]}/{lt[8:10]}"
        days = trading_days_between(_date.fromisoformat(bt), today)
        base = f"{bt[5:7]}/{bt[8:10]}建仓·{days}个交易日"
        if lt and lt != bt:
            base += f"（最近操作 {lt[5:7]}/{lt[8:10]}，含加减仓）"
        return base
    except Exception:  # noqa: BLE001
        return "—"


def build_user_content(rows: list, asset: float, cash: float, agent: str,
                       last_decisions: list | None = None,
                       orderbook: dict | None = None,
                       cross_refs: str | None = None,
                       stale: bool = False,
                       recap: str | None = None,
                       review: str | None = None,
                       pool: list | None = None) -> str:
    lines = [
        f"现在是北京时间 {now_cn():%F %T}（A股{('盘中' if in_trading_window(now_cn()) else '盘前/盘后')}）。"
        f"你是 {agent}（实盘分账账户，初始额度 ¥10 万）。你名下虚拟资产 ¥{asset:,.0f}、"
        f"可用虚拟现金 ¥{cash:,.0f}。你名下实盘持仓：",
        f"杠杆硬约束：持仓市值超过权益（现金+市值）×{LEVERAGE_MAX} 时系统会强制减仓，"
        "请优先自行降杠杆。",
        "表述要求：正文中股票一律用中文名称（首次可括注代码），决策 JSON 照 schema 填写。",
        *([recap] if recap else []),
        *([review] if review else []),
        "",
        "| 股票 | 代码 | 现价 | 成本 | 数量 | 持仓金额 | 盈亏 | 盈亏% | 今日涨跌% | 可卖量 | 持有 |",
        "|------|------|------|------|------|----------|------|-------|-----------|--------|------|",
    ]
    if stale:
        lines[0:0] = [
            "🚨🚨【数据新鲜度警告】系统检测到桥行情疑似停更（最新日K不是今天）：",
            "下方的现价/今日涨跌/盈亏**全部可能是陈旧缓存，禁止据此做任何买卖决策**；",
            "你只能基于持仓结构和风险约束（杠杆/集中度/可卖量）给出意见，",
            "并明确标注『行情停更待确认』。若必须行动，唯一允许的动作是 watch/观察。",
            "",
        ]
    from live_ledger import load_ledger

    try:
        _led_pos = (load_ledger().get("agents") or {}).get(agent, {}).get("positions") or {}
    except Exception:  # noqa: BLE001
        _led_pos = {}
    for r in rows:
        day = f"{r['day_chg']:+.2f}" if r["day_chg"] is not None else "—"
        lines.append(
            f"| {r['name']} | {r['code']} | {r['price']} | {r['cost']} | {r['volume']} "
            f"| ¥{r['price'] * r['volume']:,.0f} | ¥{r['pnl']:+,.0f} | {r['pnl_pct']:+.2f}% | {day} | {r['avail']} | {_holding_txt(_led_pos.get(r['code']))} |"
        )
    lines += [
        "",
        "⚠️ T+1 规则：**可卖量为 0 的持仓 = 今日买入，今天不能卖出**；"
        "可卖量 = 总数量 的持仓是昨天或更早买入，可正常卖出。"
        "做减仓/清仓决策前先核对可卖量，不要对可卖量 0 的持仓给 sell。",
        "允许换仓：同一轮可先 sell 再 buy（先卖后买，闸门按可卖量/额度校验）。"
        "买卖/换仓依据 = 信号分数 + 大盘盘面 + 板块主线 + 新闻分子综合判断，"
        "任何 HOLD/BUY 侧标签一律不作为依据。",
    ]
    # 事件风险警示（解禁窗口内/近期负面新闻）：持仓提示退出，候选提示别选
    # （闸门已硬拦买入，这里省一轮无效决策；2026-09-08 用户口径）
    # + 行业整体劣化（赛道级软提示，不硬拦；2026-09-11 用户口径）
    try:
        from live_prompt_context import industry_caution_block, risk_warning_block

        _nm = {r["code"]: r.get("name") for r in rows}
        _nm.update({p.get("code"): p.get("name") for p in (pool or [])})
        _hc = [r["code"] for r in rows]
        _pc = [p.get("code") for p in (pool or [])]
        rb = risk_warning_block(_hc, _pc, _nm)
        if rb:
            lines += [rb]
        ib = industry_caution_block(_hc, _pc, _nm)
        if ib:
            lines += ["", ib]
    except Exception:  # noqa: BLE001 风险清单/行业榜不可用不阻塞提示词
        pass
    # 候选池实时行情（非持仓）：换仓/新开仓的定价依据。此前持仓 agent 既拿不到
    # 池内价格、闸门也不放行非持仓买入 → "允许换仓"实际无法执行（2026-09-08 修复）
    if pool:
        from live_prompt_context import budget_filter_note, build_pool_quote_block

        qb = build_pool_quote_block(pool)
        if qb:
            lines += ["", "以下为候选池（**不是你的持仓**）——新开仓/换仓只能从池内选：", qb,
                      budget_filter_note(PER_STOCK_PCT)]
    # 上一轮建议（记忆一致性：防整点之间决策反复横跳）
    if last_decisions:
        lines += ["", "【上一轮你的建议（已按此执行或挂单）】", ""]
        for d in last_decisions:
            act = str(d.get("action") or "hold").upper()
            pct = d.get("pct")
            extra = f"（{float(pct):.0%}）" if pct and act in ("SELL", "BUY", "WATCH") else ""
            lines.append(f"- {act} {d.get('code', '')} {extra}: {d.get('reason', '')}")
        lines += [
            "",
            "一致性规则：与上一轮方向一致优先；反转必须有硬理由（价格/消息/因子突变），"
            "并在理由里写清楚；上一轮已卖出、现已不在持仓里的股票不要再输出 sell。",
        ]
    else:
        lines += ["", "（本轮为首次分析，无上一轮建议）"]
    # 其他 agent 上轮建议（共识参考：独立判断，但分歧明显时说明理由）
    if cross_refs:
        lines += ["", "【其他 agent 上轮建议（仅供参考，请独立判断；如明显分歧请说明理由）】", ""]
        lines.append(cross_refs)
    # 大盘 + 持仓相关板块（live_l2_capture 每 5 分钟采集）
    ctx = load_market_context()
    if ctx.get("indices"):
        lines += ["", "大盘（桥实时）：", ""]
        for code, idx in ctx["indices"].items():
            lines.append(f"- {idx.get('name', code)}: {idx.get('now')}（{idx.get('chg_pct'):+.2f}%）")
    sectors = ctx.get("sectors") or {}
    if sectors:
        lines += ["", "持仓相关板块：", ""]
        for bcode, s in list(sectors.items())[:5]:
            lines.append(f"- {s.get('name', bcode)}（{s.get('type', '')}）: {s.get('chg_pct'):+.2f}%")
    # L2 微观结构因子（通达信实时采集，盘中多批次；核心因子可能为 0/缺失）
    l2 = load_l2_factors([r["code"] for r in rows])
    if l2:
        lines += [
            "",
            "L2 微观结构因子（通达信盘中实时采集，最新批次；值为 0 或缺省的因子视为未算出，勿臆测）：",
            "",
            "| 代码 | VPIN(量) | 分时区分布 | 价量背离 | 冲击半衰 | 资金流失衡 |",
            "|---|---|---|---|---|---|",
        ]
        for r in rows:
            row = l2.get(r["code"])
            if not row:
                lines.append(f"| {r['code']} | （L2 数据缺失） |")
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
        # 分钟K特征 + 分笔失衡（TdxAiData 实时，持仓股）
        mf_lines = []
        for r in rows:
            rec = l2.get(r["code"]) or {}
            mf = rec.get("minute_feats") or {}
            if not mf:
                mf = self_minute_feats(r["code"])  # TdxAiData 备胎：自采分钟序列
            tk = rec.get("tick_imb") or {}
            if not mf and not tk:
                continue
            parts = []
            if mf.get("mom30m") is not None:
                parts.append(f"30min动量 {mf['mom30m']:+.2f}%")
            if mf.get("vol5m") is not None:
                parts.append(f"5min波动 {mf['vol5m']:.2f}%")
            if mf.get("mom5m") is not None:
                parts.append(f"5min动量 {mf['mom5m']:+.2f}%")
            if mf.get("inout") is not None:
                parts.append(f"内外盘失衡 {mf['inout']:+.2f}")
            if mf.get("vol_ratio") is not None:
                parts.append(f"量比 {mf['vol_ratio']}")
            if tk:
                parts.append(f"分笔 买{tk.get('buy', 0)}/卖{tk.get('sell', 0)}（失衡 {tk.get('ratio', 0):+.2f}）")
            mf_lines.append(f"- {r['code']}: {' · '.join(parts)}")
        if mf_lines:
            lines += ["", "分钟/分笔（TdxAiData 实时，近 12 根 5 分钟K + 近 100 笔）：", ""] + mf_lines
    else:
        lines += [
            "",
            "L2 微观结构因子：**本次数据缺失**（采集源未返回），不要用幻觉填充，"
            "简评以行情与新闻为准。",
        ]
    # 实时盘口（桥五档，弱 L2 信号）——买一堆积 vs 卖一堆积
    if orderbook:
        lines += [
            "",
            "实时盘口（通达信五档，桥实时采集）：",
            "",
            "| 代码 | 买一 价/量 | 卖一 价/量 | 五档失衡 | 信号 |",
            "|---|---|---|---|---|",
        ]
        for r in rows:
            ob = orderbook.get(r["code"])
            if not ob:
                continue
            lines.append(
                f"| {r['code']} | ¥{ob['bid1']:.2f} × {ob['bid1_v']:,.0f} "
                f"| ¥{ob['ask1']:.2f} × {ob['ask1_v']:,.0f} "
                f"| {ob['imb1']:+.2f}（五档 {ob['imb5']:+.2f}） | {ob['signal']} |"
            )
        # 实时增量一行：隔夜跳空 / 5分钟涨速 / 量比（vs 5日均量）
        lines.append("")
        for r in rows:
            ob = orderbook.get(r["code"])
            if not ob:
                continue
            gap = f"{ob['gap_pct']:+.2f}%" if ob.get("gap_pct") is not None else "—"
            spd = f"{ob['speed5']:+.2f}%" if ob.get("speed5") is not None else "—"
            vr = f"{ob['vol_ratio']}" if ob.get("vol_ratio") is not None else "—"
            lines.append(
                f"- {r['code']}: 隔夜跳空 {gap}（昨收→今开） · 5分钟涨速 {spd} · 量比 {vr}")
        lines.append("说明：跳空>2% 高开/低开注意追高风险；5分钟涨速>±1% 记为异动；量比>1.5 显著放量、<0.6 明显缩量。")
        lines += [
            "",
            "说明：五档失衡 = (买一量-卖一量)/(买一量+卖一量)，>0 买方堆积、<0 卖方堆积，"
            f"±{OB_IMB_STRONG:.0%} 以上为强信号；括号里是五档合计同口径。"
            "盘口只是弱 L2 辅助信号（L2 逐笔因子缺失时尤其有用），与 L2 因子、新闻结合判断。",
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
        "简评请结合上面的 L2 因子、实时盘口、大盘板块与盘中新闻——大盘/板块明显走弱时优先防守；"
        "新闻明显利好/利空时要明确提示风险与机会；"
        "盘口强买/强卖（五档失衡 ±30% 以上）时给出对应倾向但别只靠盘口下结论。"
        "今天买入的股票 T+1 明天才能卖，建议时注意这一点。输出简洁 markdown，不用复述表格。",
        "",
        "【最后必须附一个 JSON 决策块】（紧跟在 markdown 之后，用 ```json 围栏包住，"
        "这是程序要执行的指令，务必真实反映你的操作建议）：",
        "```json",
        INTRA_DAY_SCHEMA,
        "```",
        f"规则：sell 的 pct=卖出可卖量的比例（0~1，清仓=1.0）；buy 的 pct=使用剩余额度的比例"
        f"（≤{PER_STOCK_PCT:.0%}，当日新开仓 ≤{MAX_NEW_BUYS} 只）；"
        "ST/*ST/退市整理股与黑名单标的不可买入（闸门硬拦）；"
        "watch=挂条件位：stop_loss=跌破此价自动减仓 pct 比例、take_profit=涨到此价自动止盈 pct"
        "（至少给一个，由分钟级价格哨兵实时监控执行，不用等下一个整点）；"
        "move_stop=可选，价格**上触**该价后把 stop_loss 自动上移到该价（只用于浮盈锁利，"
        "必须高于当前 stop_loss，填低了会被忽略；下破触发请用 stop_loss，不要用 move_stop）；"
        "只对「②操作建议」为加仓/减仓/止损的持仓给出 sell/buy；"
        "持有但想设防守位/目标位的用 watch——简评里写了具体价位就必须挂上，否则不会被执行；其余一律 hold。",
    ]
    return "\n".join(lines)


def build_flat_content(pool: list, direction: dict, cash: float, agent: str,
                       compact: bool = False) -> str:
    """空仓 agent 的候选池复盘提示词：从池里决定是否建仓/建哪些（或继续空仓）。

    pool = load_pool(20) 返回的候选池行；方向 = picks.json 大盘方向。
    与 build_user_content 共用 INTRA_DAY_SCHEMA 决策块，规则按空仓状态裁剪。
    compact=True：候选池与今日此前轮次一致，只展示前 8 行 + 其余名单
    （dsh agent 有对话历史，整天重复整表是纯 token 浪费；池变化或当日首注给全表）。
    """
    from live_llm_trade import pool_rows

    lines = [
        f"现在是北京时间 {now_cn():%F %T}（A股盘中）。你是 {agent}（实盘分账账户，"
        f"初始额度 ¥10 万）。你名下**没有持仓（空仓）**，可用虚拟现金 ¥{cash:,.0f}。",
        "本轮任务：从候选池里决定是否建仓、建哪些（你也可以选择继续空仓观望）。",
        "",
        "候选池（按综合评分排序；score=综合分，fusion=融合分。"
        "池内没有方向标签——是否买入/换仓由你按 分数+大盘+板块+新闻分子 综合判断，"
        "任何 HOLD/BUY 侧标签一律不作为依据）：",
        "",
        "| # | 代码 | 名称 | 行业 | score | fusion | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    if compact and len(pool) > 8:
        lines += pool_rows(pool[:8])
        rest = "、".join(str(p.get("name") or p.get("code") or "") for p in pool[8:])
        lines.append(f"（其余 {len(pool) - 8} 只与今日此前轮次一致，按评分降序：{rest}）")
    else:
        lines += pool_rows(pool)
    # 资金量裁剪说明（与 09:35 主入口同一句）：池子已按该 agent 的单票预算剔除买不起的
    from live_prompt_context import budget_filter_note

    lines += ["", budget_filter_note(PER_STOCK_PCT)]
    # 池内实时价（桥口径）：无价 → 模型无法定价 → 只能空转/挂 watch（哨兵不执行买单）
    from live_prompt_context import (build_pool_quote_block,
                                     industry_caution_block, risk_warning_block)

    qb = build_pool_quote_block(pool)
    if qb:
        lines += ["", qb]
    # 事件风险警示：候选池里命中解禁/负面新闻的标的一律别选（闸门会毙掉）
    _nm = {p.get("code"): p.get("name") for p in pool}
    _pc = [p.get("code") for p in pool]
    rb = risk_warning_block(None, _pc, _nm)
    if rb:
        lines += [rb]
    # 行业整体劣化：赛道级软提示（不硬拦；2026-09-11 用户口径）
    ib = industry_caution_block(None, _pc, _nm)
    if ib:
        lines += ["", ib]
    if direction:
        lines += ["", f"大盘方向：{direction}", ""]
    ctx = load_market_context()
    if ctx.get("indices"):
        lines += ["", "大盘（桥实时）：", ""]
        for code, idx in ctx["indices"].items():
            lines.append(f"- {idx.get('name', code)}: {idx.get('now')}（{idx.get('chg_pct'):+.2f}%）")
    lines += [
        "",
        "⚠️ T+1 规则：今天买入的明天才能卖；涨停（≥9.9%）的不要追。",
        "",
        "请给出：①一句话简评（行情/基本面/消息面角度，结合候选池信息）②是否建仓及建哪些③理由。"
        "输出简洁 markdown，不用复述表格。",
        "",
        "【最后必须附一个 JSON 决策块】（紧跟在 markdown 之后，用 ```json 围栏包住，"
        "这是程序要执行的指令，务必真实反映你的操作建议）：",
        "```json",
        INTRA_DAY_SCHEMA,
        "```",
        f"规则：buy 的 pct=使用剩余额度的比例（≤{PER_STOCK_PCT:.0%}），"
        f"最多建仓 {MAX_NEW_BUYS} 只；ST/*ST/退市整理股与黑名单标的不可买入（闸门硬拦）；"
        "候选池整体都不吸引人时全部 hold 保持空仓观望（空仓不丢人，等更好的机会）；"
        "你没有持仓，不要输出 sell；不要 watch 没持有的股票（哨兵无法执行）。",
    ]
    return "\n".join(lines)


def agent_mode_for(agent: str) -> str:
    """agent 运行模式：configs/agent_mode.json {"mode": "llm"|"dsh", "dsh_agents": [...]}。
    dsh_agents 名单内的 agent 用 dsh（试点逐个切），其余用 mode 默认值。"""
    try:
        cfg = json.loads((ROOT / "configs" / "agent_mode.json").read_text(encoding="utf-8"))
        if agent in (cfg.get("dsh_agents") or []):
            return "dsh"
        mode = str(cfg.get("mode") or "llm").strip().lower()
        return mode if mode in ("llm", "dsh") else "llm"
    except (OSError, json.JSONDecodeError):
        return "llm"


def agent_model_for(agent: str) -> str:
    """dsh agent 的模型：agent_mode.json 的 models 表（默认 deepseek）。"""
    try:
        cfg = json.loads((ROOT / "configs" / "agent_mode.json").read_text(encoding="utf-8"))
        model = str((cfg.get("models") or {}).get(agent) or "deepseek").strip().lower()
        return model if model in ("deepseek", "glm") else "deepseek"
    except (OSError, json.JSONDecodeError):
        return "deepseek"


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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }
    last_exc: Exception | None = None
    for attempt in range(2):  # 供应商不稳定（智谱曾 90s 超时）：失败重试 1 次
        try:
            resp = requests.post(f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 json=payload, timeout=120)
            resp.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == 0:
                print(f"  ⚠️ {model} LLM 调用失败，重试 1 次: {exc}")
    else:
        raise last_exc  # type: ignore[union-attr]
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
    坏数据护栏（2026-09-04 净值深坑复盘）：
      - 仅交易时段落盘：盘后 --force 跑分析时桥返回过 -17% 的假资产，绝不入曲线
      - 桥返回资产 ≤0（断线）→ 总账户行跳过，宁缺毋滥
      - 持仓股全部取不到实时价 → 该 agent 行跳过（成本价假净值会造成平线段）
    """
    import fcntl

    now = now_cn()
    if not record_window(now):
        print(f"[{now:%F %T}] ⏭️ 非交易时段，跳过净值落盘（盘后假价不入曲线）")
        return
    date_key = now.strftime("%Y-%m-%d %H:%M")  # 分钟级采样，分钟级去重
    path = ROOT / "logs" / "live_equity.jsonl"
    lock_path = ROOT / "logs" / ".live_equity.lock"
    path.parent.mkdir(parents=True, exist_ok=True)

    # 先算好全部条目（含网络取价），锁内只做去重 + 落盘
    entries = []
    if asset > 0:
        entries.append(
            {"key": date_key, "agent": None, "date": now.strftime("%Y-%m-%d"),
             "ts": now.isoformat(), "value": round(asset, 2),
             "asset": round(asset, 2), "cash": round(cash, 2)})
    else:
        print(f"[{now:%F %T}] ⏭️ 净值采样跳过总账户：桥返回资产 {asset}（≤0 视为断线）")
    from live_ledger import agent_virtual_cash, load_ledger

    ledger = load_ledger()
    for agent, rec in (ledger.get("agents") or {}).items():
        mkt = 0.0
        n_pos = 0
        n_quoted = 0
        for code, p in (rec.get("positions") or {}).items():
            n_pos += 1
            price = float(p.get("cost_price") or 0)
            try:
                quote = broker.get_quote(code, "")
                px = float((quote or {}).get("close") or 0)
                if px > 0:
                    price = px
                    n_quoted += 1
            except Exception:  # noqa: BLE001
                pass
            mkt += price * float(p.get("volume") or 0)
        if n_pos and not n_quoted:
            print(f"[{now:%F %T}] ⏭️ 净值采样跳过 {agent}：持仓 {n_pos} 只全部取不到实时价")
            continue
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


def append_failure_log(agents: list | None, reason: str, exc: BaseException) -> None:
    """本轮分析失败时把原因写进各 agent 对话——前端对话 tab 直接可见，
    不再只留在 cron stdout（2026-09-09 桥崩溃循环：两轮分析静默失败，对话页空白）。
    只落对话、不写 state：失败不推进任何基线，下一轮照常重试。"""
    now = now_cn()
    try:
        if agents is None:
            from live_trade_picks import enabled_agents

            agents = enabled_agents()
    except Exception as e:  # noqa: BLE001 名单取不到也不能吞掉失败提示
        print(f"  ⚠️ 失败提示：agent 名单读取失败: {e}")
        agents = []
    detail = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:300]
    user_content = f"[{now:%F %T}] 盘中定时分析（{reason}）"
    content = "\n".join([
        f"⚠️ 本轮分析未执行（{reason}）。",
        "",
        f"原因：{detail}",
        "",
        "持仓与决策均未变更——等下一轮自动重试；若连续失败请检查通达信桥。",
    ])
    for agent in agents:
        try:
            append_log(user_content, content, agent)
        except Exception as e:  # noqa: BLE001 落盘失败不影响 cron 退出码
            print(f"  ⚠️ 失败提示落盘失败 [{agent}]: {e}")


def record_window(now: datetime) -> bool:
    """净值采样时段：9:25-11:30 / 13:00-15:10 交易日（跳过午休 11:31-12:59，
    桥价在午休冻结，采样只会写出与 11:30 相同的平线或残留下午价假折）。
    非交易日（周末/法定节假日）整体不采样：桥价冻结，采样只产生平线噪音，
    且波动触发判定只该发生在真实交易时段。"""
    if not is_trading_day(now.date()):
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


_STATE_LOCK_DEPTH = threading.local()


@contextmanager
def _state_lock(timeout: float = 3.0):
    """状态文件的跨进程写锁（flock）。yield True=可以继续写，False=超时没抢到。

    与 live_fills._file_lock 同款：同线程可重入（重入计数放 thread.local，线程之间
    仍各自持 fd 互斥），锁文件打不开时按**无锁继续**并告警——状态写不进去只影响
    节流/去重，不该反过来打断交易路径。
    """
    depth = getattr(_STATE_LOCK_DEPTH, "depth", 0)
    if depth:                      # 同线程嵌套：直接放行
        _STATE_LOCK_DEPTH.depth = depth + 1
        try:
            yield True
        finally:
            _STATE_LOCK_DEPTH.depth = depth
        return
    fh, locked = None, False
    try:
        STATE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = open(STATE_LOCK_PATH, "a+")
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
    except OSError as exc:
        print(f"[{now_cn():%F %T}] ⚠️ 状态锁不可用（{exc}），本次按无锁继续")
        yield True
        return
    finally:
        if not locked and fh is not None:
            fh.close()
    if not locked:
        yield False
        return
    _STATE_LOCK_DEPTH.depth = 1
    try:
        yield True
    finally:
        _STATE_LOCK_DEPTH.depth = 0
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _atomic_write_json(path: Path, doc: dict) -> None:
    """同目录 tmp + os.replace：读者永远看不到半写状态（旧实现是截断式 write_text）。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def update_state(updates: dict | None = None, mutate=None) -> dict:
    """状态文件的**唯一写入口**：锁内读-改-写 + 原子替换，返回写后的状态。

    2026-09-11 事故：旧 save_state 接受"调用方手里那整份字典"，每分钟的采样进程
    于是把**分析前**读到的快照整表写回，抹掉同一时段触发轮写的 last_trigger /
    last_news_key / last_ts → 20 分钟节流、60 分钟同向冷却、新闻 ts 去重全部失效
    （当天 78 轮完整分析、约 215 万 tokens）。

    现在调用方只提交**自己拥有的键**（updates 浅合并），嵌套字典的读-改-写用
    mutate(current)->dict 在锁内做。整表覆盖的入口不存在 = 这一类 bug 结构上不可能。

    写失败/抢锁超时只打印，不抛：状态丢了影响的是节流与去重，不该打断交易路径。
    """
    with _state_lock() as got:
        if not got:
            print(f"[{now_cn():%F %T}] ⚠️ 状态写入放弃（锁超时），键: "
                  f"{sorted((updates or {}).keys())}")
            return load_state()
        cur = load_state()
        new = dict(cur)
        if updates:
            new.update(updates)
        if mutate is not None:
            new = mutate(dict(new)) or new
        try:
            _atomic_write_json(STATE_PATH, new)
        except OSError as exc:
            print(f"[{now_cn():%F %T}] 波动基线写入失败: {exc}")
            return cur
        return new


# ---------- 断线恢复判据辅助（2026-09-07 加固，纯函数便于单测） ----------

def last_good_sample_ts(st: dict) -> str | None:
    """上次"桥数据好"（asset>0）的采样时刻。旧状态回退 last_sample_ts，
    但 asset≤0（桥假活/断线那分钟）不算好数据，不能当恢复起点。"""
    v = st.get("last_good_sample_ts")
    if v:
        return str(v)
    legacy_ts = st.get("last_sample_ts")
    if legacy_ts and float(st.get("last_sample_asset") or 0) > 0:
        return str(legacy_ts)
    return None


def missed_sample_minutes(last_good_ts: str | None, now: datetime) -> int:
    """断线判据（采样口径）：last_good 之后本应采样（record_window 时段）却无
    好数据的分钟数。午休 11:31-12:59 / 隔夜/非交易日不计——那不是断线，
    冻结的 last_good 跨午休不误报。坏输入返回 0（不触发补跑）。"""
    if not last_good_ts:
        return 0
    try:
        t = datetime.fromisoformat(str(last_good_ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=now.tzinfo)
    except (ValueError, TypeError):
        return 0
    if t >= now:
        return 0
    missed = 0
    cur = t + timedelta(minutes=1)
    while cur <= now:
        if record_window(cur):
            missed += 1
        cur += timedelta(minutes=1)
    return missed


# ---------- 记忆一致性：各 agent 上一轮解析出的决策 ----------

LAST_DECISIONS_FILE = ROOT / "data" / "agent_last_decisions.json"


def load_last_decisions() -> dict:
    """{agent: {"decisions": [...], "ts": iso}}——防整点之间决策反复横跳。"""
    try:
        d = json.loads(LAST_DECISIONS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_last_decisions(agent: str, decisions: list, ts: str) -> None:
    d = load_last_decisions()
    d[agent] = {"decisions": decisions, "ts": ts}
    tmp = LAST_DECISIONS_FILE.with_name(LAST_DECISIONS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(LAST_DECISIONS_FILE)


_LOCK_FH: list = []


def _try_lock() -> bool:
    """单实例锁：慢分析（LLM 120s 超时）可能跨分钟，防止并发分析互相覆盖基线/重复下单。"""
    import fcntl

    try:
        fh = open(ROOT / "logs" / "live_analysis.lock", "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _LOCK_FH.append(fh)  # 保持引用防 GC 释放锁
    return True


def sector_triggers(ctx: dict, last_sector_chg: dict, codes: list) -> list:
    """板块联动触发：持仓所属板块较上次分析变化 ≥3pp 或绝对 ≥5%。
    ctx = market_snapshot.json（indices/sectors/position_sectors）。纯函数可测。"""
    out = []
    pos_sec = (ctx or {}).get("position_sectors") or {}
    sectors = (ctx or {}).get("sectors") or {}
    for code in codes or []:
        bcode = pos_sec.get(code)
        if not bcode:
            continue
        sec = sectors.get(bcode) or {}
        chg = sec.get("chg_pct")
        if chg is None:
            continue
        prev = (last_sector_chg or {}).get(bcode)
        name = sec.get("name") or bcode
        if prev is not None and abs(chg - prev) >= TRIGGER_SECTOR_PP:
            out.append((code, "up" if chg > prev else "down",
                        f"板块联动：{name} {prev:+.1f}%→{chg:+.1f}%"))
        elif abs(chg) >= TRIGGER_SECTOR_ABS:
            out.append((code, "up" if chg > 0 else "down",
                        f"板块绝对涨跌：{name} {chg:+.1f}%"))
    return out


def l2_triggers(factors: dict, last_inout: dict, codes: list) -> list:
    """L2 资金流触发：持仓内外盘失衡较上次分析变化 ≥0.3（-1..1）。纯函数可测。"""
    out = []
    for code in codes or []:
        row = (factors or {}).get(code) or {}
        inout = ((row.get("factors") or {}).get("inout"))
        if inout is None:
            continue
        try:
            inout = float(inout)
        except (TypeError, ValueError):
            continue
        prev = (last_inout or {}).get(code)
        if prev is None:
            continue
        try:
            prev = float(prev)
        except (TypeError, ValueError):
            continue
        if abs(inout - prev) >= TRIGGER_INOUT_DELTA:
            out.append((code, "up" if inout > prev else "down",
                        f"资金流突变：内外盘失衡 {prev:+.2f}→{inout:+.2f}"))
    return out


def news_triggers(brief: dict, last_news_key: str) -> tuple:
    """新闻持仓信号触发：新分子中出现 |impact|≥TRIGGER_NEWS_IMPACT 的逐票结论。
    返回 (triggers, new_key)；brief 为空/无新内容返回 ([], last_news_key)。纯函数可测。"""
    ts = str((brief or {}).get("ts") or "")
    if not ts or ts == (last_news_key or ""):
        return [], (last_news_key or "")
    from news_brief import _list_of_dicts  # 局部导入防环

    out = []
    for h in _list_of_dicts((brief or {}).get("holdings")):
        try:
            impact = float(h.get("impact") or 0)
        except (TypeError, ValueError):
            continue
        if abs(impact) >= TRIGGER_NEWS_IMPACT:
            out.append((str(h.get("code") or ""), "up" if impact > 0 else "down",
                        f"新闻信号：{h.get('name')}({h.get('code')}) {h.get('verdict')} "
                        f"impact{impact:+.0f} · {h.get('event_type')}"))
    return out, ts


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
    triggers = []  # (code, dir, msg)
    for r in rows:
        prev_pnl = last_pnl.get(r["code"])
        if prev_pnl is not None and abs(r["pnl_pct"] - prev_pnl) >= TRIGGER_PNL_PP:
            triggers.append((r["code"], "down" if r["pnl_pct"] < prev_pnl else "up",
                             f"{r['name']}({r['code']}) 盈亏 {prev_pnl:+.2f}%→{r['pnl_pct']:+.2f}%"))
        if r["day_chg"] is not None:
            prev_day = last_day.get(r["code"])
            if (prev_day is None or abs(prev_day) < TRIGGER_DAY_CHG) and abs(r["day_chg"]) >= TRIGGER_DAY_CHG:
                triggers.append((r["code"], "down" if r["day_chg"] < 0 else "up",
                                 f"{r['name']}({r['code']}) 今日涨跌 {r['day_chg']:+.2f}%"))
    # —— 方案 C v2：板块联动 / L2 资金流 / 新闻持仓信号（各自独立降级，失败不阻塞）——
    codes = [r["code"] for r in rows]
    try:
        ctx = load_market_context()
        triggers += sector_triggers(ctx, state.get("last_sector_chg") or {}, codes)
    except Exception:  # noqa: BLE001
        pass
    try:
        from live_l2_capture import load_factors

        triggers += l2_triggers(load_factors(), state.get("last_inout") or {}, codes)
    except Exception:  # noqa: BLE001
        pass
    try:
        from news_brief import BRIEF_FILE

        brief = {}
        try:
            brief = json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            brief = {}
        news_hits, new_key = news_triggers(brief, state.get("last_news_key") or "")
        if news_hits:
            # 立即记账（防同一分子反复唤醒）；只写自己拥有的键（见 update_state）
            update_state({"last_news_key": new_key})
        triggers += news_hits
    except Exception:  # noqa: BLE001
        pass
    if not triggers:
        return None

    # 生产级防护①：坏 tick 校验——触发行的桥价与备胎源（Fuyao→腾讯）偏差 >2% 视为坏数据
    validated = []
    for code, dr, msg in triggers:
        px = next((r["price"] for r in rows if r["code"] == code and r.get("price")), 0)
        try:
            # ref=桥价：备胎与桥价差 >40% 时按"备胎坏值"丢弃（fb=0 → 不放行也不误杀，
            # 真伪由下面 ±2% 交叉校验继续判）。09-11 实录：备胎恒取 items[0]（平安银行）
            # 时这里反向把真触发整行丢掉（当日 156 条），修在 live_quote 的客户端匹配。
            fb = quote_fallback(code, px)
        except Exception:  # noqa: BLE001
            fb = 0.0
        px = next((r["price"] for r in rows if r["code"] == code and r.get("price")), 0)
        if fb > 0 and px > 0 and abs(fb - px) / max(px, 0.01) > 0.02:
            print(f"[{now:%F %T}] ⚠️ 波动触发丢弃（疑似坏 tick）：{msg}｜桥价 {px} vs 备胎 {fb}")
            continue
        validated.append((code, dr, msg))
    if not validated:
        return None

    # 生产级防护②：同标的同向触发冷却（60 分钟内不重复完整分析，价值密度低）
    lt = state.get("last_trigger") or {}
    kept, dropped = [], 0
    for code, dr, msg in validated:
        last = lt.get(code) or {}
        within = False
        if last.get("dir") == dr and last.get("ts"):
            try:
                within = (now - datetime.fromisoformat(last["ts"])).total_seconds() \
                    < TRIGGER_SAME_DIR_COOLDOWN_MIN * 60
            except ValueError:
                within = False
        if within:
            dropped += 1
            continue
        kept.append(msg)
        lt[code] = {"dir": dr, "ts": now.isoformat()}
    if dropped:
        print(f"[{now:%F %T}] ⏭️ 波动触发同向冷却丢弃 {dropped} 条"
              f"（{TRIGGER_SAME_DIR_COOLDOWN_MIN} 分钟内同向不重复）")
    if not kept:
        return None
    update_state({"last_trigger": lt})
    return "；".join(kept[:5]) + ("…" if len(kept) > 5 else "")


def daily_buy_codes(agent: str, day: str | None = None,
                    path: Path | None = None) -> set:
    """当日该 agent 已成交买入的代码集合（成交回报/定格记录）。

    风险预算口径是「**全天**新开仓 ≤ max_new_buys 只」，而闸门此前只按单轮计数，
    整点轮每小时都能再开一批——日级上限补上这个缺口（2026-09-08）。
    """
    day = day or now_cn().strftime("%Y-%m-%d")
    path = path or (ROOT / "logs" / f"live_trade_{day.replace('-', '')}.jsonl")
    out: set = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("error") or r.get("agent") != agent:
            continue
        if str(r.get("side") or "").lower() != "buy":
            continue
        code = str(r.get("code") or "")
        if not code:
            continue
        if r.get("mode") == "fill_confirm":
            vol = int(r.get("volume") or 0)
        else:
            vol = int((r.get("fill") or {}).get("filled_volume") or 0)
        if vol > 0:
            out.add(code)
    return out


def execute_intraday_decision(broker, agent: str, decisions: list,
                              holdings: list, cash: float, dry_run: bool = True,
                              pool_codes: set | None = None,
                              names: dict | None = None) -> list:
    """盘中决策 → 闸门校验 → 桥下单 → 分账记账（按真实成交价/量）→ 交易日志。

    与 live_llm_trade.py 同一套安全闸门：
      - sell: 只卖可卖量（T+1 当日买入跳过）、跌停不接、100 股整数倍、
              已有在途卖单不再重复下
      - buy:  持仓内加仓不限；非持仓标的必须是候选池成员（pool_codes）——持仓
              agent 同样放行（提示词承诺的"换仓"此前被闸门挡死，2026-09-08 修复）；
              新开仓 单轮 ≤ MAX_NEW_BUYS 且**当日累计** ≤ MAX_NEW_BUYS、
              涨停不追、单票 ≤ 剩余额度 PER_STOCK_PCT、子账户虚拟现金不透支
              （分账额度红线）、加仓后杠杆 ≤ LEVERAGE_MAX×权益、账户现金兜底、
              已有在途买单不再重复下、**循环熔断**（当日已实现亏损 ≥5% 日初权益或
              日内权益回撤 ≥6% → 该 agent 当日禁买，卖出照常；见 live_breaker）、
              **标的边界**（ST/*ST/退市整理股与操作员黑名单禁买；见 symbol_policy）
    - 成交回报：wait_fill 轮询桥当日委托，按真实 filled_price/filled_volume 记账；
      超时未确认挂 pending（live_fills.reconcile 兜底 ≤1 分钟）
    返回已执行/将执行的动作列表 [{action, code, volume, price, reason}]。
    names：代码→名称（load_names），标的边界判定用；缺省时仅能按持仓行名称识别。
    """
    import time

    from live_fills import add_pending, inflight_codes, wait_fill
    from live_ledger import (agent_remaining, agent_virtual_cash, load_ledger,
                             record_buy, record_sell, save_ledger)

    # 在途单闸门：有未确认成交的同代码单，不再重复下单（口径统一在 live_fills.inflight_codes）
    pending_sell = inflight_codes("sell")
    pending_buy = inflight_codes("buy")

    executed: list = []
    sells, buys = [], []
    new_buys = 0
    opened_today = daily_buy_codes(agent)  # 当日已开仓代码（日级上限口径）
    # 循环熔断（第二道防线）：当日已实现亏损/日内权益回撤超限 → 该 agent 当日禁买。
    # 上面六道闸门都是"单笔/单轮"口径，没有一条盯"今天已经亏了多少"（2026-09-08 补）
    from live_breaker import check_and_trip

    halt, halt_reason = check_and_trip(agent, persist=not dry_run)
    if halt:
        print(f"  🛑 [{agent}] 循环熔断：{halt_reason} → 今日禁止买入（卖出照常）")
    # 买入闸门（纯函数，与 09:35 开盘轮共用同一实现，防两处判定漂移）
    from buy_gate import BuyGate, check_buy
    from symbol_policy import load_policy_with_risk

    gate = BuyGate(pool_codes=frozenset(pool_codes or ()),
                   per_stock_pct=PER_STOCK_PCT, max_new_buys=MAX_NEW_BUYS,
                   halted=halt, halt_reason=halt_reason,
                   policy=load_policy_with_risk())  # 含解禁/负面新闻事件风险清单
    for d in decisions:
        code = d["code"]
        h = next((x for x in holdings if x["code"] == code), None)
        if d["action"] == "sell":
            if code in pending_sell:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 已有在途卖单未确认，跳过")
                continue
            if not h:
                print(f"  ⚠️ [{agent}] 卖出 {code}: 非持仓，跳过")
                continue
            avail = h["avail"]
            if avail <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: T+1 不可卖（可卖量 0），跳过")
                continue
            raw_vol = int(avail * min(max(d["pct"], 0), 1))
            vol = round_sell_qty(code, raw_vol, avail)
            if vol <= 0:
                print(f"  ⏭️ [{agent}] 卖出 {code}: 比例 {d['pct']:.0%} 无合法可卖量，跳过")
                continue
            if vol != raw_vol:
                print(f"  ⚖️ [{agent}] 卖出 {code}: 意图 {raw_vol} 股 → 手数合规实际 {vol} 股"
                      f"（{board_of(code)} 口径，集中度按实际成交更新）")
            if at_limit_down(code, h['day_chg'], h.get('name')):
                print(f"  ⏭️ [{agent}] 卖出 {code}: 跌停（{h['day_chg']:+.2f}%），不接")
                continue
            sells.append((code, vol, d["reason"], raw_vol))
            print(f"  📉 [{agent}] 卖出 {code} {vol}/{avail}股 ({d['pct']:.0%}): {d['reason']}")
        elif d["action"] == "buy":
            if code in pending_buy:
                print(f"  ⏭️ [{agent}] 买入 {code}: 已有在途买单未确认，跳过")
                continue
            bd = check_buy(code, d["pct"], bool(h), new_buys, opened_today, gate,
                           name=(h or {}).get("name") or (names or {}).get(code))
            if not bd.ok:
                print(f"  ⏭️ [{agent}] 买入 {code}: {bd.reason}，跳过")
                continue
            new_buys = bd.new_buys
            if not h:
                print(f"  🆕 [{agent}] 买入 {code}: 新开仓（候选池内，本轮第 {new_buys} 只）")
            if h and at_limit_up(code, h['day_chg'], h.get('name')):
                print(f"  ⏭️ [{agent}] 买入 {code}: 涨停（{h['day_chg']:+.2f}%），不追")
                continue
            buys.append((code, bd.pct, d["reason"]))
            print(f"  📈 [{agent}] 买入 {code} 用剩余额度 {bd.pct:.0%}"
                  f"（≤{PER_STOCK_PCT:.0%}）: {d['reason']}")

    if dry_run:
        for code, vol, _reason, _iv in sells:
            executed.append({"action": "sell", "code": code, "volume": vol,
                             "price": 0, "reason": "dry-run"})
        for code, pct, _ in buys:
            executed.append({"action": "buy", "code": code, "volume": 0,
                             "price": 0, "pct": pct, "reason": "dry-run"})
        return executed

    # 执行：先卖后买（同 live_llm_trade 顺序）
    from live_trade_picks import compute_order

    # 执行前再刷一次在途集：整点分析时段正是分钟哨兵的活跃窗口，
    # 校验用快照之后哨兵可能刚对同一代码下了止损单
    pending_sell = inflight_codes("sell")
    for code, vol, reason, raw_vol in sells:
        if code in pending_sell:
            print(f"  ⏭️ [{agent}] 卖出 {code}: 执行前已有在途卖单未确认，本轮不下单")
            continue
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
            from live_fills import ack_line

            print("  " + ack_line(f"[{agent}] 卖出 {code}", result))
            fill = wait_fill(broker, result.get("order_id", ""),
                             timeout_s=FILL_POLL_TIMEOUT_S)
            if fill and int(fill.get("filled_volume") or 0) > 0:
                fv = int(fill["filled_volume"])
                fp = float(fill.get("filled_price") or limit)
                ledger = load_ledger()
                cost_p = float((((ledger.get("agents") or {}).get(agent) or {})
                                .get("positions") or {}).get(code, {}).get("cost_price") or 0)
                ledger = record_sell(ledger, agent, code, fv, fp,
                                     now_cn().isoformat())
                save_ledger(ledger)
                from live_trade_picks import log_line

                log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday",
                          "agent": agent, "code": code, "side": "sell",
                          "volume": fv, "price": fp, "cost_price": cost_p,
                          "intent_volume": raw_vol,
                          "fill": {"order_id": fill.get("order_id"),
                                   "filled_price": fp, "filled_volume": fv}})
                executed.append({"action": "sell", "code": code, "volume": fv,
                                 "price": fp, "reason": reason})
            else:
                # 未确认成交：挂 pending，由 reconcile 兜底（≤1 分钟）
                add_pending(result.get("order_id"), agent, code, "sell", vol,
                            limit, now_cn().isoformat())
                from live_trade_picks import log_line

                log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday",
                          "agent": agent, "code": code, "side": "sell",
                          "volume": vol, "price": limit, "pending": True,
                          "intent_volume": raw_vol,
                          "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ [{agent}] 卖出 {code} 失败: {exc}")
            from live_ledger import defer_on_exc

            if defer_on_exc(agent, "sell", code, vol, exc, now_cn().isoformat()):
                print(f"  ⏳ [{agent}] 卖出 {code} 已登记延期单（桥断链，恢复后自动重放）")
            from live_trade_picks import log_line

            log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday", "agent": agent,
                      "code": code, "volume": vol, "intent_volume": raw_vol,
                      "error": str(exc)})
        time.sleep(1)  # 桥限流

    ledger = load_ledger()
    for code, pct, reason in buys:
        remaining = agent_remaining(ledger, agent)
        try:
            klines = broker.get_klines(code, interval="daily")[-5:]
        except Exception:  # noqa: BLE001
            klines = []
        o = compute_order(klines, remaining, pct, code)
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
        # 杠杆硬约束：加仓后持仓市值 ≤ 权益×1.5（现金为负时才可能超）
        pos_value = sum(x["price"] * x["volume"] for x in holdings)
        equity = vcash + pos_value
        if equity > 0 and (pos_value + o["cost"]) > LEVERAGE_MAX * equity:
            print(f"  ⏭️ [{agent}] 买入 {code}: 加仓后杠杆超 {LEVERAGE_MAX}×权益"
                  f"（{pos_value + o['cost']:,.0f} > {LEVERAGE_MAX * equity:,.0f}），跳过")
            continue
        if o["cost"] > cash:
            print(f"  ⏭️ [{agent}] 买入 {code}: 账户现金不足 ¥{o['cost']:,.0f} > ¥{cash:,.0f}")
            continue
        cash -= o["cost"]
        try:
            result = broker.buy(None, None, code, o["volume"], price=o["limit_price"])
            from live_fills import ack_line

            print("  " + ack_line(f"[{agent}] 买入 {code} {o['volume']}股 "
                                  f"限价 ¥{o['limit_price']:.2f}", result))
            fill = wait_fill(broker, result.get("order_id", ""),
                             timeout_s=FILL_POLL_TIMEOUT_S)
            if fill and int(fill.get("filled_volume") or 0) > 0:
                fv = int(fill["filled_volume"])
                fp = float(fill.get("filled_price") or o["price"])
                ledger = record_buy(load_ledger(), agent, code, fv, fp,
                                    now_cn().isoformat())
                save_ledger(ledger)
                from live_trade_picks import log_line

                log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday",
                          "agent": agent, "code": code, "side": "buy",
                          "volume": fv, "price": fp,
                          "fill": {"order_id": fill.get("order_id"),
                                   "filled_price": fp, "filled_volume": fv}})
                executed.append({"action": "buy", "code": code, "volume": fv,
                                 "price": fp, "reason": reason})
            else:
                # 未确认成交：挂 pending，由 reconcile 兜底（≤1 分钟）
                add_pending(result.get("order_id"), agent, code, "buy",
                            o["volume"], o["price"], now_cn().isoformat())
                from live_trade_picks import log_line

                log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday",
                          "agent": agent, "code": code, "side": "buy",
                          "volume": o["volume"], "price": o["price"],
                          "pending": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ [{agent}] 买入 {code} 失败: {exc}")
            from live_ledger import defer_on_exc

            if defer_on_exc(agent, "buy", code, o["volume"], exc,
                            now_cn().isoformat()):
                print(f"  ⏳ [{agent}] 买入 {code} 已登记延期单（桥断链，恢复后自动重放）")
            from live_trade_picks import log_line

            log_line({"ts": now_cn().isoformat(), "mode": "execute_intraday", "agent": agent,
                      "code": code, "error": str(exc)})
        time.sleep(1)
    return executed


def compute_forced_trims(holdings: list, virtual_cash: float,
                         planned_sells: dict | None = None) -> list:
    """杠杆硬约束：持仓市值 > 权益×LEVERAGE_MAX → 强制减仓到 LEVERAGE_TRIM_TO×。

    planned_sells = {code: 模型已主动计划卖出的市值}——模型自己已经降了杠杆的部分
    要扣除，否则「模型自觉卖 30% + 系统强制减仓 32%」会双卖（14:00 教训：688183
    被 800 股砍掉 57%）。从市值最大的持仓开始减（先跳过 T+1 不可卖），返回 sell
    决策（与 LLM 决策一起走同一套卖出闸门）。空仓/未超限返回 []。
    """
    pos_value = sum(x["price"] * x["volume"] for x in holdings)
    equity = virtual_cash + pos_value
    if equity <= 0 or pos_value <= LEVERAGE_MAX * equity:
        return []
    planned = sum((planned_sells or {}).get(h["code"], 0.0) for h in holdings)
    need = (pos_value - LEVERAGE_TRIM_TO * equity) - planned
    if need <= 0:
        return []  # 模型已主动降到安全区，无需强制
    out: list = []
    for h in sorted(holdings, key=lambda x: -x["price"] * x["volume"]):
        if need <= 0:
            break
        if h["avail"] <= 0:
            continue
        avail_value = h["price"] * h["avail"]
        # 按板块手数口径取整（科创板 200 股/手）：不足 1 手不产生决策（卖出闸门同口径）
        raw_vol = round_sell_qty(h["code"],
                                 int(min(h["price"] * h["avail"], need) / h["price"]),
                                 h["avail"])
        if raw_vol <= 0:
            continue
        pct = raw_vol * h["price"] / avail_value
        out.append({
            "action": "sell", "code": h["code"], "pct": round(pct, 3),
            "reason": (f"杠杆超限强制减仓（持仓 ¥{pos_value:,.0f} > "
                       f"权益×{LEVERAGE_MAX} = ¥{LEVERAGE_MAX * equity:,.0f}，"
                       f"已扣除模型主动卖出 ¥{planned:,.0f}）"),
        })
        need -= raw_vol * h["price"]
    return out


def agent_locked_idle(my_holdings: list, virtual_cash: float) -> bool:
    """锁仓降频判据：名下有持仓但全部 T+1 不可卖，且可用资金不足以建仓
    → 本轮整点分析对该 agent 无事可做（卖出被 T+1 封死、买入金额不够一手）。
    只用于整点定时轮降频；唤醒触发与尾盘轮不经过本判据。"""
    return bool(my_holdings) and all(h["avail"] <= 0 for h in my_holdings) \
        and virtual_cash < LOCKED_SKIP_CASH


def run_analysis(broker, reason: str, dry_run: bool = True,
                 agents: list | None = None,
                 gap_window: tuple[str, str] | None = None,
                 allow_lock_skip: bool = False) -> int:
    """完整盘中分析（每小时 cron + 9:30 开盘 + 波动触发 + 断线恢复补跑共用）：
    在途成交 reconcile → 净值记录 → 各分账 agent 名下持仓 LLM 简评落盘
    → 解析决策（sell/buy/watch）→ 闸门校验 → 桥执行（dry_run=False 才真下单）
    → 按真实成交价记账 → 杠杆超限强制减仓 → 更新波动基线 state。
    gap_window=(start,end ISO)：断线恢复补跑时注入【断线窗口提示】——
    模型需知分析覆盖了数据中断窗口，窗口轨迹可回放/明说缺失。"""
    now = now_cn()
    gap_start_iso, gap_end_iso = (gap_window or (None, None))
    # 先补记在途成交（下单≠成交，按真实成交价/量入账，≤1 分钟延迟兜底）
    from live_fills import reconcile

    try:
        reconcile(broker)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 成交回报 reconcile 失败: {exc}")
    acct = broker._account_query()
    asset = float((acct.get("asset") or {}).get("asset") or 0)
    cash = float((acct.get("asset") or {}).get("cash") or 0)
    # 桥假活硬闸（2026-09-09）：HTTP 通、行情正常，但账户通道返空（交易账号未登录/
    # 会话掉线）——旧代码落进下面的"无实盘持仓，跳过"静默返回，对话页一片空白，
    # 用户以为 agent 没在跑。资产 ≤0 不可能是真账户状态 → 显式失败，走失败提示落盘。
    if asset <= 0:
        from agent_tools.brokers.base import BrokerError

        raise BrokerError(f"桥假活：account/query 返回资产 {asset}（行情通道正常，"
                          "交易账号可能未登录或会话掉线）")
    # 净值记录：每次分析一条（空仓也记，曲线不中断）
    record_equity(broker, asset, cash)
    positions = [p for p in (acct.get("positions") or [])
                 if float(p.get("total_volume") or 0) > 0]
    if not positions:
        print(f"[{now:%F %T}] 无实盘持仓，跳过")
        return 0
    # 心跳基线提前写：慢分析（LLM 120s 超时）跨分钟时，波动节流依然有效
    # （原实现只在分析结束后写 last_ts，glm 超时会让下一分钟重新触发 → 叠跑）
    update_state({"last_ts": now.isoformat(), "last_reason": reason})

    names = load_names()
    rows = build_rows(broker, positions, names)
    # 新闻分子（新闻 agent 管线产物，主编 latest.json）：全 agent 共享同一份，
    # 交易侧只读不分析（2026-09-07 用户口径：交易 agent 不单独读新闻）
    try:
        from live_prompt_context import load_news_brief

        news_brief_text = load_news_brief()
    except Exception:  # noqa: BLE001 分子缺失不阻塞交易分析
        news_brief_text = ""
    if news_brief_text:
        print(f"  📰 新闻分子已注入（{len(news_brief_text)} 字）")
    # 实时盘口（桥五档，弱 L2 信号）：全部持仓一次拉齐，各 agent 提示词共用
    orderbook = load_orderbook(broker, sorted({r["code"] for r in rows})) or {}
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
    last_decisions = load_last_decisions()
    ok = 0
    pool, direction = None, None  # 候选池（本轮懒加载一次，空仓建仓 + 持仓新开仓/换仓共用）
    pool_loaded = False
    for agent in (agents if agents is not None else enabled_agents()):
      try:  # per-agent 隔离：单 agent 执行/解析异常不拖死其他 agent（2026-09-07 复盘）
        rec = (ledger.get("agents") or {}).get(agent) or {}
        mine_codes = set((rec.get("positions") or {}).keys())
        my_rows = [r for r in rows if r["code"] in mine_codes]
        my_holdings = [h for h in holdings if h["code"] in mine_codes]
        virtual_cash = agent_virtual_cash(ledger, agent)
        if allow_lock_skip and agent_locked_idle(my_holdings, virtual_cash):
            print(f"[{now:%F %T}] {agent} 🔒 持仓全部 T+1 锁定且可用资金不足建仓 → 本轮跳过"
                  f"（波动/板块/L2/新闻唤醒与尾盘轮仍在线）")
            continue
        if not pool_loaded:  # 候选池：持仓 agent 也要（新开仓/换仓 + 注入池内实时价）
            from live_llm_trade import load_pool

            pool, direction = load_pool(20)
            pool_loaded = True
        # 按资金量裁候选（2026-09-09）：单票预算 = 剩余额度×单票比例，且不超虚拟现金。
        # 买不起的（最小 100/200 股一手都超预算）整行不推给模型——池子是研究产物、
        # 与 agent 资金量无关，不裁则模型反复点买不起的票，下单闸门再拦已经是浪费。
        from live_ledger import agent_remaining as _agent_remaining
        from live_prompt_context import filter_affordable

        budget = min(_agent_remaining(ledger, agent) * PER_STOCK_PCT, virtual_cash)
        pool_agent, too_pricey = (filter_affordable(pool, budget) if pool
                                  else (pool, []))
        if too_pricey:
            print(f"[{now:%F %T}] 💸 {agent} 单票预算 ¥{budget:,.0f}，"
                  f"候选池剔除 {len(too_pricey)} 只买不起的："
                  + "、".join(f"{c}{n}(最小{q}股 ¥{v:,.0f})"
                              for c, n, v, q in too_pricey))
        if not my_rows and pool and not pool_agent:
            print(f"[{now:%F %T}] {agent} 空仓且候选池全部超出单票预算 "
                  f"¥{budget:,.0f}，本轮跳过")
            continue
        if not my_rows:
            # 空仓 → 候选池复盘 + 可建仓（等价 09:35 权限）；池子都没有就跳过
            if not pool:
                print(f"[{now:%F %T}] {agent} 空仓且无候选池，跳过")
                continue
            virtual_asset = virtual_cash
            # 候选池 diff：当日该 agent 已注入过同一份池（dsh 有对话历史）→ 紧凑注入
            # 前 8 行 + 全名单；llm 无状态模式/池变化/当日首次 → 全表（保守可逆）
            st = load_state()
            pool_sig = ",".join(str(p.get("code") or "") for p in pool_agent)
            today = now.strftime("%Y-%m-%d")
            seen_sigs = st.get("last_pool_sig") or {}
            seen_days = st.get("last_pool_sig_day") or {}
            pool_compact = (agent_mode_for(agent) == "dsh"
                            and seen_sigs.get(agent) == pool_sig
                            and seen_days.get(agent) == today)
            if not pool_compact:
                # 嵌套字典的读-改-写在锁内做：同轮里前一个 agent 刚写的 sig 不能被
                # 本 agent 手里的陈旧副本抹掉（见 update_state 的 mutate）
                update_state(mutate=lambda cur: {
                    **cur,
                    "last_pool_sig": {**(cur.get("last_pool_sig") or {}), agent: pool_sig},
                    "last_pool_sig_day": {**(cur.get("last_pool_sig_day") or {}),
                                          agent: today},
                })
            user_content = build_flat_content(pool_agent, direction, virtual_cash, agent,
                                              compact=pool_compact)
            print(f"[{now:%F %T}] {agent} 空仓，候选池复盘"
                  f"（可建仓 ¥{virtual_cash:,.0f}{'，紧凑注入' if pool_compact else ''}）")
        else:
            print(f"[{now:%F %T}] {agent} 名下持仓 {len(my_rows)} 只，盘中分析")
            virtual_asset = virtual_cash + sum(r["price"] * r["volume"] for r in my_rows)
            cross = _cross_agent_refs(last_decisions, agent)
            stale = market_data_stale(broker)  # 盘中硬闸：日K停在昨天 → 提示词禁价
            if stale:
                print(f"[{now:%F %T}] 🚨 行情停更（桥假活/通道断），"
                      f"已禁止 {agent} 基于陈旧价格做买卖决策")
            recap = build_trade_recap(agent)  # 近7日成交回顾（防隔日行为漂移）
            review = load_review_recap(agent)  # 昨日复盘要点（自我进化 v1.5）
            user_content = build_user_content(my_rows, virtual_asset, virtual_cash,
                                              agent,
                                              (last_decisions.get(agent) or {}).get("decisions"),
                                              orderbook, cross, stale, recap, review,
                                              pool=pool_agent)
        # 比赛配置多选：多选时按自然日轮转（一天一种模式，跨天轮换，
        # 盘中口径一致不横跳；单选/轮转关闭时行为不变）
        from prompts.analysis_modes import rotated_modes

        agent_exec_done = False  # 每 agent 每小时只执行一轮（多模式多轮会叠加买入突破分账额度）
        hyp_lines = load_hypotheses_summary()
        sc_lines = load_decision_scorecard(agent)  # 决策远期记分卡（自我纠正回路）
    # 盘面状态（系统确定性注入，模型不可争辩；5 分钟缓存）
        try:
            from market_state import build_market_state

            state_text = build_market_state(broker)
        except Exception:  # noqa: BLE001
            state_text = ""
        # 断线恢复补跑：注入【断线窗口提示】（首部，持仓窗口轨迹有则回放、无则明说）
        if gap_start_iso and my_rows:
            note = build_gap_note(gap_start_iso, gap_end_iso, my_rows)
            if note:
                user_content = note + "\n" + user_content
        for mode in rotated_modes(agent):
            mode_prompt = mode["prompt"]
            if mode["id"] == "awareness":  # 情境感知: 注入今日排行榜上下文
                lb = build_leaderboard()
                if lb:
                    mode_prompt = mode_prompt + f"\n今日各 agent 虚拟净值排行榜（¥10 万起点）：\n{lb}"
            # 模式正文必须进提示词：dsh agent 与直连 LLM 一视同仁
            # （此前 dsh 分支只收到【分析配置：名称】标签，苦行/极限杠杆等
            #  纪律文字从未进入 v4-flash 的上下文——2026-09-03 修复）
            labeled_content = (f"【分析配置：{mode['name']}】\n{mode_prompt}\n"
                               + (f"{state_text}\n" if state_text else "")
                               + (f"{hyp_lines}\n" if hyp_lines else "")
                               + (f"{sc_lines}\n" if sc_lines else "")
                               + (f"{news_brief_text}\n\n" if news_brief_text else "")
                               + "\n" + user_content)
            usage = None
            try:
                if agent_mode_for(agent) == "dsh":
                    # dsh agent：工具（行情/搜索/数学/记忆/quantdb）+ 可写代码，
                    # 思考过程不再是单次提示词；模型可 per-agent 配（deepseek/glm）
                    from dsh_agent import run_agent

                    # 全 agent 工作法注入（按角色分化，见 prompts/agent_extra.py）
                    from prompts.agent_extra import get_extra

                    task = get_extra(agent) + "\n\n" + labeled_content
                    content = run_agent(task, timeout_s=420,
                                        model=agent_model_for(agent))
                    # dsh 不返回 token 数 → 按字符量估算（中文 ≈1.8 字符/token），
                    # 标记 usage_est 供前端区分；否则模型卡 token 统计会永久冻结
                    # （2026-09-03 实测 flash 自 9/2 09:35 起停更）
                    usage = {
                        "prompt_tokens": int(len(task) / 1.8),
                        "completion_tokens": int(len(content or "") / 1.8),
                        "total_tokens": int((len(task) + len(content or "")) / 1.8),
                        "usage_est": True,
                    }
                else:
                    content, usage = call_llm(
                        labeled_content, agent,
                        system_prompt_for(agent, {**mode, "prompt": mode_prompt}))
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
            save_last_decisions(agent, decisions, now.isoformat())
            try:  # 决策远期记分卡入池（统计失败不得影响交易主链路）
                from decision_track import ingest_decisions

                ingest_decisions(agent, decisions, now.isoformat(),
                                 held_codes={h["code"] for h in my_holdings},
                                 source="live_hourly_analysis")
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠️ 决策记分卡入池失败（不影响交易）: {exc}")
            if agent_exec_done:
                continue
            agent_exec_done = True
            exec_list = [d for d in decisions if d["action"] in ("sell", "buy")]
            # 杠杆硬约束：超限强制减仓（走同一套卖出闸门，不依赖模型自觉）
            if my_rows:
                # 模型已主动计划卖的市值（按卖出闸门同口径：avail×pct 取整到手）
                planned: dict = {}
                for d in decisions:
                    if d["action"] != "sell":
                        continue
                    h = next((x for x in my_holdings if x["code"] == d["code"]), None)
                    if not h or h["avail"] <= 0:
                        continue
                    vol = round_sell_qty(h["code"],
                                         int(h["avail"] * min(max(d["pct"], 0), 1)),
                                         h["avail"])
                    planned[h["code"]] = planned.get(h["code"], 0) + h["price"] * vol
                forced = compute_forced_trims(my_holdings, virtual_cash, planned)
                if forced:
                    print(f"[{now:%F %T}] ⚠️ [{agent}] 杠杆超限，强制减仓 {len(forced)} 笔"
                          f"（扣除模型主动卖出 ¥{sum(planned.values()):,.0f}）")
                    exec_list = forced + exec_list  # 强制优先
            if not dry_run:
                # watch 条件位（跌破止损/到位止盈）→ 分钟级价格哨兵接管，不等整点；
                # 该 agent 的旧规则整组替换，最新分析说了算
                from live_price_watch import save_watch_rules

                n_watch = save_watch_rules(agent, decisions)
                if n_watch:
                    print(f"[{now:%F %T}] 👁️ [{agent}] 挂 {n_watch} 个 watch 条件位"
                          f"（分钟级哨兵监控）")
            executed = execute_intraday_decision(
                broker, agent, exec_list, my_holdings, cash, dry_run=dry_run,
                pool_codes={p.get("code") for p in pool} if pool else None,
                names=names)
            if executed:
                tag = "🟡 DRY-RUN 决策" if dry_run else "✅ 已执行"
                acts = "/".join(f"{e['action']} {e['code']}" for e in executed)
                print(f"[{now:%F %T}] {tag} [{agent}] {mode['name']}: "
                      f"{len(executed)} 笔（{acts}）")

      except Exception as exc:  # noqa: BLE001
          print(f"[{now_cn():%F %T}] ⚠️ [{agent}] 本轮分析/执行失败: {type(exc).__name__}: {exc}"
                f"（其余 agent 不受影响）")
          append_failure_log([agent], "本轮分析/执行", exc)
          continue
    # 更新波动基线（全部持仓，跨 agent 汇总）；merge 旧键——仲裁/其他写入方
    # 可能带 last_good_sample_ts 等采样键，整体替换会丢掉断线恢复判据
    # v2 触发源基线：分析完成时的板块涨跌 / 内外盘失衡（下次 check_volatility 的比对基准）
    last_sector_chg, last_inout = {}, {}
    try:
        ctx = load_market_context()
        pos_sec = ctx.get("position_sectors") or {}
        for code, bcode in pos_sec.items():
            sec = (ctx.get("sectors") or {}).get(bcode) or {}
            if sec.get("chg_pct") is not None:
                last_sector_chg[bcode] = sec["chg_pct"]
    except Exception:  # noqa: BLE001
        pass
    try:
        from live_l2_capture import load_factors as _load_factors

        _facs = _load_factors() or {}
        for r in rows:
            inout = ((_facs.get(r["code"]) or {}).get("factors") or {}).get("inout")
            if inout is not None:
                last_inout[r["code"]] = inout
    except Exception:  # noqa: BLE001
        pass
    update_state({
        "last_ts": now.isoformat(),
        "last_reason": reason,
        "last_pnl": {r["code"]: r["pnl_pct"] for r in rows},
        "last_day": {r["code"]: r["day_chg"] for r in rows if r["day_chg"] is not None},
        "last_sector_chg": last_sector_chg,
        "last_inout": last_inout,
    })
    return 0 if ok else 1


def after_hours_exec_enabled() -> bool:
    """盘后固定价格交易执行开关：configs/intraday_exec.json {"after_hours": true}（缺省开）。
    只作用于科创板/创业板标的的卖出（收盘价撮合）；主板不支持盘后定价。"""
    try:
        cfg = json.loads((ROOT / "configs" / "intraday_exec.json").read_text(encoding="utf-8"))
        return bool(cfg.get("after_hours", True))
    except (OSError, json.JSONDecodeError):
        return True


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
    parser.add_argument("--dry-run", action="store_true",
                        help="强制只打印不下单（覆盖配置开关；验证测试用）")
    parser.add_argument("--record-only", action="store_true",
                        help="只采样净值（cron 每 5 分钟跑），不做 LLM 分析")
    parser.add_argument("--agents", default="",
                        help="只分析指定分账 agent（逗号分隔；默认全部 enabled；"
                             "手动触发用，如 --agents glm-5.3-flash）")
    args = parser.parse_args()
    agents_filter = [a.strip() for a in args.agents.split(",") if a.strip()] or None

    now = now_cn()
    from agent_tools.brokers.tdx_bridge import TdxBridgeBroker

    # 配置开关：外部调度不传 --execute 时也可自动执行
    cfg_exec = intraday_exec_enabled()
    # --force 只用于时段外测试：不再顺带开启执行（防 12:25 那种验证测试真下单）；
    # --dry-run 显式覆盖一切
    do_execute = args.execute or (cfg_exec and not args.force and not args.dry_run)
    if args.dry_run:
        do_execute = False

    if args.record_only:
        # 轻量净值采样：交易时段每分钟（cron * 9-15），只查账户+写文件
        if not args.force and not record_window(now):
            print(f"[{now:%F %T}] {why_not(now) or '非采样时段（9:25-15:10）'}，跳过")
            return 0
        try:
            broker = TdxBridgeBroker()
            acct = broker._account_query()
        except Exception as exc:  # noqa: BLE001 桥不可达：本分钟采样失败，等下一分钟
            print(f"[{now:%F %T}] ⚠️ 净值采样失败（桥不可达）: {exc}")
            return 0
        asset = float((acct.get("asset") or {}).get("asset") or 0)
        cash = float((acct.get("asset") or {}).get("cash") or 0)
        record_equity(broker, asset, cash)
        if asset > 0:
            print(f"[{now:%F %T}] 净值采样完成 资产 ¥{asset:,.2f}")
        # 在途单成交补记（2026-09-11）：本段是 cron 每分钟入口，此前从不对账 →
        # 哨兵/整点轮挂的 pending 最长压到下一整点才补进账本，分账额度/闸门
        # 建立在滞后账本上。reconcile 内部有跨进程锁，与哨兵/整点轮撞车自动退让。
        try:
            from live_fills import reconcile as _reconcile

            n = _reconcile(broker, now)
            if n:
                print(f"[{now:%F %T}] ♻️ 在途单成交补记 {n} 笔")
        except Exception as exc:  # noqa: BLE001 补记失败不能打断采样
            print(f"[{now:%F %T}] ⚠️ 在途单成交补记失败: {exc}")
        # 断线恢复判据：last_good = 上次桥数据好（asset>0）的采样时刻，每健康分钟
        # 推进；中断期间无写入 → 恢复首分钟 gap≥阈值立即整窗口补跑。硬断线崩溃
        # 不再依赖"上一分钟恰好记到 asset≤0"（崩溃留旧正值 → 恢复后傻等到整点）。
        st_prev = load_state()
        last_good = last_good_sample_ts(st_prev)
        missed = missed_sample_minutes(last_good, now)
        keep_last_good = False  # 补跑失败 → 保留旧值，下一分钟自动重试
        if asset > 0:
            if args.force or in_trading_window(now):
                win = ((last_good, now.isoformat()) if last_good else None)
                positions = [p for p in (acct.get("positions") or [])
                             if float(p.get("total_volume") or 0) > 0]
                # 方案 C: 波动触发 — 交易时段内持仓盈亏 ±3pp 或个股涨跌 ±5%
                vol_reason = None
                if positions:
                    try:
                        vol_reason = check_volatility(broker, positions)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[{now:%F %T}] ⚠️ 波动检测失败: {exc}")
                        vol_reason = None
                    if vol_reason:
                        print(f"[{now:%F %T}] ⚡ 波动触发完整分析: {vol_reason}")
                        if not _try_lock():
                            print(f"[{now:%F %T}] ⏭️ 已有分析实例在跑，跳过（防并发叠跑）")
                        else:
                            try:
                                run_analysis(broker, f"波动触发: {vol_reason}",
                                             dry_run=not do_execute, gap_window=win)
                            except Exception as exc:  # noqa: BLE001
                                print(f"[{now:%F %T}] ⚠️ 波动触发分析失败: {exc}")
                # 断线恢复补跑：数据中断 ≥RECOVERY_DOWN_MIN 分钟、恢复即整窗口补跑。
                # 断线期间整点轮全跳过 → 空窗到下一整点（2026-09-04 复盘）；本次加固：
                # 短中断（如 13:20-13:35 掉线、13:00 刚分析过）同样恢复即补——不再傻等。
                # 2026-09-09 修：原写作 elif 挂在 if positions 下 → 有持仓时永不补跑
                # （当天断线 1h、有 2 只持仓，恢复后只做波动检测，静默空窗到下一整点）。
                # 现在与波动触发互斥但不再被持仓短路：波动没触发就补跑。
                if not vol_reason and missed >= RECOVERY_DOWN_MIN:
                    # 开盘宽限：断线自盘前（last_good 停在昨日）且刚开盘不久 →
                    # 09:35 llm_trade 开盘全池分析会接手，让路防 09:34/09:35 双跑叠买
                    open_grace = (str(last_good)[:10] != now.strftime("%Y-%m-%d")
                                  and now.hour * 60 + now.minute < 9 * 60 + 40)
                    if open_grace:
                        print(f"[{now:%F %T}] ⏭️ 恢复补跑开盘宽限："
                              f"断线自盘前开始，09:35 开盘全池分析将接手")
                    else:
                        print(f"[{now:%F %T}] ⚡ 断线恢复补跑"
                              f"（错失 {missed} 个采样分钟，窗口自 {last_good}）")
                        if not _try_lock():
                            print(f"[{now:%F %T}] ⏭️ 已有分析实例在跑，恢复补跑让位")
                        else:
                            try:
                                run_analysis(broker, "断线恢复补跑", dry_run=not do_execute,
                                             gap_window=(last_good, now.isoformat()))
                            except Exception as exc:  # noqa: BLE001
                                print(f"[{now:%F %T}] ⚠️ 断线恢复补跑失败: {exc}（下分钟重试）")
                                keep_last_good = True
        # 记录本分钟采样有效性（旧键保留兼容；补跑失败不推进 last_good → 下分钟重试）
        # 只提交采样键：旧实现把**分析前**读到的 st_prev 整表写回，抹掉了本分钟内
        # 触发轮写的 last_trigger/last_ts/last_news_key → 节流/冷却/去重全失效
        # （2026-09-11，78 轮完整分析）。见 update_state。
        new_sample = {"last_sample_asset": asset, "last_sample_ts": now.isoformat()}
        if asset > 0 and not keep_last_good:
            new_sample["last_good_sample_ts"] = now.isoformat()
        update_state(new_sample)
        return 0

    if not args.force and not in_trading_window(now):
        print(f"[{now:%F %T}] {why_not(now) or '非交易时段（北京 9:30-11:30/13:00-15:00）'}，跳过")
        return 0

    if not _try_lock():
        print(f"[{now:%F %T}] ⏭️ 已有分析实例在跑，跳过（防并发叠跑）")
        return 0
    broker = TdxBridgeBroker()
    label = "手动触发" if agents_filter else "每小时定时"
    # 锁仓降频只挂整点定时轮且尾盘前；手动/波动/补跑一律全跑
    allow_lock_skip = (label == "每小时定时"
                       and now.hour * 60 + now.minute < LOCKED_SKIP_BEFORE)
    try:
        rc = run_analysis(broker, label, dry_run=not do_execute,
                          agents=agents_filter, allow_lock_skip=allow_lock_skip)
    except Exception as exc:  # noqa: BLE001 桥挂/数据源坏 → 一行落日志，等下一轮 cron
        print(f"[{now:%F %T}] ⚠️ {label}分析失败: {type(exc).__name__}: {exc}（等下一轮重试）")
        append_failure_log(agents_filter, label, exc)
        rc = 1
    # 辩论 v2：分歧检测 → arbiter 建议（只记录与提示，不代执行权）
    try:
        from debate_arbiter import check_and_arbitrate

        check_and_arbitrate()
    except Exception:  # noqa: BLE001
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
