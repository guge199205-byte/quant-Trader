"""行情回测（market_lab）：代码归一 + PyneCore 模块缓存隔离。

背景（2026-09-08 实测）：PyneCore 把 Script 对象（含 SimPosition 持仓与成交统计）
挂在脚本模块的 `main` 函数上，而 `import_script` 走 `import_module(stem)` 命中
`sys.modules` 缓存——不清缓存时同一进程内第 2 次回测会继承第 1 次的持仓：
万华化学 sma_cross 实测成交数 69 → 138 → 207 递增、净值起点 10 万 → 6.9 万 → 4.2 万。
本测试锁定「清模块缓存 → 多次回测结果一致」。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_market_lab.py -q
（回测用例需 pynecore；未安装时自动跳过，代码归一/缓存清理用例仍会执行）
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# PyneCore 装在 pine_lab 的独立 venv 里（后端容器内也有）；测试环境借用其 site-packages，
# 让确定性回归用例真的跑起来而不是永远 skip。pynecore 无第三方依赖，借用无版本风险。
_PINE_SITE = (Path(__file__).resolve().parents[1]
              / "pine_lab/.venv/lib/python3.14/site-packages")
if _PINE_SITE.is_dir() and str(_PINE_SITE) not in sys.path:
    sys.path.append(str(_PINE_SITE))

from backend.services import market_lab as ml  # noqa: E402


class TestNormalizeCode:
    def test_six_digit_shanghai(self):
        assert ml.normalize_code("600309") == "600309.SH"

    def test_six_digit_shenzhen(self):
        assert ml.normalize_code("000001") == "000001.SZ"

    def test_dot_ss_alias(self):
        assert ml.normalize_code("600309.SS") == "600309.SH"

    def test_prefixed_forms(self):
        assert ml.normalize_code("SH600309") == "600309.SH"
        assert ml.normalize_code("sz000001") == "000001.SZ"

    def test_already_normalized_passthrough(self):
        assert ml.normalize_code("600309.SH") == "600309.SH"

    def test_empty_and_garbage(self):
        assert ml.normalize_code("") == ""
        assert ml.normalize_code(None) == ""
        assert ml.normalize_code("NOTACODE") == "NOTACODE"


class TestPurgeScriptModule:
    def test_removes_stem_and_submodules(self):
        sys.modules["_lab_fake_strategy"] = object()
        sys.modules["_lab_fake_strategy.util"] = object()
        sys.modules["_lab_fake_strategy_other"] = object()  # 前缀相同但不是子模块
        try:
            ml._purge_script_module(Path("_lab_fake_strategy.py"))
            assert "_lab_fake_strategy" not in sys.modules
            assert "_lab_fake_strategy.util" not in sys.modules
            assert "_lab_fake_strategy_other" in sys.modules
        finally:
            sys.modules.pop("_lab_fake_strategy_other", None)

    def test_missing_module_is_noop(self):
        ml._purge_script_module(Path("_lab_never_imported.py"))  # 不抛异常即可


@pytest.mark.skipif(
    not (Path("/data/quantdb/1_kline_data").is_dir()
         or (Path.home() / "projects/quantmind/data/quantdb/1_kline_data").is_dir()),
    reason="quantdb 未挂载",
)
class TestBacktestDeterminism:
    def test_same_params_same_result_twice(self):
        pytest.importorskip("pynecore", reason="PyneCore 运行时未安装")
        kw = dict(strategy_id="sma_cross", symbol="600309.SH", adj="backward",
                  params={"fast": 5, "slow": 20})
        a = ml.run_backtest(**kw)
        b = ml.run_backtest(**kw)
        assert a["stats"]["Total trades"]["value"] == b["stats"]["Total trades"]["value"]
        assert a["stats"]["Net profit"]["value"] == pytest.approx(
            b["stats"]["Net profit"]["value"])
        assert a["equity"][0]["value"] == pytest.approx(100000.0)
        assert b["equity"][0]["value"] == pytest.approx(100000.0)


class TestStrategyRegistry:
    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError):
            ml.run_backtest(strategy_id="no_such_strategy", symbol="600309.SH")

    def test_registry_entries_have_file_and_params(self):
        for sid, spec in ml.STRATEGIES.items():
            assert spec["name"] and spec["desc"]
            assert spec["params"], f"{sid} 无参数定义"
            assert (ml.STRATEGY_DIR / spec["file"]).is_file(), f"{sid} 策略文件缺失"


class TestTomlOverrides:
    """PyneCore 的 <策略>.toml 里 `value =` 会在运行时覆盖 input 默认值。

    实测（2026-09-08）：给 donchian.toml 写 entry_len=99/exit_len=3，同一标的同一
    参数的回测从 -16.7%/60 笔变成 -30.7%/44 笔。批量回测多进程并发重写该文件时，
    读到半截文件会报错、读到别人的值会静默算出不同结果——必须保证模板目录里没有 toml。
    """

    def test_side_file_disabled(self):
        assert os.environ.get("PYNE_SAVE_SCRIPT_TOML") == "0", "必须禁用 PyneCore 回写 toml"
        assert not list(ml.STRATEGY_DIR.glob("*.toml")), "模板目录不该有 toml 残留"

    @pytest.mark.skipif(
        not (Path("/data/quantdb/1_kline_data").is_dir()
             or (Path.home() / "projects/quantmind/data/quantdb/1_kline_data").is_dir()),
        reason="quantdb 未挂载",
    )
    def test_poisoned_toml_is_ignored(self):
        pytest.importorskip("pynecore", reason="PyneCore 运行时未安装")
        script = ml.STRATEGY_DIR / ml.STRATEGIES["donchian"]["file"]
        toml = script.with_suffix(".toml")
        toml.write_text("[script]\npyramiding = 1\n\n"
                        "[inputs.entry_len]\nvalue = 99\n\n"
                        "[inputs.exit_len]\nvalue = 3\n", encoding="utf-8")
        try:
            bars = ml.load_klines("600028.SH", adj="backward", limit=0)
            got = ml._run_strategy_on_bars("donchian", bars, "600028.SH", "backward",
                                           want_trades=False)
            trades = got["stats"]["Total trades"]["value"]
            clean = ml.run_backtest("donchian", "600028.SH", adj="backward")
            assert trades == clean["stats"]["Total trades"]["value"], "残留 toml 污染了回测"
            assert not toml.exists(), "回测前应清掉残留 toml"
        finally:
            toml.unlink(missing_ok=True)
