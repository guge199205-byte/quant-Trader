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
  - 晚间研究纪要（logs/night_pool）与候选池产出（quantmind stock_picks）
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
    # 晚间研究/候选池取用（2026-09-12 批 6）：这两个模块常量在原值下指向生产
    # logs/ 与 quantmind 报告目录。实录：新增用例漏 patch NIGHT_POOL_DIR，
    # 一次测试就把真实 logs/night_pool/{session}.md 覆写成桩内容。
    "night_pool_agent": ("NIGHT_POOL_DIR", "PICKS_DIR"),
    "picks_source": ("PICKS_DIR",),
    # 决策远期记分卡（2026-09-18 实录）：POOL/SCORECARD 是**生产**决策池与记分卡，
    # 却不在隔离网里。任何走到实盘路径的用例（test_hourly_exec_settle 等调
    # live_hourly_analysis / live_llm_trade，两处都会 ingest_decisions(path=None
    # → 模块常量 POOL)）都会往真实 logs/decision_pool.jsonl **追加测试决策**：
    # 记分卡的「看多意向」胜率、P2 池位置报告全被 fixture 数据污染，而 ingest
    # 按 make_id 幂等 → 同一个用例重跑不会再加行，越跑越难察觉（实录：连跑两次
    # 全量套件，真实池从 138 行涨到 141 行，混入 09-12/09-18 两条买入）。
    "decision_track": ("POOL", "SCORECARD"),
    # 主机看门狗（2026-09-18）：心跳/离线日志都是生产观测面，测试一律重定向
    # （2026-09-21 增补留痕文件由下面的兜底网管）
    "host_watchdog": ("HB_FILE", "GAP_LOG"),
    # 整点轮死进程对账（2026-09-21 盘满事故）：ROUND_FILE 是生产轮状态——对账会
    # 显式写它（收尾 failed），测试必须重定向，否则用例一跑就把真实轮状态改写
    "round_reconcile": ("ROUND_FILE",),
    # 影子代价账（2026-09-19）：GHOST 与 decision_track.POOL 同族——两条买入路径
    # 的每个否决点都会往里写一行。不隔离就会重演 decision_pool 那次事故（fixture
    # 事件混进真实影子账，且 make_id 幂等 → 越跑越难察觉），
    # 见 tests/test_production_isolation.py 的动态回归钉。
    # 只隔离 GHOST（状态）；REGISTRY 是 configs/ 下的只读规则表，重定向到 tmp
    # 会让「登记表覆盖所有规则」的防线在测试里对着空表跑（假绿）。
    "ghost_ledger": ("GHOST",),
    # 降级通知限频状态（2026-09-21）：notify_throttled 的 key→时间戳落在生产 logs/，
    # 用例（哨兵重布防/未成交告警）写进去会把真实告警的 30 分钟窗口提前耗掉。
    "live_account_cache": ("_NOTIFY_STATE",),
    # AiData 跨进程熔断器（2026-09-22）：BREAKER_FILE 落 logs/。**这条不是「怕写坏」，
    # 是「怕读到」**——AiData 真故障期间生产文件长期是「熔断中」，用例若读到它，
    # 每一个 tdx_aidata.available() 都会变 False，断言 AiData 路径的用例会以
    # 「本地文件影响测试结果」这种最难查的形态变红。
    # 注意：本模块不在 scripts/ 下 → 上面那段 startswith(ROOT/"scripts") 会跳过它，
    # 真正接管的是文件末尾的兜底网（靠这里的登记把模块送进 mods 列表）。
    "agent_tools.datasources.tdx_aidata": (),
}
for _m in _OPTIONAL_STATE_MODULES:
    try:
        __import__(_m)
    except ImportError:      # 缺依赖时跳过：隔离面少一块，但不让整个套件无法收集
        pass

_PROD_LOGS = (ROOT / "logs").resolve()
_PROD_DATA = (ROOT / "data").resolve()
# quantmind 侧研究产物（stock_picks 候选池等）：不在本仓 logs//data/ 下，但同样是
# 生产写路径——晚间研究池被测试覆写 = 次日实盘候选池被污染（2026-09-12 实录）。
_PROD_QMDATA = (ROOT.parent / "projects/quantmind/data").resolve()


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
                               (_PROD_DATA, tmp_path / "prod-state" / "data"),
                               (_PROD_QMDATA, tmp_path / "prod-state" / "quantmind")):
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
def _no_real_aidata(monkeypatch):
    """测试默认禁用 live_quotes 的 AiData 源（2026-09-15 实录：mock broker 的
    用例会先撞真 AiData 网络+限流退避，全量测试被拖过 timeout）。
    要专门测 aidata 路径的用例自行 delenv（见 test_live_quotes.py 的 aidata fixture）。"""
    monkeypatch.setenv("LIVE_QUOTES_DISABLE_AIDATA", "1")


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
    import push_notify  # 2026-09-18：测试跑 live_quotes 时经它写生产 push_notify.log

    mods = [live_fills, live_ledger, live_price_watch, live_trade_picks, push_notify]
    mods += [sys.modules[m] for m in _OPTIONAL_STATE_MODULES if sys.modules.get(m)]
    _redirect_leftover_state_paths(monkeypatch, tmp_path, mods)

    # 出站通知（QQ）绝不真发（2026-09-18）：settle_place_fill / reconcile 在成交后
    # 都会推 notify_order——任何走到这两处的用例都会打真网络。统一替换为记录器：
    #   live_fills._push_fill_notice.sent      → 本用例收到的推送 kw 列表
    #   live_fills._push_fill_notice.original  → 原函数（要断言报文组装/转发口径的
    #     用例自行 monkeypatch.setattr(live_fills, "_push_fill_notice", .original)，
    #     并在 push_notify.notify_order 上再打桩）
    sent: list = []

    def _push_recorder(**kw):
        sent.append(kw)

    _push_recorder.sent = sent
    _push_recorder.original = live_fills._push_fill_notice
    monkeypatch.setattr(live_fills, "_push_fill_notice", _push_recorder)

    # 执行时段闸门（2026-09-18）墙钟无关化：测试在任意时刻跑（含周末、收盘后）
    # 都不该被 9:30-11:30/13:00-14:57 窗口拦住导致「不下单」的假绿/假红；
    # 专门测窗口闸门的用例自行把这层桩改掉（见 test_exec_window_guard.py）。
    import live_hourly_analysis
    import live_llm_trade

    monkeypatch.setattr(live_hourly_analysis, "in_continuous_auction",
                        lambda now: True)
    monkeypatch.setattr(live_llm_trade, "in_continuous_auction",
                        lambda now: True)
