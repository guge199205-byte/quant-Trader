"""数据字典生成器（scripts/quantdata_dictionary.py）。

最要紧的一组是**跨市场口径**：
本生成器第一版把 A 股的「1 手=100 股 / amount 单位万元」套到了美/港股上，
还盖了 `official` 的章——而实测（amount/volume 反算对照 close）证明
**美/港股的 amount 是原币**，套用 A 股约定会错 10000 倍。
这正是字典本身要防的事，所以必须用测试钉死。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_quantdata_dictionary.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import quantdata_dictionary as D  # noqa: E402


def test_amount_unit_differs_by_market():
    """★ 跨市场口径不得套用：A 股万元 vs 美/港股原币（差 10000 倍）。"""
    assert D.guess_unit("amount", "cn") == ("万元", "official")
    assert D.guess_unit("amount", "us") == ("原币（美元）", "verified")
    assert D.guess_unit("amount", "hk") == ("原币（港元）", "verified")


def test_volume_is_shares_everywhere_but_lot_note_is_cn_only():
    """volume 三市场都是股；但「1 手=100 股」是 A 股专属，不得外溢。"""
    assert D.guess_unit("volume", "cn")[0].startswith("股")
    assert "手" in D.guess_unit("volume", "cn")[0]
    for m in ("us", "hk"):
        assert D.guess_unit("volume", m) == ("股", "verified")
        assert "手" not in D.guess_unit("volume", m)[0]


def test_unknown_market_gives_no_unit_not_a_guess():
    """市场未知时宁可不给单位，也不猜——不臆造是这套字典的底线。"""
    assert D.guess_unit("amount", "jp") == (None, None)
    assert D.guess_unit("volume", "jp") == (None, None)


def test_common_fields_and_inferred_marking():
    assert D.guess_unit("close")[1] == "official"
    assert D.guess_unit("turnover_rate") == ("%", "inferred")
    assert D.guess_unit("完全没见过的字段") == (None, None)
