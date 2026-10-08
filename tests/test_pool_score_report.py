"""P2 读数面：按候选池位置分档看远期超额（scripts/pool_score_report.py）。

本报告回答 P2 的唯一问题：**池内分数更低（=排名更靠后）的买入，远期超额更差吗？**
分档先按「模型有没有看到这只票」切，再按 rank 分档——因为 09-17 实录里
20 只池有 11 只（含 rank 1/2/3/4/6/8）因买不起被整行剔除，"低分买入"与
"资金买不起高分票"是两件方向相反的事，混在一档里会得出反向结论。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_pool_score_report.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pool_score_report as R  # noqa: E402


def _rec(rank, excess, state="shown", kind="bullish", date="2026-09-21", t5=None, agent="a"):
    fwd = {"t1": {"ret": excess, "bench": 0.0, "excess": excess}}
    if t5 is not None:
        fwd["t5"] = {"ret": t5, "bench": 0.0, "excess": t5}
    rec = {"agent": agent, "date": date, "kind": kind, "action": "buy",
           "code": f"6000{rank if rank is not None else 99:02d}.SH", "fwd": fwd,
           "pool_ctx": {"file": "p.json", "n_shown": 9, "n_dropped": 11,
                        "state": state, "shown": state == "shown",
                        "rank": rank, "score": 0.7, "fusion": None}}
    return rec


# ---------- 分档 ----------

def test_rank_band_splits_off_pool_and_tail():
    assert R.rank_band(None, "off") == "池外"
    assert R.rank_band(1) == "#1-5"
    assert R.rank_band(5) == "#1-5"
    assert R.rank_band(6) == "#6-10"
    assert R.rank_band(11) == "#11-20"
    assert R.rank_band(21) == "#21+"


def test_rank_band_separates_unranked_pool_rows_from_off_pool():
    """池内行没标排名 ≠ 池外：后者是"模型自选"，与前者的读法完全相反。"""
    assert R.rank_band(None, "dropped") == "未标排名"
    assert R.rank_band(None, "shown") == "未标排名"
    assert R.rank_band("3.5", "shown") == "未标排名"
    assert R.rank_band(float("inf"), "shown") == "未标排名"   # OverflowError 已兜
    assert R.rank_band(float("nan"), "shown") == "未标排名"


def test_state_of_prefers_explicit_state_over_rank_inference():
    """显式 state 优先；旧戳（无 state）才按 rank 反推。"""
    assert R.state_of({"state": "dropped", "rank": None}) == "dropped"
    assert R.state_of({"state": "off", "rank": 7}) == "off"      # 显式压过反推
    assert R.state_of({"shown": False, "rank": 3}) == "dropped"  # 旧戳
    assert R.state_of({"shown": False, "rank": None}) == "off"   # 旧戳
    assert R.state_of(None) == ""


def test_group_splits_shown_dropped_and_offpool():
    """三态必须分成三组：被资金闸剔除的高分票不能混进"选择集内低分档"。"""
    assert R.group_of({"state": "shown", "shown": True, "rank": 3}) == "选择集内"
    assert R.group_of({"state": "dropped", "shown": False, "rank": 3}) == "池内被剔除"
    assert R.group_of({"state": "off", "shown": False, "rank": None}) == "池外"
    assert R.group_of(None) == ""            # 无戳 → 不进任何组


def test_dropped_without_rank_still_grouped_as_dropped():
    """池行缺 rank 时，被剔除的票仍进"池内被剔除"（09-18 审查 MEDIUM 的主场景）。"""
    recs = [_rec(None, 0.02, state="dropped"), _rec(None, 0.01, state="off")]

    s = R.summarize(recs)

    assert s["groups"]["池内被剔除"]["未标排名"]["n"] == 1
    assert s["groups"]["池外"]["池外"]["n"] == 1


# ---------- 汇总 ----------

def test_summarize_buckets_by_group_and_band():
    recs = [
        _rec(2, 0.02), _rec(3, 0.01),                # 选择集内 #1-5
        _rec(12, -0.01),                             # 选择集内 #11-20
        _rec(1, 0.03, state="dropped"),              # 池内被剔除（高分票买不起）
        _rec(None, 0.005, state="off"),              # 池外
        _rec(4, 0.99, kind="position"),              # 非 bullish → 不计
    ]

    s = R.summarize(recs)
    g = s["groups"]["选择集内"]["#1-5"]

    assert (s["n_total"], s["n_ctx"], s["n_used"]) == (5, 5, 5)   # 第 6 条是 position
    assert (g["n"], round(g["mean"], 4)) == (2, 0.015)
    assert s["groups"]["池内被剔除"]["#1-5"]["n"] == 1
    assert s["groups"]["池外"]["池外"]["n"] == 1


def test_summarize_treats_nan_and_bad_values_as_pending():
    """NaN/非数值不得静默进均值：NaN 会把 mean 染成 nan、胜率算错（审查 MEDIUM）。"""
    recs = [_rec(2, 0.02), _rec(3, float("nan")), _rec(4, "oops"), _rec(5, float("inf"))]

    s = R.summarize(recs)

    assert s["n_used"] == 1 and s["n_pending"] == 3
    g = s["groups"]["选择集内"]["#1-5"]
    assert g["n"] == 1 and g["mean"] == 0.02


def test_summarize_counts_coverage_and_missing_stamp():
    """覆盖面上报告面：没插桩的记录（旧格式）必须显式计数，否则会以为样本够。"""
    recs = [_rec(2, 0.01), {k: v for k, v in _rec(3, 0.01).items() if k != "pool_ctx"}]

    s = R.summarize(recs)

    assert s["n_total"] == 2 and s["n_ctx"] == 1 and s["n_unstamped"] == 1


def test_summarize_tracks_horizons_separately():
    """t1 覆盖全、t5 稀疏——两个 horizon 的 n 必须各算各的（不能拿 t1 的 n 冒充）。"""
    recs = [_rec(2, 0.02, t5=0.05), _rec(3, -0.01)]

    s = R.summarize(recs)
    g = s["groups"]["选择集内"]["#1-5"]

    assert g["n"] == 2 and g["mean"] == 0.005
    assert g["n_t5"] == 1 and g["mean_t5"] == 0.05


def test_summarize_ignores_records_without_forward_return():
    """入场价/远期收益还没回填（新决策）→ 不入统计，但算进"未回填"。"""
    fresh = _rec(2, 0.0)
    fresh["fwd"] = {}
    s = R.summarize([_rec(3, 0.01), fresh])

    assert s["n_used"] == 1 and s["n_pending"] == 1


# ---------- 判读与渲染 ----------

def test_verdict_refuses_conclusion_below_min_sample():
    """样本 < MIN_SAMPLE 时不得出现任何"支持设闸"的措辞（P2 的失效方向）。"""
    s = R.summarize([_rec(2, 0.02), _rec(12, -0.01)])

    md = R.render(s, "2026-09-15", "2026-09-21")

    assert "样本不足" in md
    assert "建议设闸" not in md and "支持设闸" not in md


def test_verdict_states_required_evidence_when_sample_insufficient():
    """样本不足时判读必须**写明为什么不下结论**（断言具体条目，不靠"样本"二字）。

    旧断言 `"样本" in md or "MIN_SAMPLE" in md` 恒真（固定文案里两者必有其一），
    删掉判读逻辑也不会红——09-18 审查 LOW 实录。
    """
    md = R.render(R.summarize([]), "2026-09-15", "2026-09-21")

    assert "至少要有两个达标档位" in md
    assert "更不应据此设闸" in md
    assert "资金规模/池子价格结构" in md          # 被剔除档的口径说明必须在


def test_render_orders_bands_by_rank_not_alphabetically():
    """档位必须按排名序渲染：#6-10 排在 #11-20 之前（字典序会把它排到最后）。"""
    recs = [_rec(2, 0.01), _rec(7, 0.02), _rec(12, 0.03), _rec(25, 0.04)]

    md = R.render(R.summarize(recs), "2026-09-15", "2026-09-21")
    order = [b for b in ("#1-5", "#6-10", "#11-20", "#21+") if b in md]

    assert order == ["#1-5", "#6-10", "#11-20", "#21+"]
    assert md.index("#6-10") < md.index("#11-20") < md.index("#21+")


