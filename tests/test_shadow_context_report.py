"""P1 影子 A/B 周报（scripts/shadow_context_report.py）纯逻辑测试。

判据口径：noise = diff(A1, A2)（同提示词重跑，温度 0.3 的采样噪声），
effect = diff(A1, B)（+盘面+新闻分子）。只有 effect 显著高于 noise 才支持上线。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_shadow_context_report.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import shadow_context_report as R  # noqa: E402


def _d(action, code="600362.SH", pct=0.2, reason="r"):
    return {"action": action, "code": code, "pct": pct, "reason": reason}


def _arm(decisions):
    return {"decisions": decisions, "fail": "", "usage": {"total_tokens": 10}}


def _rec(date, a1, a2, b, forced=False, agent="deepseek-v4-pro"):
    return {"date": date, "ts": f"{date}T09:40:00+08:00", "mode": "shadow_context_ab",
            "forced": forced, "market_state": True, "news_present": True,
            "news_age_min": 12.0, "blocks_text": "B",
            "agents": {agent: {"prompt_len_a": 100, "prompt_len_b": 150,
                               "a1": _arm(a1), "a2": _arm(a2), "b": _arm(b)}}}


# ---------- 决策对比 ----------

def test_compare_same_decisions_no_changes():
    a = [_d("hold"), _d("buy", "000001.SZ")]
    assert R.compare_decisions(a, list(a)) == []


def test_compare_detects_action_and_pct_and_code_delta():
    a1 = [_d("hold", "600362.SH"), _d("buy", "000001.SZ", 0.2)]
    b = [_d("sell", "600362.SH"), _d("buy", "000001.SZ", 0.3), _d("buy", "600519.SH")]

    changes = {c["code"]: c for c in R.compare_decisions(a1, b)}

    assert changes["600362.SH"]["from"].startswith("hold")
    assert changes["600362.SH"]["to"].startswith("sell")
    assert changes["000001.SZ"]["from"].endswith("0.2")
    assert changes["000001.SZ"]["to"].endswith("0.3")
    assert changes["600519.SH"]["from"] == "absent"
    assert R.compare_decisions(None, b)          # None 臂不炸，按空处理


# ---------- 汇总与渲染 ----------

def test_summarize_splits_noise_from_effect_and_skips_forced():
    same = [_d("hold")]
    recs = [
        # D1：A1=A2（无噪声）、B 变了 → effect
        _rec("2026-09-21", same, same, [_d("buy")]),
        # D2：A1≠A2（噪声）、B 与 A1 相同 → noise
        _rec("2026-09-22", same, [_d("sell")], same),
        # D3：forced 冒烟 → 不计入
        _rec("2026-09-23", same, [_d("sell")], [_d("buy")], forced=True),
    ]

    s = R.summarize(recs)

    assert s["days"] == 2
    assert s["per_agent"]["deepseek-v4-pro"]["noise_days"] == 1
    assert s["per_agent"]["deepseek-v4-pro"]["effect_days"] == 1
    assert s["days"] == 2                                   # forced 记录被排除
    assert all("2026-09-23" not in d for d in s["dates"])


def test_summarize_excludes_failed_arms_and_surfaces_counts():
    """评审 HIGH-1：失败臂（decisions=None）不参与判定——否则会被折算成
    "全仓缺席 → 差异"，把纯 API 失败印成 effect，且 B 臂提示词更长、失败率
    天然更高，偏差方向恰是"支持上线"。"""
    same = [_d("hold")]
    ok = _rec("2026-09-21", same, same, same)
    bad = _rec("2026-09-22", same, same, same)
    bad["agents"]["deepseek-v4-pro"]["b"] = {"decisions": None, "fail": "api_failed",
                                             "usage": None}

    s = R.summarize([ok, bad])
    a = s["per_agent"]["deepseek-v4-pro"]

    assert a["days"] == 1 and a["effect_days"] == 0          # 失败日不入判定
    assert a["fails"]["b"] == 1                              # 但失败数必须可见


def test_summarize_dedups_same_day_and_flags_missing_blocks():
    """评审 MEDIUM-2/HIGH-1：同 (日期, agent) 多行保留最后一条（否则天数灌水）；
    块缺失日单独计数（B 臂没加成时 effect 无意义）。"""
    same = [_d("hold")]
    first = _rec("2026-09-21", same, same, [_d("buy")])      # 早行：有 effect
    last = _rec("2026-09-21", same, same, same)              # 后行：保留（无 effect）
    last["news_present"] = False

    s = R.summarize([first, last])
    a = s["per_agent"]["deepseek-v4-pro"]

    assert a["days"] == 1 and a["effect_days"] == 0          # 同日只算一次、取最后
    assert s["block_missing_dates"] == ["2026-09-21"]
    assert a["missing_block_days"] == 1


def test_compare_treats_missing_and_dirty_pct_as_distinct():
    """pct 三态参与比较（评审 MEDIUM）：未表达比例 / 显式 0 / 脏值是三种语义。"""
    unspecified = [{"action": "buy", "code": "600362.SH", "pct": 0.0,
                    "pct_given": False}]
    explicit = [{"action": "buy", "code": "600362.SH", "pct": 0.0,
                 "pct_given": True}]
    dirty = [{"action": "buy", "code": "600362.SH", "pct": 0.2,
              "pct_bad_raw": "'0.3股'"}]

    assert R.compare_decisions(unspecified, explicit)
    assert R.compare_decisions(explicit, dirty)
    assert not R.compare_decisions(explicit, list(explicit))


def test_render_markdown_contains_rates_and_hint():
    recs = [_rec("2026-09-21", [_d("hold")], [_d("hold")], [_d("buy")])]

    md = R.render(R.summarize(recs), "2026-09-21", "2026-09-21")

    assert "2026-09-21" in md and "deepseek-v4-pro" in md
    assert "noise" in md and "effect" in md
    assert "采样噪声" in md        # 解读指引必须写明温度噪声的存在


# ---------- 文件读取与 CLI ----------

def test_load_records_filters_range_and_bad_lines(tmp_path):
    d = tmp_path
    (d / "2026-09-21.jsonl").write_text(
        json.dumps(_rec("2026-09-21", [_d("hold")], [_d("hold")], [_d("hold")])) + "\n"
        + "不是 json\n", encoding="utf-8")
    (d / "2026-09-25.jsonl").write_text(
        json.dumps(_rec("2026-09-25", [_d("hold")], [_d("hold")], [_d("hold")])) + "\n",
        encoding="utf-8")

    recs = R.load_records(d, "2026-09-20", "2026-09-22")

    assert [r["date"] for r in recs] == ["2026-09-21"]


def test_cli_prints_and_writes_report(tmp_path, capsys):
    from datetime import datetime, timedelta, timezone

    # 日期相对"今天"生成（--days 窗口按当天收窗；钉死日期=定时炸弹，见 09-18 教训）
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    (tmp_path / f"{today}.jsonl").write_text(
        json.dumps(_rec(today, [_d("hold")], [_d("hold")], [_d("buy")])) + "\n",
        encoding="utf-8")
    out = tmp_path / "report.md"

    rc = R.main(["--dir", str(tmp_path), "--days", "7", "--out", str(out)])

    assert rc == 0
    assert "影子 A/B" in capsys.readouterr().out
    assert "deepseek-v4-pro" in out.read_text(encoding="utf-8")


def test_cli_rejects_zero_or_negative_days(tmp_path, capsys):
    assert R.main(["--dir", str(tmp_path), "--days", "0"]) == 2
    assert "--days" in capsys.readouterr().err


def test_cli_out_failure_returns_nonzero(tmp_path):
    """落盘失败必须 rc≠0（评审 M-4：cron 视角的"成功"不能是没产物）。"""
    (tmp_path / "block").write_text("x", encoding="utf-8")   # 以文件挡住 mkdir

    rc = R.main(["--dir", str(tmp_path), "--days", "7",
                 "--out", str(tmp_path / "block" / "r.md")])

    assert rc == 1
