"""news_digest 纯逻辑单测：相关度闸门（噪声源）/聚类键/时间转换。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_news_digest.py -q
（build_digest 走 127.0.0.1:8000 真实 API，不在单测范围）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from news_digest import _bz, _norm_key, _relevant, _to_bj  # noqa: E402


def art(title: str, source: str = "财联社-电报", tk: list | None = None,
         inds: list | None = None) -> dict:
    return {"title": title, "source_name": source,
            "enrichment": {"tickers": tk or [], "industries": inds or []}}


def test_industry_tag_keeps_finance_news():
    assert _relevant(art("某公司签订大额订单", tk=["600519.SH"]))


def test_noisy_source_needs_title_keyword():
    # 新浪国内滚动桶带代码标签的路况/社会新闻 → 无财经关键词必须丢弃
    assert not _relevant(art("昆明早高峰路况", source="新浪财经－国内滚动",
                             tk=["300024.SZ"]))
    assert _relevant(art("央行开展逆回购操作", source="新浪财经－国内滚动",
                         tk=["600036.SH"]))


def test_pure_macro_news_kept_by_keyword():
    assert _relevant(art("美联储9月议息前瞻：CPI 与非农数据何去何从"))


def test_unrelated_news_dropped():
    assert not _relevant(art("莴笋收购环节染色 官方通报立案查处"))


def test_norm_key_clusters_repost_variants():
    a = art("康欣新材：因定期报告涉嫌信息披露违法违规被证监会立案", tk=["600076.SH"])
    b = art("600076，被证监会立案！一周机构龙虎榜出炉", tk=["600076.SH", "300059.SZ"])
    # 同代码 + 标题主干相同 → 同簇（去重）；不同公司不误并
    assert _norm_key(a) == _norm_key(b)
    c = art("兴业科技：因涉嫌信息披露违法违规被证监会立案", tk=["002674.SZ"])
    assert _norm_key(a) != _norm_key(c)


def test_bj_time_conversion():
    assert _to_bj("2026-09-06T13:00:00Z") == "2026-09-06 21:00"
    assert _bz("2026-09-06T13:00:00Z") == "09-06 21:00"