def test_render_flags_missing_pool_file_as_path_problem():
    """池文件不在 ≠ 没插桩：零样本时的诊断方向不能指错（审查 LOW）。"""
    md = R.render(R.summarize([]), "2026-09-15", "2026-09-21", pool_missing=True)

    assert "路径问题" in md and "不存在" in md


def test_render_omits_table_when_no_samples():
    """零样本时不许渲染表头——空表会被读成"跑通了但没数据"，掩盖两类故障。

    （本文件自己踩过：插入缺失文件告警时把零样本早退挤进了 if 里，
    结果是"无可用样本"后面跟着一张空表。）
    """
    md = R.render(R.summarize([]), "2026-09-15", "2026-09-21")

    assert "无可用样本" in md
    assert "| 分组 | 档位 |" not in md
    assert "## 判读" in md                      # 结论段仍要在


def test_render_surfaces_unstamped_records_as_coverage_not_silence():
    """未插桩条数必须印出来——否则"样本够了"可能是插桩之后才够的假象。"""
    recs = [_rec(2, 0.01)] + [{k: v for k, v in _rec(3, 0.01).items()
                               if k != "pool_ctx"} for _ in range(4)]

    md = R.render(R.summarize(recs), "2026-09-15", "2026-09-21")

    assert "未插桩 4 条" in md


