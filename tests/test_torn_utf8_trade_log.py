"""撕裂 UTF-8 的成交流水：读取方不得整文件挂掉（2026-09-12 批 10）。

`logs/live_trade_*.jsonl` 是**追加型**日志，行内含中文（决策理由/意图），写入侧是
普通 append（非原子）——进程被杀/磁盘满时可能在某行中间截断，且正好落在**多字节
字符中间**：文件尾部留下非法 UTF-8 字节序列。纯 ASCII 的追加日志不会出这个形态
（截断后的前缀仍是合法 UTF-8），所以这一类只出现在含中文的流水上。

后果面：`read_text(encoding="utf-8")` 严格解码 → **整个文件**抛 UnicodeDecodeError。
8 个读取点的异常口径都接不住它（`UnicodeDecodeError` 是 ValueError 子类、**不是**
OSError；`except (OSError, json.JSONDecodeError)` 同样接不住；tradability_audit 更是
完全没有 try）。**实测筛出的全类 = 盘上确实含非 ASCII 字节 + 追加写（非原子）的
4 个文件、14 个读取点**（AST 扫描「行式 read_text + 接不住 UnicodeDecodeError 的
except」后逐个核对字节与写入侧）：
  A. logs/live_trade_*.jsonl（8 点）——
     交易路径 live_fills.fill_recorded / live_hourly_analysis.daily_buy_codes /
              live_breaker.realized_loss_today / live_prompt_context.build_trade_recap
     报表路径 post_review.collect_facts / daily_report_agent.collect /
              decision_track.backfill_from_logs / tradability_audit.load_trades
  B. data/agent_data_astock/*/log/*/log.jsonl（2 点）——daily_report_agent.collect
     （轮次统计）/ decision_track.backfill_from_agent_logs
  C. data/news_brief/history.jsonl（3 点）——news_review.load_day_briefs /
     night_pool_agent._news_block / risk_list._read_news_lines
  D. logs/decision_pool.jsonl（1 点）——decision_track.load_pool
（纯 ASCII 的 live_equity / min_snapshots / sentiment_zt 与原子写的 JSON 文档不在类内：
ASCII 截断后的前缀仍是合法 UTF-8；而**整文档** `json.loads` 加 replace 会把坏值读成
含 U+FFFD 的合法字符串，比抛错更糟——本类只针对**逐行 JSONL** 读取方。）
一旦撕裂，**当天（乃至此后）每一轮**读取都再挂一次——坏文件一直躺在那儿。而且方向
是「越错越静默」：fill_recorded 抛异常打断对账/结算；若改成
`except ...: return False` 更糟——已记账的单会被判成「没记过」→ 重复记账。

正解 `errors="replace"`：撕裂字节 → U+FFFD → 该行 `json.loads` 失败，被既有逐行
handler 跳过，其余完整行照常使用（这些读取方本来就逐行容错）。本文件既是回归测试，
也是这一族读取点「读取必须容错解码」的口径声明。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_torn_utf8_trade_log.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import daily_report_agent as D  # noqa: E402
import decision_track as DT  # noqa: E402
import live_breaker as B  # noqa: E402
import live_fills as F  # noqa: E402
import live_hourly_analysis as H  # noqa: E402
import live_prompt_context as PC  # noqa: E402
import live_trade_picks as TP  # noqa: E402
import news_review as NR  # noqa: E402
import night_pool_agent as NP  # noqa: E402
import post_review as PR  # noqa: E402
import risk_list as RL  # noqa: E402
import tradability_audit as TA  # noqa: E402

NOW = datetime(2026, 9, 11, 10, 0)
DAY = "2026-09-11"
DAY8 = "20260911"
# "止"的 UTF-8 是 3 字节，截断到 2 字节 = append 中途被杀留下的撕裂尾巴
TORN = b"\xe6\xad"


def _torn_log(path: Path, rows: list) -> Path:
    """合法行（含中文，`ensure_ascii=False` 与写入侧同格式）+ 尾部撕裂字节。"""
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8") + TORN)
    return path


# ---------------------------------------------------------------- 前提

def test_premise_torn_tail_raises_and_is_not_oserror(tmp_path):
    """前提钉住：撕裂尾巴让严格解码整文件抛错，且**不是** OSError
    —— 这正是 8 个 `except OSError` 接不住、会穿出去的原因。"""
    p = _torn_log(tmp_path / "live_trade_20260911.jsonl", [{"reason": "止损"}])

    with pytest.raises(UnicodeDecodeError) as ei:
        p.read_text(encoding="utf-8")

    assert not isinstance(ei.value, OSError)          # 旧 except 口径接不住
    assert p.read_text(encoding="utf-8", errors="replace").count("�") == 1


# ---------------------------------------------------------------- 交易路径

def test_fill_recorded_survives_torn_tail(tmp_path, monkeypatch):
    """对账/结算的判据：尾部撕裂不许把「已记账」判成「没记过」（重复记账源头）。"""
    monkeypatch.setattr(TP, "LOG_DIR", tmp_path)
    _torn_log(tmp_path / f"live_trade_{DAY8}.jsonl",
              [{"order_id": "OID1", "agent": "a1", "side": "buy", "reason": "止损"}])

    assert F.fill_recorded("OID1", NOW) is True
    assert F.fill_recorded("OID-OTHER", NOW) is False     # 未记过的单仍如实 False


def test_daily_buy_codes_survives_torn_tail(tmp_path):
    p = _torn_log(tmp_path / f"live_trade_{DAY8}.jsonl",
                  [{"agent": "a1", "side": "buy", "code": "600000.SH",
                    "mode": "fill_confirm", "volume": 100, "price": 10.0,
                    "reason": "回调加仓"}])

    assert H.daily_buy_codes("a1", DAY, p) == {"600000.SH"}


def test_realized_loss_today_survives_torn_tail(tmp_path):
    """熔断判据：解码挂掉 = 熔断当天永远算不出已实现亏损（静默失效）。"""
    p = _torn_log(tmp_path / f"live_trade_{DAY8}.jsonl",
                  [{"agent": "a1", "side": "sell", "code": "600000.SH",
                    "cost_price": 10.0,
                    "fill": {"filled_volume": 500, "filled_price": 9.0}}])

    assert B.realized_loss_today("a1", DAY, p) == 500.0


def test_build_trade_recap_survives_torn_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(PC, "ROOT", tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    _torn_log(tmp_path / "logs" / f"live_trade_{today.replace('-', '')}.jsonl",
              [{"agent": "a1", "side": "sell", "code": "600000.SH", "ts": f"{today}T10:00:00",
                "mode": "fill_confirm", "volume": 300, "price": 9.5, "reason": "止损离场"}])

    out = PC.build_trade_recap("a1", days=7)

    assert "600000.SH" in out


# ---------------------------------------------------------------- 报表路径

def test_post_review_collect_facts_survives_torn_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "ROOT", tmp_path)
    _torn_log(tmp_path / "logs" / f"live_trade_{DAY8}.jsonl",
              [{"agent": "a1", "side": "sell", "code": "600000.SH", "ts": f"{DAY}T10:00:00",
                "fill": {"filled_volume": 200, "filled_price": 9.5}, "reason": "止损"}])

    out = PR.collect_facts("a1", DAY)

    assert "600000.SH" in out                            # 成交段没被整段吞掉
    assert "无（或全部被拒单）" not in out


def test_daily_report_collect_survives_torn_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(TP, "enabled_agents", lambda: ["a1"])
    today = datetime.now().strftime("%Y-%m-%d")
    _torn_log(tmp_path / "logs" / f"live_trade_{today.replace('-', '')}.jsonl",
              [{"agent": "a1", "side": "buy", "code": "600000.SH",
                "ts": f"{today}T10:00:00",
                "fill": {"filled_volume": 100, "filled_price": 10.0}}])

    out = D.collect(today)

    assert out["agents"][0]["fills"] >= 1


def test_decision_track_backfill_survives_torn_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(DT, "ROOT", tmp_path)
    _torn_log(tmp_path / "logs" / f"live_trade_{DAY8}.jsonl",
              [{"agent": "a1", "side": "buy", "code": "600000.SH", "ts": f"{DAY}T10:00:00",
                "fill": {"filled_volume": 100, "filled_price": 10.0}}])

    res = DT.backfill_from_logs(tmp_path / "pool.json")

    assert res["backfilled"] >= 1


def test_tradability_audit_load_trades_survives_torn_tail(tmp_path):
    """此站点原本**没有任何 try**：撕裂 = 审计工具直接崩。"""
    p = _torn_log(tmp_path / f"live_trade_{DAY8}.jsonl",
                  [{"order_id": "OID1", "agent": "a1", "side": "buy",
                    "code": "600000.SH", "ts": f"{DAY}T10:00:00",
                    "fill": {"filled_volume": 100, "filled_price": 10.0}}])

    rows = TA.load_trades([p])

    assert [r["code"] for r in rows] == ["600000.SH"]


# -------------------------------------------- B. 模型对话流（agent_data_astock）

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def test_daily_report_rounds_survive_torn_agent_log(tmp_path, monkeypatch):
    """日报的「轮数/未执行轮数」取自对话流；撕裂会让两个数都静默归零
    ——正好是 09-10「桥假活但日报盖章运行正常」那种形态的翻版。"""
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(TP, "enabled_agents", lambda: ["a1"])
    today = _today()
    _torn_log(tmp_path / "data" / "agent_data_astock" / "a1" / "log" / today / "log.jsonl",
              [{"timestamp": f"{today}T09:40:00", "kind": "analysis",
                "new_messages": [{"role": "assistant", "content": "本轮分析未执行：桥不可用"}]}])

    rec = D.collect(today)["agents"][0]

    assert rec["rounds"] == 1
    assert rec["rounds_failed"] == 1                      # 正文也读出来了，不是只数行


def test_decision_track_backfill_from_agent_logs_survives_torn(tmp_path, monkeypatch):
    monkeypatch.setattr(DT, "ROOT", tmp_path)
    _torn_log(tmp_path / "data" / "agent_data_astock" / "a1" / "log" / DAY / "log.jsonl",
              [{"timestamp": f"{DAY}T09:40:00",
                "new_messages": [{"role": "assistant",
                                  "content": '{"decisions":[{"action":"buy",'
                                             '"code":"600000.SH","reason":"回调到位"}]}'}]}])

    res = DT.backfill_from_agent_logs(tmp_path / "pool.json")

    assert res["backfilled_agent_logs"] == 1


# -------------------------------------------- C. 新闻 history

def test_news_history_readers_survive_torn_tail(tmp_path, monkeypatch):
    """news history 是含中文的追加日志（news_brief.py:680 `open("a")`）：
    新闻复盘（NR）与风险清单（RL）两个读取方都要活下来。"""
    p = _torn_log(tmp_path / "history.jsonl",
                  [{"ts": f"{DAY}T08:00:00", "macro": {"view": "美股大跌"}}])
    monkeypatch.setattr(NR, "HISTORY_FILE", p)
    monkeypatch.setattr(RL, "NEWS_HISTORY", p)

    assert len(NR.load_day_briefs(DAY)) == 1
    assert len(RL._read_news_lines()) == 1


def test_night_pool_news_block_survives_torn_history(tmp_path, monkeypatch):
    """夜池 02:30 的新闻块：撕裂会让整段新闻研究在看板/提示词里消失（不再回退原始
    抓取），而夜池正是 09:35 候选池的上游。"""
    monkeypatch.setattr(NP, "ROOT", tmp_path)
    today = _today()
    _torn_log(tmp_path / "data" / "news_brief" / "history.jsonl",
              [{"ts": f"{today}T08:00:00", "macro": {"view": "美股大跌", "bias": -1.5},
                "themes": []}])

    out = NP._news_block(today)

    assert "美股大跌" in out


# -------------------------------------------- D. 决策池

def test_decision_load_pool_survives_torn_tail(tmp_path):
    p = _torn_log(tmp_path / "decision_pool.jsonl",
                  [{"id": "abc123", "code": "600000.SH", "reason": "止损记录"}])

    assert [r["id"] for r in DT.load_pool(p)] == ["abc123"]
