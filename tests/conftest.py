"""测试全局隔离：任何用例都不许碰实盘生产状态文件。

2026-09-11 实录（code-review CRITICAL）：`test_reconcile_hardening.py` 的
`test_reconcile_proceeds_lockless_when_lock_file_unusable` 调真实 `reconcile`，
而 fixture 只隔离了日志目录、**没隔离 `live_ledger.LEDGER_FILE`**。该用例的桥桩
返回「001312.SZ 卖出成交 100 股 @17.5」→ reconcile 走 delta>0 分支 → 直接写
真实 `logs/live_ledger.json`：**每跑一次 pytest，实盘分账账本被凭空扣 100 股、
多记 ¥1,750 虚拟现金**，`last_ts` 还被改写成用例常量。当天连跑 10 次 →
deepseek-v4-pro 的 001312.SZ 1100 股被抹成 100 股、现金虚增 ¥17,500，持仓
归属与买入额度全错（额度虚增会让 agent 继续买入；再扣两轮持仓会被删掉，之后
真实成交入账时 `record_sell` 找不到持仓会**静默丢弃**，成本基准永久丢失）。

"提交前跑一遍测试"是现行工作流 → 等于每次提交都损坏一次实盘账本。修法不是
逐个用例补 patch（漏一个就再来一次），而是 conftest 全局 autouse 兜底：
生产状态文件一律重定向到 pytest tmp，用例要断言真实文件时自己再 patch。

覆盖范围 = 测试可能触达的全部生产写路径：
  - 分账账本 / 在途委托 / 委托事件 / 对账锁（live_ledger、live_fills）
  - 成交流水、哨兵动作日志（live_trade_picks.LOG_DIR、live_price_watch.LOG_DIR）
  - 条件位文件（live_price_watch.WATCH_FILE，写坏 = 真实止损被抹）
  - 决策/状态/熔断/L2 快照等次级状态
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "agent_tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 次级状态模块在收集期就导入：fixture 是按 sys.modules 存在与否决定 patch 的，
# 而「在用例函数体里 import」的模块要等 fixture 跑完才出现 —— 那时 patch 已经
# 来不及，模块常量仍指向真实路径，用例一写就落到生产文件上。
# 这四个模块本就被测试直接 import（见 test_pool_quotes / test_live_l2_factors /
# test_live_breaker / test_live_llm_trade_retry），提前导入不引入新依赖，
# 实测总耗时 0.08s。
_OPTIONAL_STATE_MODULES = {
    # STATE_PATH 故意不列在这里：test_volatility_guard 在 setup_module 里把它指到
    # /tmp 并在用例里直接读写（模块级赋值不受 monkeypatch 管），显式 patch 会在
    # 每个用例开头把它拨回 tmp 路径 → 读到的状态是空的。它归下面的兜底网管。
    "live_hourly_analysis": ("LAST_DECISIONS_FILE",),
    "live_llm_trade": ("LOCK_FILE", "STATE_FILE"),
    "live_breaker": ("TRIP_DIR",),
    "live_l2_capture": ("FACTORS_FILE", "STATE_FILE", "STATUS_FILE", "MARKET_FILE"),
}
for _m in _OPTIONAL_STATE_MODULES:
    try:
        __import__(_m)
    except ImportError:      # 缺依赖时跳过：隔离面少一块，但不让整个套件无法收集
        pass

_PROD_LOGS = (ROOT / "logs").resolve()
_PROD_DATA = (ROOT / "data").resolve()


def _redirect_leftover_state_paths(monkeypatch, tmp_path, modules) -> list:
    """兜底网：仍指向生产 logs/、data/ 的模块级 Path 常量，一律重定向到 tmp。

    显式清单拦不住「import 时就按旧根算好的派生常量」——2026-09-11 审查 HIGH 实录：
    `live_price_watch.SKIP_STATE_FILE = LOG_DIR / "live_watch_notify_state.json"` 是
    独立常量，只 patch LOG_DIR 对它无效。测试跑到账户快照退化分支（哨兵假的账户
    通道）就写真实文件，把**实盘同一小时的告警去重标记吃掉**：之后真出现假活，
    `_notify_once` 命中去重直接 return，告警被静默。同类的还有
    live_hourly_analysis.STATE_PATH、live_breaker.EQUITY_LOG 等，以后新加的常量
    也归这张网管。
    """
    moved = []
    for mod in modules:
        for attr, val in list(vars(mod).items()):
            if attr.startswith("__") or not isinstance(val, Path):
                continue
            try:
                real = val.resolve()
            except OSError:
                continue
            # 落点单独开一棵 prod-state/ 树（不走 tmp_path/"logs"）：有些用例自己
            # 会 mkdir(tmp_path/"logs")，预建同名目录会让它们 FileExistsError。
            for root, dest in ((_PROD_LOGS, tmp_path / "prod-state" / "logs"),
                               (_PROD_DATA, tmp_path / "prod-state" / "data")):
                if real == root:
                    target = dest
                elif str(real).startswith(str(root) + "/"):
                    target = dest / val.name
                else:
                    continue
                dest.mkdir(parents=True, exist_ok=True)   # 父目录可得（见 writable 用例）
                monkeypatch.setattr(mod, attr, target)
                moved.append(f"{mod.__name__}.{attr}")
                break
    return moved


@pytest.fixture(autouse=True)
def _isolate_production_state(monkeypatch, tmp_path):
    """把生产状态文件的模块常量整体重定向到本用例的 tmp 目录。

    只 patch「模块常量」这一层（代码里都在调用点读模块属性）；用例自己的
    monkeypatch 在其后生效，仍然可指向自己的 tmp 文件。
    """
    import live_fills
    import live_ledger
    import live_price_watch
    import live_trade_picks

    logs = tmp_path / "logs"      # 不预建：写日志的模块自己 mkdir，预建会让
                                  # 「自己建 logs 目录」的用例 FileExistsError

    # 账本 / 委托 / 事件 / 终态台账 / 跨进程锁
    monkeypatch.setattr(live_ledger, "LEDGER_FILE", tmp_path / "live_ledger.json")
    monkeypatch.setattr(live_fills, "PENDING_FILE", tmp_path / "live_pending_orders.json")
    monkeypatch.setattr(live_fills, "EVENTS_FILE", tmp_path / "live_order_events.json")
    monkeypatch.setattr(live_fills, "OUTCOMES_FILE", tmp_path / "live_order_outcomes.json")
    monkeypatch.setattr(live_fills, "RECONCILE_LOCK_FILE", tmp_path / "live_reconcile.lock")
    # 成交流水（live_trade_picks.log_line）与哨兵动作日志
    monkeypatch.setattr(live_trade_picks, "LOG_DIR", logs)
    monkeypatch.setattr(live_price_watch, "LOG_DIR", logs)
    # 条件位：写坏它 = 真实止损被抹掉
    monkeypatch.setattr(live_price_watch, "WATCH_FILE", tmp_path / "live_watch.json")

    # 次级状态：模块已在收集期导入（见文件头），一律重定向；父目录先建好——
    # 少了它，锁文件 open("a+") 抛 FileNotFoundError 被 except OSError 吞掉 →
    # 用例静默走进「已有另一班在跑」，状态文件也只是打印告警后丢失（假绿）。
    for mod_name, attrs in _OPTIONAL_STATE_MODULES.items():
        mod = sys.modules.get(mod_name)
        if mod is None or not getattr(mod, "__file__", None):
            continue
        if str(Path(mod.__file__).resolve()).startswith(str(ROOT / "scripts")):
            state_dir = tmp_path / mod_name
            state_dir.mkdir(exist_ok=True)
            for attr in attrs:
                if hasattr(mod, attr):
                    monkeypatch.setattr(mod, attr, state_dir / attr)

    # 兜底网：上面清单漏掉的、或以后新加的「指向生产 logs//data/ 的模块常量」
    mods = [live_fills, live_ledger, live_price_watch, live_trade_picks]
    mods += [sys.modules[m] for m in _OPTIONAL_STATE_MODULES if sys.modules.get(m)]
    _redirect_leftover_state_paths(monkeypatch, tmp_path, mods)
