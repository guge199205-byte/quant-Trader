"""日报的「跨期绩效」区块（daily_report_agent._render_performance_md）。

为什么要单独测：这个区块是**确定性渲染**——数字由脚本直出、不经 LLM。
日报其余部分由 LLM 写，但绩效数字一旦被 LLM 转述就有改写/幻觉风险，
所以这里断言的是"渲染结构 + 关键告警必须出现"。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_daily_report_perf.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import daily_report_agent as D  # noqa: E402


def _perf(**over):
    blk = {
        "returns": {"n_days": 10, "total_return_pct": -6.4},
        "risk": {"max_drawdown": -6.46},
        "discontinuities": [],
        "trading": {"n_roundtrips": 0, "reason": "尚无已平仓回合"},
    }
    blk.update(over)
    return {"agents": {"a1": blk}, "total": blk}


def test_renders_table_with_agents_and_total():
    md = D._render_performance_md(_perf())

    assert "跨期绩效（脚本直出，非 LLM 生成）" in md
    assert "| a1 | 10 | -6.40% | -6.46% | — | — |" in md
    assert "| 合计 |" in md


def test_discontinuity_is_surfaced_inline():
    """记账跳变必须写出来——否则区间收益会被当成真实业绩采信。"""
    md = D._render_performance_md(_perf(discontinuities=[{
        "date": "2026-09-01", "from": 107978.8, "to": 91324.8,
        "change_pct": -15.42, "note": "疑似记账口径变更（非交易损益）"}]))

    assert "2026-09-01" in md and "-15.4%" in md
    assert "非交易损益" in md


def test_small_sample_note_when_annualized_suppressed():
    md = D._render_performance_md(_perf(
        returns={"n_days": 10, "total_return_pct": -6.4,
                 "reason_annualized": "样本 9 个日收益 < 20，年化/Sharpe 无统计意义"}))

    assert "无统计意义" in md


def test_insufficient_data_does_not_crash():
    md = D._render_performance_md(_perf(returns={"n_days": 1, "total_return_pct": None}))

    assert "数据不足" in md


def test_error_input_degrades_gracefully():
    """绩效算不出来不许拖垮整份日报。"""
    assert "本期未能计算" in D._render_performance_md({"error": "ImportError: x"})


def test_roundtrips_show_when_present():
    md = D._render_performance_md(_perf(trading={"n_roundtrips": 4, "win_rate_pct": 75.0}))

    assert "| 4 | 75.0% |" in md
