"""抽样审计：名额分摊 / 失败归类 / 汇总统计。

汇总里的数字是判断「链路还能不能打」的依据，口径错了比没有更糟，所以逐条锁住。

运行：.venv/bin/python -m pytest tests/test_pine_pipeline_audit.py -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import pine_pipeline_audit as a  # noqa: E402


class TestAllocate:
    def test_sums_to_n_and_covers_every_category(self):
        counts = {"趋势跟踪": 537, "突破策略": 155, "均值回归": 112, "剥头皮": 68,
                  "网格": 47, "其他": 38, "指标": 22, "组合": 19, "套利": 3}
        got = a.allocate(counts, 30)
        assert sum(got.values()) == 30
        assert all(v >= 1 for v in got.values())
        # 大分类名额更多，但不会吞掉小分类
        assert got["趋势跟踪"] > got["套利"]

    def test_n_smaller_than_categories(self):
        counts = {"a": 10, "b": 5, "c": 1}
        got = a.allocate(counts, 2)
        assert sum(got.values()) == 2 and set(got) == {"a", "b"}


class TestClassify:
    def test_done(self):
        assert a.classify({"stage": "done"}) == "通过"
        assert a.classify({"stage": "done"}, trades=5) == "通过"

    def test_done_but_never_traded_is_its_own_bucket(self):
        """跑通但 0 成交：链路是通的，可很可能转写把开仓条件弄丢了，不能算真通过。"""
        assert a.classify({"stage": "done"}, trades=0) == "通过（0 成交）"

    def test_truncation(self):
        assert a.classify({"stage": "transpile",
                           "error": "转写失败：模型输出被 max_tokens 截断"}) == "转写截断"

    def test_static_problems(self):
        assert a.classify({"stage": "static", "problems": ["禁止动态执行：eval("]}) == "静态检查不过"

    def test_runtime_type_error(self):
        assert a.classify({"stage": "backtest",
                           "error": "TypeError: 'float' object is not subscriptable"}) == "运行时 TypeError"

    def test_timeout(self):
        assert a.classify({"stage": "backtest", "error": "回测超时（>180s）"}) == "backtest超时"


class TestErrorOf:
    def test_prefers_concrete_static_problem(self):
        """「静态检查未通过」本身没信息量，报表要显示第一条具体问题。"""
        job = {"stage": "static", "error": "静态检查未通过",
               "problems": ["语法错误：invalid syntax（第 32 行）", "缺少必需结构：@pyne"]}
        assert a._error_of(job) == "语法错误：invalid syntax（第 32 行）；缺少必需结构：@pyne"

    def test_falls_back_to_error_when_no_problems(self):
        assert a._error_of({"stage": "backtest", "error": "TypeError: x"}) == "TypeError: x"


class TestSummarize:
    def test_counts_first_try_repair_and_failures(self, tmp_path, monkeypatch):
        tdir = tmp_path / "pine_transpile"
        cases = {
            "0001": ({"status": "done", "stage": "done"},
                     {"title": "甲", "elapsed_sec": 5.0, "repairs": []},
                     {"trades": 12, "stats": {"Net profit": {"pct": 8.5}}}),
            "0002": ({"status": "done", "stage": "done"},
                     {"title": "乙", "elapsed_sec": 7.0, "repairs": [{"error": "boom"}]},
                     {"trades": 3, "stats": {"Net profit": {"pct": -2.0}}}),
            "0003": ({"status": "failed", "stage": "backtest", "error": "TypeError: x"},
                     {"title": "丙", "elapsed_sec": 6.0}, None),
            "0004": ({"status": "done", "stage": "done"},
                     {"title": "丁", "elapsed_sec": 4.0}, {"trades": 0, "stats": {}}),
        }
        for iid, (job, meta, rep) in cases.items():
            d = tdir / iid
            d.mkdir(parents=True)
            (d / "job.json").write_text(json.dumps(job), encoding="utf-8")
            (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            if rep:
                (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        monkeypatch.setattr(a, "TRANSPILE_DIR", tdir)

        manifest = tmp_path / "sample.json"
        manifest.write_text(json.dumps({
            "symbol": "600309.SH", "ids": ["0001", "0002", "0003", "0004"],
            "items": [{"id": "0001", "category": "趋势跟踪"}]}), encoding="utf-8")

        got = a.summarize(manifest)
        assert got["total"] == 4 and got["done"] == 3
        assert got["first_try"] == 2 and got["after_repair"] == 1
        assert got["zero_trade"] == 1
        assert got["avg_transpile_sec"] == 5.5
        assert got["buckets"] == {"通过": 2, "通过（0 成交）": 1, "运行时 TypeError": 1}
        assert got["finished"] is True
        assert got["rows"][0]["net_pct"] == 8.5
        assert got["rows"][0]["category"] == "趋势跟踪"

    def test_unfinished_when_job_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(a, "TRANSPILE_DIR", tmp_path / "nope")
        manifest = tmp_path / "sample.json"
        manifest.write_text(json.dumps({"ids": ["0009"], "items": []}), encoding="utf-8")
        got = a.summarize(manifest)
        assert got["finished"] is False and got["rows"][0]["status"] == "未跑"


class TestUnique:
    def test_same_second_does_not_clobber(self, tmp_path):
        """真实踩过：回归清单和抽样清单同一秒写出，后者把前者顶掉了。"""
        first = a._unique(tmp_path / "sample-20260909-000000.json")
        first.write_text("x", encoding="utf-8")
        second = a._unique(tmp_path / "sample-20260909-000000.json")
        assert second.name == "sample-20260909-000000-2.json"
        assert first.read_text(encoding="utf-8") == "x"


class TestFailedIds:
    def test_picks_every_non_pass_row_but_keeps_zero_trade_out(self, tmp_path):
        """0 成交是「链路通了但策略不触发」，重转写也白搭，不进回归队列。"""
        rep = tmp_path / "report.json"
        rep.write_text(json.dumps({"rows": [
            {"id": "0001", "verdict": "通过"},
            {"id": "0002", "verdict": "静态检查不过"},
            {"id": "0003", "verdict": "通过（0 成交）"},
            {"id": "0004", "verdict": "backtest超时"},
        ]}), encoding="utf-8")
        assert a.failed_ids(rep) == ["0002", "0004"]

    def test_missing_report_is_empty(self, tmp_path):
        assert a.failed_ids(tmp_path / "nope.json") == []
