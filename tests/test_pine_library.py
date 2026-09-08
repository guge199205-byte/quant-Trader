"""Pine 策略库：索引构建（来源优先级 / 缩进判定）+ 服务层（过滤 / 编辑保存 / id 校验）。

来源优先级与缩进判定是这套东西的核心口径——旧语料被爬虫拍平过，
一旦优先级写反，界面就会拿残缺版本盖掉重爬好的完整版本。

运行：.venv/bin/python -m pytest tests/test_pine_library.py -q
"""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import pine_library as pl  # noqa: E402
from scripts import pine_library_index as pli  # noqa: E402


FLAT = """//@version=6
strategy("t", overlay=true)
if close > open
strategy.entry("L", strategy.long)
"""

INDENTED = """//@version=6
strategy("t", overlay=true)
if close > open
    strategy.entry("L", strategy.long)
"""


def _corpus(tmp: Path) -> Path:
    """造一份最小语料：分类清单.csv + 两个分类目录。"""
    src = tmp / "corpus"
    for cat, name, body in [("趋势跟踪", "001_a.pine", FLAT),
                            ("均值回归", "002_b.pine", FLAT)]:
        (src / cat).mkdir(parents=True, exist_ok=True)
        (src / cat / name).write_text(body, encoding="utf-8")
    with open(src / pli.CSV_NAME, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["序号", "分类", "中文标题", "原标题", "文件名", "URL", "状态"])
        w.writeheader()
        w.writerow({"序号": "1", "分类": "趋势跟踪", "中文标题": "甲", "原标题": "A",
                    "文件名": "001_a.pine", "URL": "https://tv/1", "状态": "ok"})
        w.writerow({"序号": "2", "分类": "均值回归", "中文标题": "乙", "原标题": "B",
                    "文件名": "002_b.pine", "URL": "https://tv/2", "状态": "ok"})
    return src


class TestMeta:
    def test_flat_source_is_flagged(self):
        assert pli._meta(FLAT)["indent_ok"] is False

    def test_indented_source_is_usable(self):
        m = pli._meta(INDENTED)
        assert m["indent_ok"] is True
        assert m["version"] == 6
        assert m["has_strategy"] is True
        assert m["lines"] == 4


class TestBuild:
    def test_index_lists_catalog_and_flags_flat(self, tmp_path):
        idx = pli.build(source=_corpus(tmp_path), lib_dir=tmp_path / "lib")
        assert idx["total"] == 2
        assert idx["usable"] == 0          # 两份都是拍平的
        assert {c["name"] for c in idx["categories"]} == {"趋势跟踪", "均值回归"}
        assert (tmp_path / "lib/index.json").exists()
        assert (tmp_path / "lib/source/0001.pine").read_text(encoding="utf-8") == FLAT

    def test_recrawl_wins_over_desktop(self, tmp_path):
        lib = tmp_path / "lib"
        recrawl = lib / "recrawl"
        recrawl.mkdir(parents=True)
        (recrawl / "001_x.pine").write_text(
            "// 来源: https://tv/1\n" + INDENTED, encoding="utf-8")
        idx = pli.build(source=_corpus(tmp_path), lib_dir=lib)
        by_id = {it["id"]: it for it in idx["items"]}
        assert by_id["0001"]["source_kind"] == "recrawl"
        assert by_id["0001"]["indent_ok"] is True
        assert by_id["0002"]["source_kind"] == "desktop"
        assert idx["usable"] == 1

    def test_edited_wins_over_recrawl(self, tmp_path):
        lib = tmp_path / "lib"
        (lib / "recrawl").mkdir(parents=True)
        (lib / "recrawl/001_x.pine").write_text(
            "// 来源: https://tv/1\n" + FLAT, encoding="utf-8")
        (lib / "edited").mkdir(parents=True)
        (lib / "edited/0001.pine").write_text(INDENTED, encoding="utf-8")
        idx = pli.build(source=_corpus(tmp_path), lib_dir=lib)
        by_id = {it["id"]: it for it in idx["items"]}
        assert by_id["0001"]["source_kind"] == "edited"
        assert (lib / "source/0001.pine").read_text(encoding="utf-8") == INDENTED

    def test_missing_file_marked_not_dropped(self, tmp_path):
        src = _corpus(tmp_path)
        (src / "均值回归/002_b.pine").unlink()
        idx = pli.build(source=src, lib_dir=tmp_path / "lib")
        by_id = {it["id"]: it for it in idx["items"]}
        assert by_id["0002"]["source_kind"] == "missing"
        assert idx["total"] == 2

    def test_orphan_recrawl_lands_in_unclassified(self, tmp_path):
        lib = tmp_path / "lib"
        (lib / "recrawl").mkdir(parents=True)
        (lib / "recrawl/zzz.pine").write_text(
            "// 来源: https://tv/999\n" + INDENTED, encoding="utf-8")
        idx = pli.build(source=_corpus(tmp_path), lib_dir=lib)
        orphans = [it for it in idx["items"] if it["category"] == "未归类"]
        assert len(orphans) == 1 and orphans[0]["url"] == "https://tv/999"


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """把服务层的目录指到临时库，并按语料建一次索引。"""
    d = tmp_path / "lib"
    monkeypatch.setattr(pl, "LIB_DIR", d)
    monkeypatch.setattr(pl, "INDEX", d / "index.json")
    monkeypatch.setattr(pl, "SOURCE_DIR", d / "source")
    monkeypatch.setattr(pl, "EDITED_DIR", d / "edited")
    pli.build(source=_corpus(tmp_path), lib_dir=d)
    return d