# ---------- 文件读取与 CLI ----------

def test_load_records_filters_by_date_range(tmp_path):
    p = tmp_path / "pool.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        _rec(2, 0.01, date="2026-09-14"),
        _rec(3, 0.01, date="2026-09-21"),
        _rec(4, 0.01, date="2026-09-25"),
    ]) + "\n不是 json\n", encoding="utf-8")

    recs = R.load_records(p, "2026-09-15", "2026-09-21")

    assert [r["date"] for r in recs] == ["2026-09-21"]


def test_load_records_missing_file_returns_empty(tmp_path):
    assert R.load_records(tmp_path / "nope.jsonl") == []


def test_cli_prints_and_writes_report(tmp_path, capsys):
    from datetime import datetime, timedelta, timezone

    # 日期相对"今天"生成（--days 窗口按当天收窗；钉死日期 = 定时炸弹，见 09-18 教训）
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    p = tmp_path / "pool.jsonl"
    p.write_text(json.dumps(_rec(2, 0.01, date=today)) + "\n", encoding="utf-8")
    out = tmp_path / "report.md"

    rc = R.main(["--pool", str(p), "--days", "30", "--out", str(out)])

    assert rc == 0
    assert "P2 候选池位置" in capsys.readouterr().out
    assert "入统计 1 条" in out.read_text(encoding="utf-8")


def test_cli_rejects_zero_or_negative_days(tmp_path, capsys):
    assert R.main(["--pool", str(tmp_path / "x.jsonl"), "--days", "0"]) == 2
    assert "--days" in capsys.readouterr().err


def test_cli_out_failure_returns_nonzero(tmp_path):
    """落盘失败必须 rc≠0（cron 视角的"成功"不能是没产物）。"""
    (tmp_path / "block").write_text("x", encoding="utf-8")   # 以文件挡住 mkdir

    rc = R.main(["--pool", str(tmp_path / "x.jsonl"), "--days", "30",
                 "--out", str(tmp_path / "block" / "r.md")])

    assert rc == 1
