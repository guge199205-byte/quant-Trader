"""晚间池 02:30 化 + 目标交易日命名（2026-09-12 P1-4）。

背景：晚间研究原 19:30 跑，quantdb 当日分区次日 01:05 才落 → 池子恒滞后一个
交易日（09-10 收盘数据 09-11 20:30 才成池，09-11 早盘消费不到），且池文件按
**数据会话日**命名 → 每个交易日早盘拿到的都是"昨天的池"。

改法（用户 2026-09-12 决策）：改到北京 02:30（JST 03:30）跑，等当日分区落盘；
池文件按**目标交易日**命名 `{target}_agent_picks.json` → 自然成为当日最新池。
日历闸门重写为 session=最近已收盘交易日 / target=下一个交易日；
`latest_dt() < session` → 退出码 2，宁可不产也不产陈旧池。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_night_pool_gate.py -q
"""
import json
import sys
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import night_pool_agent as N      # noqa: E402

BJ = ZoneInfo("Asia/Shanghai")


def _at(y, m, d, hh=2, mm=30):
    return datetime(y, m, d, hh, mm, tzinfo=BJ)


# ---------- 会话日 / 目标日 ----------

def test_session_and_target_friday_0230():
    """周五凌晨 02:30：周四已收盘（数据=周四），池服务周五。"""
    assert N.session_and_target(_at(2026, 9, 11)) == ("20260910", "20260911")


def test_session_and_target_saturday_0230():
    """周六凌晨 02:30：周五已收盘，池服务下周一（周末无需重产）。"""
    assert N.session_and_target(_at(2026, 9, 12)) == ("20260911", "20260914")


def test_session_and_target_holiday_morning():
    """国庆首日凌晨：最近已收盘是节前 09-30，池服务复市日 10-08。"""
    assert N.session_and_target(_at(2026, 10, 1)) == ("20260930", "20261008")


# ---------- 数据新鲜度闸门 ----------

def test_check_data_fresh_matrix(monkeypatch):
    monkeypatch.setattr(N, "latest_dt", lambda: "20260910")
    assert N.check_data_fresh("20260910")[0] is True       # 分区到位

    monkeypatch.setattr(N, "latest_dt", lambda: "20260909")
    ok, why = N.check_data_fresh("20260910")
    assert ok is False and "20260909" in why and "20260910" in why

    monkeypatch.setattr(N, "latest_dt", lambda: "")        # 目录都没有
    assert N.check_data_fresh("20260910")[0] is False


# ---------- main 闸门与幂等补跑 ----------

@pytest.fixture
def stub_run(monkeypatch):
    calls = []
    monkeypatch.setattr(N, "run", lambda *a, **k: (calls.append((a, k)), 0)[1])
    return calls


def test_main_exits_2_without_running_when_data_not_landed(monkeypatch, stub_run, capsys):
    monkeypatch.setattr(N, "now_cn", lambda: _at(2026, 9, 11))
    monkeypatch.setattr(N, "latest_dt", lambda: "20260909")
    assert N.main([]) == 2
    assert stub_run == []                                  # 宁可不产，不产陈旧池
    assert "20260910" in capsys.readouterr().out


def test_main_runs_with_session_and_target(monkeypatch, stub_run):
    monkeypatch.setattr(N, "now_cn", lambda: _at(2026, 9, 11))
    monkeypatch.setattr(N, "latest_dt", lambda: "20260910")
    assert N.main([]) == 0
    assert stub_run == [(("20260910", "20260911"), {"dry": False})]


def test_retry_if_missing_skips_when_target_file_exists(monkeypatch, tmp_path, stub_run, capsys):
    monkeypatch.setattr(N, "now_cn", lambda: _at(2026, 9, 11))
    monkeypatch.setattr(N, "latest_dt", lambda: "20260910")
    monkeypatch.setattr(N, "PICKS_DIR", tmp_path)
    (tmp_path / "20260911_agent_picks.json").write_text("{}", encoding="utf-8")

    assert N.main(["--if-missing"]) == 0
    assert stub_run == []                                  # 补跑幂等：已有目标日池就不重产
    assert "已存在" in capsys.readouterr().out

    # 同条件下主跑（不带 --if-missing）照常重产：刷新当日新闻窗口
    assert N.main([]) == 0 and len(stub_run) == 1