class TestService:
    def test_list_filters_by_category_and_keyword(self, lib):
        assert pl.list_items(category="趋势跟踪")["filtered"] == 1
        assert pl.list_items(q="乙")["filtered"] == 1
        assert pl.list_items(q="不存在")["filtered"] == 0
        assert pl.list_items()["filtered"] == 2

    def test_get_source_returns_text(self, lib):
        got = pl.get_source("0001")
        assert got["title"] == "甲" and "strategy(" in got["source"]
        assert got["edited"] is False

    def test_save_then_reset(self, lib):
        pl.save_source("0001", INDENTED)
        got = pl.get_source("0001")
        assert got["edited"] is True and got["source"] == INDENTED
        assert (lib / "edited/0001.pine").exists()
        pl.reset_source("0001")
        assert pl.get_source("0001")["edited"] is False
        assert not (lib / "edited/0001.pine").exists()

    def test_save_rejects_empty_and_oversize(self, lib):
        with pytest.raises(ValueError):
            pl.save_source("0001", "   ")
        with pytest.raises(ValueError):
            pl.save_source("0001", "x" * (pl.MAX_SOURCE_BYTES + 1))

    @pytest.mark.parametrize("bad", ["../etc/passwd", "1", "0001.pine", "", "x99"])
    def test_bad_id_rejected(self, lib, bad):
        with pytest.raises(ValueError):
            pl.get_source(bad)

    def test_unknown_id_rejected(self, lib):
        with pytest.raises(ValueError):
            pl.get_source("0099")

    def test_save_checks_id_exists_first(self, lib):
        with pytest.raises(ValueError):
            pl.save_source("0099", INDENTED)

    def test_index_payload_shape(self, lib):
        payload = json.loads((lib / "index.json").read_text(encoding="utf-8"))
        assert set(payload) >= {"generated", "total", "usable", "categories", "items"}


@pytest.fixture()
def queue(lib, tmp_path, monkeypatch):
    """把转写/队列目录指到临时目录；标的校验换成直通（另有用例单测真实校验）。"""
    d = tmp_path / "pine_transpile"
    monkeypatch.setattr(pl, "TRANSPILE_DIR", d)
    monkeypatch.setattr(pl, "QUEUE_DIR", d / "queue")
    monkeypatch.setattr(pl, "_check_symbol", lambda s: s)
    return d


class TestBacktestQueue:
    def test_enqueue_writes_request_and_reports_queued(self, queue):
        got = pl.enqueue_backtest("0001", "600309.SH", adj="forward")
        assert got["queued"] is True
        req = json.loads((queue / "queue/0001.json").read_text(encoding="utf-8"))
        assert req["symbol"] == "600309.SH" and req["adj"] == "forward"
        # 还没开跑 → 界面看到 queued
        assert pl.job_status("0001")["status"] == "queued"

    def test_duplicate_enqueue_rejected(self, queue):
        pl.enqueue_backtest("0001", "600309.SH")
        with pytest.raises(ValueError, match="已在队列"):
            pl.enqueue_backtest("0001", "600309.SH")

    def test_running_job_rejected(self, queue):
        d = queue / "0001"
        d.mkdir(parents=True)
        (d / "job.json").write_text(json.dumps({"status": "running", "stage": "backtest"}),
                                    encoding="utf-8")
        with pytest.raises(ValueError, match="正在跑"):
            pl.enqueue_backtest("0001", "600309.SH")

    def test_unknown_id_and_bad_adj_rejected(self, queue):
        with pytest.raises(ValueError):
            pl.enqueue_backtest("0099", "600309.SH")
        with pytest.raises(ValueError):
            pl.enqueue_backtest("0001", "600309.SH", adj="前复权")

    def test_check_symbol_rejects_unknown(self, monkeypatch):
        from backend.services import market_lab as ml

        monkeypatch.setattr(ml, "_names", lambda: {"600309.SH": "万华化学"})
        assert pl._check_symbol("600309") == "600309.SH"
        with pytest.raises(ValueError):
            pl._check_symbol("999999.SZ")

    def test_list_transpile_skips_work_dirs(self, queue):
        """queue/ 是工作目录，不能当成一条策略冒出来。"""
        (queue / "queue").mkdir(parents=True)
        d = queue / "0001"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"title": "甲", "problems": []}),
                                     encoding="utf-8")
        got = pl.list_transpile()
        assert set(got) == {"0001"}
        assert got["0001"]["job"] == {"status": "", "stage": "", "error": ""}

    def test_read_report_missing_is_empty(self, queue):
        assert pl.read_report("0001") == {}
