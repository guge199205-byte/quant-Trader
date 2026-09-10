"""日报事实口径单测（scripts/daily_report_agent.py）。

2026-09-10 实录：当日 7 轮盘中分析全部「本轮分析未执行」（桥假活），日报却写
"8 轮满额运行、流程纪律正常"；异常计数写的是整份日志的累计值 369（当日实际 5）。
两处都是"数了但数错口径"——本文件把口径钉死。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_daily_report_facts.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import daily_report_agent as D  # noqa: E402


def _row(content: str, kind: str | None = None) -> str:
    """真实 log.jsonl 行：无 kind 键的普通轮次 / kind=review 的盘后复盘。"""
    rec = {"timestamp": "2026-09-10T09:52:01+08:00", "signature": "deepseek-v4-pro",
           "new_messages": [{"role": "user", "content": "盘中定时分析"},
                            {"role": "assistant", "content": content}]}
    if kind:
        rec["kind"] = kind
    return json.dumps(rec, ensure_ascii=False)


# ---------- 轮次口径 ----------

def test_rounds_excludes_review_and_counts_unexecuted():
    """当日实录形状：7 个普通轮次（全未执行）+ 1 个 review 行。"""
    text = "\n".join([_row("⚠️ 本轮分析未执行（每小时定时）。")] * 7
                     + [_row("复盘完成", kind="review")])

    assert D._rounds_from_log(text) == (7, 7)


def test_rounds_distinguishes_ok_rounds():
    text = "\n".join([_row("🧠 分析完成：全部 hold"),
                      _row("⚠️ 本轮分析未执行（每小时定时）。"),
                      _row("🧠 分析完成：买入 XYZ")])

    assert D._rounds_from_log(text) == (3, 1)


def test_rounds_tolerates_broken_lines_and_empty_text():
    text = "\n".join([_row("⚠️ 本轮分析未执行。"), "", "{半行 json", "不是json"])

    assert D._rounds_from_log(text) == (1, 1)
    assert D._rounds_from_log("") == (0, 0)


# ---------- 异常行口径 ----------

LOG = "\n".join([
    "[2026-09-08 09:52:01] 轮次失败: 桥断线",
    "[2026-09-08 10:52:01] 轮次失败: 桥断线",
    "[2026-09-10 09:52:01] 轮次失败: 桥假活 asset=0",
    "📰 新闻分子已注入（无时间戳，归属上一行日期）",
    "[2026-09-10 10:52:01] ❌ 调仓失败",
    "[2026-09-10 11:52:01] 一切正常",
    "❌ 无时间戳的错误行，仍归属 09-10",
])


def test_err_lines_only_counts_target_date():
    """旧口径把 30 天累计值（实况 369）当当日值；当日实际 3 行。"""
    assert D._err_lines_for_date(LOG, "2026-09-10") == 3
    assert D._err_lines_for_date(LOG, "2026-09-08") == 2


def test_err_lines_ignores_lines_before_first_timestamp():
    text = "启动日志 失败: 引导阶段\n[2026-09-10 09:00:00] 失败: 桥"

    assert D._err_lines_for_date(text, "2026-09-10") == 1
    assert D._err_lines_for_date(text, "2026-09-09") == 0


# ---------- 执行通道块 ----------

def test_exec_channel_reports_fake_alive_and_closed_switch(tmp_path, monkeypatch):
    """日报 LLM 只有拿到这些字段才可能把"链路故障"写进正文。"""
    monkeypatch.setattr(D, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs" / "live_llm_trade_state.json").write_text(json.dumps(
        {"day": "2026-09-10", "ok": False, "orders_attempted": False,
         "note": "bridge_account_query"}), encoding="utf-8")
    (tmp_path / "logs" / "live_analysis_state.json").write_text(json.dumps(
        {"last_good_sample_ts": "2026-09-09T15:00:00", "last_sample_asset": 296732.4}),
        encoding="utf-8")
    (tmp_path / "configs" / "intraday_exec.json").write_text(
        '{"enabled": false}', encoding="utf-8")
    (tmp_path / "data" / "live_watch.json").write_text(json.dumps(
        {"deepseek-v4-pro": [], "glm-5.3-flash": [{"code": "600309.SH"}]}),
        encoding="utf-8")
    (tmp_path / "logs" / "rt_status.json").write_text(json.dumps(
        {"account": {"ok": False, "error": "asset=0 positions=0"}}), encoding="utf-8")

    ec = D._exec_channel([{"rounds_failed": 7}, {"rounds_failed": 0}])

    assert ec["rounds_failed"] == 7
    assert ec["trade_state"]["day"] == "2026-09-10" and ec["trade_state"]["ok"] is False
    assert ec["exec_enabled"] is False
    assert ec["last_good_account_ts"] == "2026-09-09T15:00:00"
    assert ec["watch_rules"] == {"glm-5.3-flash": 1}          # 空列表不进（不是条件位）
    assert ec["account_probe"]["ok"] is False


def test_exec_channel_degrades_gracefully_without_files(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ROOT", tmp_path)

    ec = D._exec_channel([])

    assert ec["rounds_failed"] == 0
    assert ec["trade_state"] == {} and ec["exec_enabled"] is False
    assert ec["watch_rules"] == {} and ec["account_probe"] == {"ok": None, "error": None}
    assert "preflight_ok" not in ec
