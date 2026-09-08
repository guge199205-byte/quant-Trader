"""批量回测（lab_batch）：股票池解析 + 聚合 + 结果落盘。

聚合口径容易被静默搞错（均值/中位数/跑赢比例），这里用构造数据锁定；
股票池用例读 quantdb 的真实成分文件，quantdb 未挂载时跳过。

运行：.venv/bin/python -m pytest tests/test_lab_batch.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_PINE_SITE = (Path(__file__).resolve().parents[1]
              / "pine_lab/.venv/lib/python3.14/site-packages")
if _PINE_SITE.is_dir() and str(_PINE_SITE) not in sys.path:
    sys.path.append(str(_PINE_SITE))

from backend.services import lab_batch as lb  # noqa: E402
from backend.services import market_lab as ml  # noqa: E402

_HAS_DB = (Path("/data/quantdb/1_kline_data").is_dir()
           or (Path.home() / "projects/quantmind/data/quantdb/1_kline_data").is_dir())


def _row(symbol: str, strategy: str, net, bh, dd=10.0, sharpe=0.5, pf=1.5,
         win=40.0, trades=10.0) -> dict:
    return {"symbol": symbol, "name": symbol, "strategy": strategy,
            "net_pct": net, "buy_hold_pct": bh, "dd_pct": dd, "sharpe": sharpe,
            "sortino": None, "profit_factor": pf, "win_rate_pct": win,
            "trades": trades, "avg_bars": 12.0, "bars": 1000,
            "start": "2016-01-04", "end": "2026-09-07"}


class TestAggregate:
    def test_mean_median_and_beat_ratio(self):
        rows = [_row("A.SH", "s1", 10.0, 5.0),     # 跑赢
                _row("B.SH", "s1", 30.0, 50.0),    # 跑输
                _row("C.SH", "s1", 20.0, 15.0),    # 跑赢
                _row("D.SH", "s1", 40.0, 100.0)]   # 跑输
        agg = lb.aggregate(rows)[0]
        assert agg["symbols"] == 4
        assert agg["mean_net_pct"] == pytest.approx(25.0)
        assert agg["median_net_pct"] == pytest.approx(25.0)   # (20+30)/2
        assert agg["beat_bh_pct"] == pytest.approx(50.0)

    def test_none_values_excluded_from_mean(self):
        rows = [_row("A.SH", "s1", 10.0, 5.0),
                _row("B.SH", "s1", None, 5.0, sharpe=None, pf=None)]
        agg = lb.aggregate(rows)[0]
        assert agg["mean_net_pct"] == pytest.approx(10.0)
        assert agg["mean_sharpe"] == pytest.approx(0.5)
        assert agg["symbols"] == 2
        assert agg["beat_bh_pct"] == pytest.approx(100.0)   # 只有 A 参与比较

    def test_ranking_sorted_by_mean_desc(self):
        rows = [_row("A.SH", "weak", 1.0, 0.0), _row("A.SH", "strong", 99.0, 0.0)]
        assert [a["strategy"] for a in lb.aggregate(rows)] == ["strong", "weak"]

    def test_unknown_strategy_id_falls_back_to_id(self):
        """结果文件比注册表旧（策略已删）时，名字退回 id 而不是 KeyError。"""
        agg = lb.aggregate([_row("A.SH", "retired_strategy", 1.0, 0.0)])[0]
        assert agg["name"] == "retired_strategy"


class TestRow:
    def test_maps_stats_to_flat_row(self):
        result = {"stats": {
            "Net profit": {"value": 12345.0, "pct": 12.345},
            "Buy & hold return": {"value": 999.0, "pct": 9.99},
            "Max equity drawdown": {"value": -8000.0, "pct": 8.0},
            "Sharpe ratio": {"value": 0.77, "pct": None},
            "Profit factor": {"value": 1.9, "pct": None},
            "Percent profitable": {"value": None, "pct": 55.5},
            "Total trades": {"value": 7.0, "pct": None},
            "Avg # bars in trades": {"value": 11.5, "pct": None},
        }, "meta": {"start": "2016-01-04", "end": "2026-09-07"}}
        row = lb._row("600309.SH", "万华化学", "sma_cross", result, 2595)
        assert row["net_pct"] == pytest.approx(12.345)
        assert row["buy_hold_pct"] == pytest.approx(9.99)
        assert row["dd_pct"] == pytest.approx(8.0)
        assert row["win_rate_pct"] == pytest.approx(55.5)
        assert row["trades"] == pytest.approx(7.0)
        assert row["bars"] == 2595
        assert row["name"] == "万华化学"

    def test_infinite_profit_factor_becomes_none(self):
        """全胜标的：PyneCore 写 Profit factor=0（真值 ∞）→ 必须当缺失，不能当 0 拉低均值。"""
        stats = {"Profit factor": {"value": 0.0, "pct": None},
                 "Gross loss": {"value": 0.0, "pct": 0.0}}
        ml._fix_infinite_pf(stats)
        assert stats["Profit factor"]["value"] is None

    def test_real_profit_factor_untouched(self):
        stats = {"Profit factor": {"value": 1.8, "pct": None},
                 "Gross loss": {"value": 5000.0, "pct": 5.0}}
        ml._fix_infinite_pf(stats)
        assert stats["Profit factor"]["value"] == pytest.approx(1.8)


class TestPersistence:
    def test_save_list_load_roundtrip(self, tmp_path):
        payload = {"run_id": "20260908-test", "created": "2026-09-08 22:00:00",
                   "pool": "hs300", "pool_label": "沪深300", "adj": "backward",
                   "strategies": [{"id": "sma_cross", "name": "双均线交叉"}],
                   "universe": 3, "counts": {"rows": 1, "skipped": 0, "errors": 0},
                   "ranking": [], "rows": [_row("A.SH", "sma_cross", 5.0, 1.0)],
                   "skipped": [], "errors": []}
        lb.save_run(payload, tmp_path)
        assert (tmp_path / "20260908-test.json").is_file()
        assert not list(tmp_path.glob("*.tmp"))          # 原子替换后无残留
        runs = lb.list_runs(tmp_path)
        assert runs[0]["run_id"] == "20260908-test"
        assert runs[0]["universe"] == 3
        assert "rows" not in runs[0]                     # 列表只读摘要
        assert lb.load_run("20260908-test", tmp_path)["rows"][0]["symbol"] == "A.SH"

    def test_load_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            lb.load_run("nope", tmp_path)

    def test_load_run_rejects_path_traversal(self, tmp_path):
        """run_id 来自 URL，不能让它跳出结果目录。"""
        with pytest.raises(FileNotFoundError):
            lb.load_run("../../etc/passwd", tmp_path)

    def test_list_runs_empty_dir(self, tmp_path):
        assert lb.list_runs(tmp_path) == []
        assert lb.list_runs(tmp_path / "not-exist") == []


class TestFromFile:
    def test_plain_list(self, tmp_path):
        p = tmp_path / "codes.json"
        p.write_text(json.dumps(["600309", "000001.SZ"]), encoding="utf-8")
        assert lb._from_file(str(p)) == ["600309", "000001.SZ"]

    def test_objects_and_wrapper_key(self, tmp_path):
        p = tmp_path / "watch.json"
        p.write_text(json.dumps({"candidates": [{"code": "600309.SH"},
                                                {"symbol": "000001.SZ"}]}),
                     encoding="utf-8")
        assert lb._from_file(str(p)) == ["600309.SH", "000001.SZ"]


@pytest.mark.skipif(not _HAS_DB, reason="quantdb 未挂载")
class TestResolvePool:
    def test_index_pool_sizes(self):
        assert len(lb.resolve_pool("sz50")) == 50
        assert len(lb.resolve_pool("hs300")) == 300
        assert len(lb.resolve_pool("zz1000")) == 1000

    def test_index_pool_codes_normalized_and_named(self):
        members = lb.resolve_pool("sz50")
        assert all(m["code"].endswith((".SH", ".SZ")) for m in members)
        assert any(m["name"] for m in members)            # instrument_detail 能取到中文名
        assert len({m["code"] for m in members}) == len(members)

    def test_limit_applied(self):
        assert len(lb.resolve_pool("hs300", limit=5)) == 5

    def test_all_excludes_st_and_delisted(self):
        import duckdb
        root = ml._root()
        p = root / "2_base_sector/instrument_detail/instrument_detail.parquet"
        total = duckdb.connect().execute(
            "SELECT count(*) FROM read_parquet(?) WHERE Symbol LIKE '%.%'", [str(p)]
        ).fetchone()[0]
        codes = lb._all_stocks()
        assert 0 < len(codes) < total                     # 确实剔掉了一批
        bad = duckdb.connect().execute(
            "SELECT count(*) FROM read_parquet(?) WHERE Symbol LIKE '%.%' "
            "AND (IsSTGP = '1' OR IsQuitGP = '1')", [str(p)]).fetchone()[0]
        assert bad > 0                                    # 数据里确实有 ST/退市股

    def test_unknown_pool_raises(self):
        with pytest.raises(ValueError):
            lb.resolve_pool("no_such_pool")

    def test_pool_label(self):
        assert lb.pool_label("hs300") == "沪深300"
        assert lb.pool_label("all") == "全市场"
        assert lb.pool_label("file:/tmp/x.json") == "x.json"


@pytest.mark.skipif(not _HAS_DB, reason="quantdb 未挂载")
class TestRunBatch:
    def test_end_to_end_single_symbol(self):
        pytest.importorskip("pynecore", reason="PyneCore 运行时未安装")
        symbols = lb.resolve_pool("sz50", limit=1)
        r = lb.run_batch(symbols, ["sma_cross", "donchian"])
        assert not r["errors"]
        assert len(r["rows"]) == 2
        for row in r["rows"]:
            assert row["bars"] >= lb.MIN_BARS
            assert row["net_pct"] is not None
            assert row["start"] < row["end"]
        # 批量只留统计，不带逐笔/净值（体积和速度都不允许）
        assert all("equity" not in row for row in r["rows"])

    def test_short_history_skipped(self):
        """K线不足 min_bars 的标的进 skipped 而不是抛错。"""
        pytest.importorskip("pynecore", reason="PyneCore 运行时未安装")
        r = lb.run_batch([{"code": "600309.SH", "name": "万华化学"}], ["sma_cross"],
                         min_bars=999999)
        assert not r["rows"]
        assert r["skipped"][0]["symbol"] == "600309.SH"
        assert "K线不足" in r["skipped"][0]["reason"]

    def test_unknown_symbol_is_skipped(self):
        """不存在的代码：load_klines 返回空 → 进 skipped，不抛异常拖垮整批。"""
        r = lb.run_batch([{"code": "999999.SH", "name": "不存在"}], ["sma_cross"],
                         min_bars=1)
        assert not r["rows"]
        assert r["skipped"] and r["skipped"][0]["bars"] == 0
