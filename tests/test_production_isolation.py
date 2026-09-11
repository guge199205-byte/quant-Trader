"""隔离兜底自检（2026-09-11 实盘账本被 pytest 写坏事故的回归钉）。

事故：`test_reconcile_hardening.py` 调真实 reconcile，桥桩回「卖出成交 100 股
@17.5」→ 该用例只隔离了日志目录，**没隔离 `live_ledger.LEDGER_FILE`** → 每跑
一次 pytest 就往真实 `logs/live_ledger.json` 记一笔假成交：deepseek-v4-pro 的
001312.SZ 被连扣 10 次（1100 → 100 股）、虚拟现金虚增 ¥17,500。

本文件从两个角度钉住 tests/conftest.py 的全局隔离：
  1) 各生产状态常量确实被重定向出仓库（静态）；
  2) 真跑一轮 reconcile（含真实成交补记路径），真实账本字节不变（动态）。
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import live_fills  # noqa: E402
import live_ledger  # noqa: E402
import live_price_watch  # noqa: E402
import live_trade_picks  # noqa: E402

PROD_LEDGER = ROOT / "logs" / "live_ledger.json"
AGENT, CODE = "deepseek-v4-pro", "001312.SZ"


def test_production_state_constants_are_redirected(tmp_path):
    """所有生产写路径都必须落在 tmp 里（写进仓库 = 污染实盘）。"""
    live = {
        "ledger": live_ledger.LEDGER_FILE,
        "pending": live_fills.PENDING_FILE,
        "events": live_fills.EVENTS_FILE,
        "lock": live_fills.RECONCILE_LOCK_FILE,
        "watch": live_price_watch.WATCH_FILE,
        "trade_logs": live_trade_picks.LOG_DIR,
        "watch_logs": live_price_watch.LOG_DIR,
        # import 时就从 LOG_DIR 算好的派生常量：只 patch LOG_DIR 拦不住它们
        # （审查 HIGH：测试写它 = 吃掉实盘同小时的告警去重标记）
        "watch_notify_state": live_price_watch.SKIP_STATE_FILE,
    }
    for name, path in live.items():
        assert str(path).startswith(str(tmp_path)), f"{name} 没被隔离: {path}"


def test_no_module_constant_points_into_production_state_dirs(tmp_path):
    """兜底网契约：这些模块里不许再有 Path 常量指向生产 logs//data/。

    新加一个 `FOO_FILE = LOG_DIR / "x"` 忘了进清单时，这条会红——这正是
    SKIP_STATE_FILE 漏网半年的形态。
    """
    import live_breaker
    import live_hourly_analysis
    import live_l2_capture
    import live_llm_trade

    prod = [str((ROOT / "logs").resolve()) + "/", str((ROOT / "data").resolve()) + "/"]
    leaked = []
    for mod in (live_fills, live_ledger, live_price_watch, live_trade_picks,
                live_hourly_analysis, live_llm_trade, live_breaker, live_l2_capture):
        for attr, val in vars(mod).items():
            if attr.startswith("__") or not isinstance(val, Path):
                continue
            real = str(val.resolve())
            if any(real.startswith(p) for p in prod):
                leaked.append(f"{mod.__name__}.{attr}={val}")
    assert leaked == [], "这些常量仍指向生产状态目录: " + ", ".join(leaked)


def test_redirected_state_paths_are_writable(tmp_path):
    """重定向后的父目录必须真的存在：否则锁文件 open() 失败会被 except OSError
    吞掉，用例静默走「已有另一班在跑」分支（假绿），状态也只打印告警后丢失。"""
    import live_breaker
    import live_hourly_analysis
    import live_llm_trade

    for path in (live_llm_trade.LOCK_FILE, live_llm_trade.STATE_FILE,
                 live_hourly_analysis.LAST_DECISIONS_FILE,
                 live_hourly_analysis.STATE_PATH, live_breaker.TRIP_DIR):
        assert str(path).startswith(str(tmp_path)), f"没被隔离: {path}"
        assert path.parent.is_dir(), f"{path.parent} 不存在（conftest 没建父目录）"
        probe = path.parent / ".writable-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()


def test_secondary_state_modules_are_redirected_here(tmp_path):
    """次级状态（决策/条件位之外的 id 状态）也在 tmp 里，且**单文件跑**也成立。

    这些模块有被「用例函数体里才 import」的用法（test_buy_gate:110、
    test_live_llm_trade_retry:120 等）：fixture 按 sys.modules 存在与否来 patch，
    若等函数体 import 才加载，patch 早已跑完 → 常量仍指真实 data/ 与 logs/。
    本用例自身只 import conftest（收集期）就已存在这些模块来钉这一点。
    """
    import live_breaker
    import live_hourly_analysis
    import live_l2_capture
    import live_llm_trade

    live = {
        "last_decisions": live_hourly_analysis.LAST_DECISIONS_FILE,
        "llm_lock": live_llm_trade.LOCK_FILE,
        "llm_state": live_llm_trade.STATE_FILE,
        "breaker_trips": live_breaker.TRIP_DIR,
        "l2_factors": live_l2_capture.FACTORS_FILE,
        "l2_state": live_l2_capture.STATE_FILE,
    }
    for name, path in live.items():
        assert str(path).startswith(str(tmp_path)), f"{name} 没被隔离: {path}"


def test_conftest_imports_secondary_modules_eagerly():
    """源码钉：这几个模块必须在 conftest 收集期导入，隔离才覆盖单文件运行。

    少了它，`pytest tests/test_buy_gate.py` 这类单文件跑法下模块要等用例函数体
    才出现，patch 不到 → 真实 data/agent_last_decisions.json 被写。
    """
    text = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    for name in ("live_hourly_analysis", "live_llm_trade", "live_breaker", "live_l2_capture"):
        assert f'"{name}": (' in text, f"conftest 的次级状态表里没有 {name}"
        assert f"__import__(_m)" in text, "conftest 没在收集期导入次级状态模块"


def test_reconcile_fill_never_touches_real_ledger():
    """真跑一轮带成交的 reconcile：补记只进 tmp 账本，仓库账本字节不变。"""
    # 先验隔离再动手：本用例是**整文档覆盖写**，若隔离坏了，digest 断言会红——
    # 但那已经在真实账本被合成账本（只含一个 agent 一只票）覆盖之后了。
    assert live_ledger.LEDGER_FILE != PROD_LEDGER, "隔离失效：要先修 conftest 再跑本用例"

    before = _digest(PROD_LEDGER)

    live_ledger.save_ledger({"version": 1, "agents": {AGENT: {
        "virtual_cash": 100000.0,
        "positions": {CODE: {"volume": 500, "cost_price": 17.0,
                             "buy_ts": "2026-09-08T09:37:42+08:00",
                             "last_ts": "2026-09-08T09:37:42+08:00"}}}}})

    live_fills.save_pending([{
        "order_id": "T-ISOLATION", "agent": AGENT, "code": CODE, "side": "sell",
        "volume": 100, "price": 17.0, "volume_recorded": 0,
        "ts": datetime.now(live_fills.CN_TZ).isoformat()}])

    class Broker:
        def get_orders(self):
            return [{"order_id": "T-ISOLATION", "status": "filled", "filled_volume": 100,
                     "filled_price": 17.5, "total_volume": 100, "stock_code": CODE}]

    assert live_fills.reconcile(Broker()) == 1              # 真走了补记分支
    assert _digest(PROD_LEDGER) == before                   # 真实账本一个字节没动

    tmp_led = json.loads(Path(live_ledger.LEDGER_FILE).read_text(encoding="utf-8"))
    pos = tmp_led["agents"][AGENT]["positions"][CODE]
    assert pos["volume"] == 400                             # 补记落在这里
    assert tmp_led["agents"][AGENT]["virtual_cash"] == 101750.0


def _digest(path: Path) -> str | None:
    """文件指纹；文件不存在返回 None（同样要求前后一致）。"""
    if not path.is_file():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_notify_once_only_writes_the_redirected_state_file():
    """哨兵的账户级去重标记：写了它 = 吃掉同一小时的真实告警。

    `SKIP_STATE_FILE = LOG_DIR / ...` 是 import 时算好的独立常量，只 patch
    LOG_DIR 拦不住（2026-09-11 审查 HIGH）。这里真跑一次 _notify_once 钉住：
    真实文件字节不变，标记只落在 tmp。
    """
    prod_state = ROOT / "logs" / "live_watch_notify_state.json"
    before = _digest(prod_state)

    assert live_price_watch.SKIP_STATE_FILE != prod_state
    live_price_watch._notify_once("isolation-probe", "【隔离自检】不应出现在真实日志")

    assert _digest(prod_state) == before
    tmp_state = json.loads(Path(live_price_watch.SKIP_STATE_FILE).read_text(encoding="utf-8"))
    assert "isolation-probe" in tmp_state
