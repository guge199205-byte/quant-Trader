"""候选池入口的标的边界（live_llm_trade.load_pool，2026-09-11 用户口径）。

「agent 选出来的股票，跟踪的股票，不需要 ST 的；垃圾股、财务造假、恶劣新闻……
都黑名单」——此前 symbol_policy 只在**下单时**拦（buy_gate），池子本身仍把
ST/退市/黑名单/事件风险标的原样推给模型与 L2 采集：模型看得见就会反复考虑，
白挨一轮"提了买、被毙掉"；采集侧还给这些票空烧行情窗口。

三条实盘路径（09:35 决策 / 整点轮 / L2 采集）都从 load_pool 取池，本测试钉住
这个单点：过滤生效、按原排序补齐到 top（不是白白变小）、坏输入不炸。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_pool_symbol_filter.py -q
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_llm_trade as LT  # noqa: E402
import symbol_policy as SP  # noqa: E402


@pytest.fixture
def pool_env(monkeypatch, tmp_path):
    """候选池读的 picks 目录指向 tmp；返回 (喂给 subprocess 的池, 收到的命令参数)。"""
    monkeypatch.setattr(LT, "PICKS_JSON", tmp_path / "picks")
    feed, calls = [], []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=json.dumps(feed[0]), stderr="", returncode=0)

    monkeypatch.setattr(LT.subprocess, "run", fake_run)
    return feed, calls


def _row(code, name, score=0.5):
    return {"code": code, "name": name, "side": "HOLD", "score": score,
            "fusion": 0.0, "rank": 1, "industry": "", "remark": ""}


def test_load_pool_drops_st_row(pool_env):
    feed, _ = pool_env
    feed.append([_row("600309.SH", "万华化学"), _row("600666.SH", "*ST瑞德")])

    pool, _dir = LT.load_pool(20)

    assert [p["code"] for p in pool] == ["600309.SH"]


def test_load_pool_drops_blacklist_and_risk_rows(pool_env, monkeypatch, capsys):
    feed, _ = pool_env
    feed.append([_row("600309.SH", "万华化学"), _row("001312.SZ", "福恩股份")])
    monkeypatch.setattr(SP, "load_policy_with_risk", lambda *a, **kw: SP.SymbolPolicy(
        block_buy=frozenset({"001312.SZ"})))

    pool, _dir = LT.load_pool(20)

    assert [p["code"] for p in pool] == ["600309.SH"]
    assert "剔除" in capsys.readouterr().out          # 剔除必须留痕（运维可查）


def test_load_pool_overfetches_then_fills_back_to_top(pool_env):
    """剔除的名额由后续排名补上：取 top+10、过滤后再截断——池子不许白白变小。"""
    feed, calls = pool_env
    feed.append([_row(f"6000{i:02d}.SH", f"股票{i}") for i in range(5)]
                + [_row(f"6001{i:02d}.SH", f"*ST股{i}") for i in range(5)]
                + [_row(f"6002{i:02d}.SH", f"补位{i}") for i in range(20)])

    pool, _dir = LT.load_pool(20)

    assert len(pool) == 20 and not any("ST" in p["name"] for p in pool)
    assert pool[0]["code"] == "600000.SH"              # 原排序保留
    assert "--top" in calls[0] and calls[0][calls[0].index("--top") + 1] == "30"


def test_load_pool_survives_bad_subprocess_output(pool_env, monkeypatch, tmp_path):
    feed, _ = pool_env

    def broken(cmd, **kw):
        return types.SimpleNamespace(stdout="not json", stderr="", returncode=1)

    monkeypatch.setattr(LT.subprocess, "run", broken)
    assert LT.load_pool(20) == ([], {})


def test_live_paths_share_the_filtered_pool_entry():
    """源码钉：整点轮 / L2 采集不许自建第二个取池口——过滤只此一处，漏一处就漏一处。"""
    for name in ("live_hourly_analysis.py", "live_l2_capture.py"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "from live_llm_trade import load_pool" in text, name
        assert "select_from_reports" not in text, f"{name} 绕过了 load_pool 的过滤"