def test_retry_if_missing_runs_when_target_file_absent(monkeypatch, tmp_path, stub_run):
    monkeypatch.setattr(N, "now_cn", lambda: _at(2026, 9, 11))
    monkeypatch.setattr(N, "latest_dt", lambda: "20260910")
    monkeypatch.setattr(N, "PICKS_DIR", tmp_path)
    assert N.main(["--if-missing"]) == 0 and len(stub_run) == 1


# ---------- run：命名与落盘 ----------

_POOL_JSON = json.dumps({
    "date": "20260910", "market_view": "震荡偏弱",
    "sectors": [], "pool": [{"code": "600817.SH", "name": "上工申贝",
                             "score": 9.5, "reason": "趋势延续"}],
})


@pytest.fixture
def stub_run_deps(monkeypatch, tmp_path):
    """run() 的 IO 全部落 tmp：不碰生产 logs/quantmind 目录、不调桥/LLM。

    NIGHT_POOL_DIR/PICKS_DIR 必须显式指到本用例 tmp（这两个模块常量在 conftest
    兜底网之外的原值指向生产目录——2026-09-12 实录：漏 patch 一次就把真实
    logs/night_pool/{session}.md 覆写成桩内容）。
    """
    monkeypatch.setattr(N, "ROOT", tmp_path)
    monkeypatch.setattr(N, "NIGHT_POOL_DIR", tmp_path / "logs" / "night_pool")
    monkeypatch.setattr(N, "PICKS_DIR", tmp_path / "stock_picks")
    monkeypatch.setattr(N, "build_candidates", lambda *a, **k: (
        [{"code": "600817.SH", "name": "上工申贝", "score": 9.5}], "20260910"))
    monkeypatch.setattr(N, "_news_block", lambda d0: "")
    # 依赖模块用替身塞进 sys.modules：避免真 import（dsh_agent 会读环境/连端点）
    monkeypatch.setitem(sys.modules, "dsh_agent",
                        types.SimpleNamespace(run_agent=lambda *a, **k: _POOL_JSON))
    monkeypatch.setitem(sys.modules, "event_radar",
                        types.SimpleNamespace(tag_events=lambda codes: {}))
    monkeypatch.setitem(sys.modules, "risk_list",
                        types.SimpleNamespace(load_risk=lambda: {}))
    monkeypatch.setitem(sys.modules, "market_state",
                        types.SimpleNamespace(build_market_state=lambda b: "（盘面）"))
    monkeypatch.setitem(sys.modules, "agent_tools.brokers.tdx_bridge",
                        types.SimpleNamespace(TdxBridgeBroker=lambda: None))
    return tmp_path


def test_run_writes_pool_named_by_target_and_log_by_session(stub_run_deps):
    tmp = stub_run_deps
    assert N.run("20260910", "20260911") == 0

    pool_file = tmp / "stock_picks" / "20260911_agent_picks.json"
    assert pool_file.is_file()                             # 按**目标交易日**命名
    out = json.loads(pool_file.read_text(encoding="utf-8"))
    assert out["date"] == "20260911" and out["session"] == "20260910"
    assert out["picks"][0]["code"] == "600817.SH"

    assert (tmp / "logs" / "night_pool" / "20260910.md").is_file()   # 纪要按会话日
    assert (tmp / "logs" / "night_pool" / "20260910.json").is_file()
    # 对话流：归到该池服务的交易日（周一早班看得到周一的池）
    log = tmp / "data" / "agent_data_astock" / "market-research" / "log" / "2026-09-11" / "log.jsonl"
    assert log.is_file()
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["kind"] == "night_pool"


def test_run_refuses_stale_partition_directly(stub_run_deps, monkeypatch, capsys):
    """直调 run() 的兜底：数据分区早于会话日 → 退出码 2 且不落任何文件。"""
    monkeypatch.setattr(N, "build_candidates", lambda *a, **k: (
        [{"code": "600817.SH", "name": "上工申贝", "score": 9.5}], "20260909"))
    tmp = stub_run_deps
    assert N.run("20260910", "20260911") == 2
    assert not (tmp / "stock_picks").exists()
    assert not (tmp / "logs").exists()
    assert "20260909" in capsys.readouterr().out
