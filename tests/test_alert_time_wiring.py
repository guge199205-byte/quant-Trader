"""时区接线不变量：alert.sh 日键 + 三处「+08:00 标错」的时间戳。

2026-09-20 修的两类缺陷（都有线上实录）：
1. alert.sh 的按日去重键用裸 `date +%F`——主机是 JST（北京 +1h），
   北京 23:00（JST 午夜）就翻日：周五的两个未解决卖单事件在周六/周日
   北京 23:00 被重新公告（/tmp/.baymax_*_seen 三个日期文件为证）。
   breaker 块早就是 `TZ=Asia/Shanghai date +%F`，照搬成统一的 BJ_DATE。
2. 三处 `time.strftime("%Y-%m-%dT%H:%M:%S+08:00")`：strftime 取的是机器本地
   时间（JST），却标成 +08:00 → 时间戳比真实北京时间快 1 小时。
   （agent_commands.py:153 的 now.strftime 不在此列：now 由调用方传入且
   CLI 传的是 BJ aware 时间。）

alert.sh 是 shell，没法直接单测判定逻辑 → 源码扫描钉住缺陷模式；
两个 Python 写入点用行为测试（回拨 1 小时的 bug 会被抓住）。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

BJ = ZoneInfo("Asia/Shanghai")


def _sh() -> str:
    return (ROOT / "scripts" / "alert.sh").read_text(encoding="utf-8")


# ---------- alert.sh：所有「按日/按小时」键与面向人的时间戳 = 北京时间 ----------

def test_bj_date_defined_once_for_all_day_keys():
    sh = _sh()

    assert "BJ_DATE=$(TZ=Asia/Shanghai date +%F)" in sh
    assert "BJ_HOUR=$(TZ=Asia/Shanghai date +%H)" in sh
    assert sh.count("$BJ_DATE") >= 11, "9 个 _seen 去重键 + alpha 滞后键都要用 BJ_DATE"
    assert "$BJ_HOUR" in sh


def test_no_bare_local_date_keys_left():
    """裸 date +%F/%H/%u 会按 JST 翻日 → 未解决事件在北京 23:00 重播。"""
    sh = _sh()

    for pat in ("$(date +%F)", "$(date +%H)", "$(date +%u)",
                "$(date '+%Y-%m-%d %H:%M')", "$(date '+%F %T')"):
        assert pat not in sh, f"alert.sh 仍有裸本地时间：{pat}"


def test_alerts_log_header_and_api_restart_use_beijing_time():
    sh = _sh()

    assert "$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M')" in sh      # 块头 MSG
    assert sh.count("TZ=Asia/Shanghai date '+%F %T'") >= 2         # api 重启两条日志


def test_breaker_day_reuses_bj_date():
    sh = _sh()

    assert 'BREAKER_DAY=$(TZ=Asia/Shanghai date +%F)' not in sh
    assert 'BREAKER_DAY="$BJ_DATE"' in sh


def test_aidata_day_key_carries_the_checker_state():
    """按天去重键带状态词：`degraded`（熔断状态文件坏）与 `down`（通道坏）同一天是
    两件事，共用一个键 = 先到的那条把另一条吃掉。

    2026-09-21 盘满把这个形状演示了一遍：状态文件先被截成 0 字节（degraded），
    之后即便真有通道故障（down）也报不出来——一条告警掩盖另一条。
    状态词来自 `rt_down` 的**第 3 行**（机器可读的键，不是从文案里正则抠词）。
    """
    sh = _sh()

    assert """AIDATA_KEY=$(printf '%s\\n' "$RT_OUT" | sed -n 3p)""" in sh
    assert 'AIDATA_SEEN="/tmp/.baymax_aidata_alerted_${BJ_DATE}${AIDATA_KEY:+_$AIDATA_KEY}"' in sh


def test_rt_stale_check_is_wired_with_an_hour_key():
    """看板新鲜度哨兵必须真的挂在 alert.sh 上（2026-09-22 二轮审查 HIGH-1）。

    这条检查存在但没接线 = 它的价值是零，而「板子停更 → 六路告警冻结」的形态
    恰恰在告警面自己身上，只能靠源码扫描钉住（alert.sh 是 shell，跑不了单测）。
    """
    sh = _sh()

    assert "acheck rt_stale logs/rt_status.json" in sh
    assert "RTS_SEEN=\"/tmp/.baymax_rt_stale_${BJ_DATE}_${BJ_HOUR}\"" in sh, \
        "板子停更按小时去重（不是按天：它不是「一天一条」的慢性病）"


def test_order_events_check_receives_ledger():
    """argv[2]=账本：账本已无持仓的卖域事件自动失效，不再半夜刷屏。

    2026-09-22 起检查器统一走 alert.sh 的 `acheck`（按 rc 分流：缺参/崩溃不再等于
    「没问题」）——这里连**入口**一起钉住，防止有人改回直调。
    """
    line = next(l for l in _sh().splitlines() if "ORD_EVENTS=$(acheck order_events" in l)

    assert "logs/live_ledger.json" in line


def test_every_checker_call_goes_through_acheck():
    """唯一的直调只允许出现在 `acheck` 自己体内。

    绕过 acheck = 丢掉 rc 分流 → 检查器崩了/参数写错时，那条检查静默失效而告警面
    全绿（2026-09-02→09-22 分钟因子三周静默就是这个形状）。
    """
    sh = _sh()
    bare = [l.strip() for l in sh.splitlines()
            if "/usr/bin/python3 scripts/alert_checks.py" in l
            and not l.strip().startswith("#")]

    assert bare == ['out=$(/usr/bin/python3 scripts/alert_checks.py "$@" 2>"$err")'], \
        f"有检查器调用绕过了 acheck：{bare}"
    assert "acheck()" in sh


# ---------- 三处时间标注 ----------

def test_account_cache_ts_cn_is_beijing(tmp_path, monkeypatch):
    import live_account_cache as LAC

    monkeypatch.setattr(LAC, "CACHE_FILE", tmp_path / "broker_account_cache.json")
    LAC.save_snapshot({"asset": {"asset": 100.0}, "positions": [], "channel_used": None})

    snap = json.loads((tmp_path / "broker_account_cache.json").read_text(encoding="utf-8"))
    ts = datetime.fromisoformat(snap["ts_cn"])
    assert ts.utcoffset() == timedelta(hours=8)
    assert abs((ts - datetime.now(BJ)).total_seconds()) < 60, "ts_cn 必须是真实北京时间"


def test_agent_command_executed_ts_is_beijing(tmp_path, monkeypatch):
    import agent_commands as AC

    monkeypatch.setattr(AC, "COMMANDS_FILE", tmp_path / "commands.json")
    AC.COMMANDS_FILE.write_text(json.dumps({"version": 1, "commands": [
        {"id": "c1", "active": True, "op": "sell", "agent": "a", "code": "300408.SZ"}]}),
        encoding="utf-8")

    AC.consume({"id": "c1"})

    cmd = json.loads(AC.COMMANDS_FILE.read_text(encoding="utf-8"))["commands"][0]
    ts = datetime.fromisoformat(cmd["executed_ts"])
    assert ts.utcoffset() == timedelta(hours=8)
    assert abs((ts - datetime.now(BJ)).total_seconds()) < 60, "executed_ts 必须是真实北京时间"


def test_tdx_bridge_snapshot_ts_is_beijing():
    """桥内联快照写入器与 live_account_cache 是同一份数据（源码扫描：无 broker 可跑）。"""
    src = (ROOT / "agent_tools" / "brokers" / "tdx_bridge.py").read_text(encoding="utf-8")

    assert 'strftime("%Y-%m-%dT%H:%M:%S+08:00")' not in src
    snap_block = src[src.index('"ts_cn"'):]
    assert "timedelta(hours=8)" in snap_block[:400], "ts_cn 必须显式按 UTC+8 构造"
