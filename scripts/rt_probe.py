#!/usr/bin/env python3
"""实时行情源健康看板：桥 / Fuyao / TdxAiData / quantdb 四路状态一次探齐。

输出 logs/rt_status.json（供日报/告警消费）+ 一行摘要。
cron：每 5 分钟（alert 联动前先看这里）。探针失败=该路降级，不影响主流程。
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "rt_status.json"
CN_TZ = ZoneInfo("Asia/Shanghai")
# 成功证据的时效上界：超过它就只剩「没有近期证据」（unproven），不再叫健康。
# 取 15 分钟 = 3 倍于探针 cron 间隔（5 分钟）：容忍一次漏跑，又能当天发现通道静默。
OK_EVIDENCE_TTL_SEC = 900.0
# 失败证据的「旧」阈值：超过它仍判 down（没人复测 ≠ 已恢复），但必须在文案里
# 说明年龄——否则「自 09:00 起连续失败」在第三天读起来像刚刚发生的故障。
FAIL_EVIDENCE_STALE_SEC = 6 * 3600.0
# 时钟抖动容忍：last_ok_ts 略微超前（NTP 步进）不算异常；大偏移一律不予采信。
CLOCK_SKEW_TOL_SEC = 60.0
for _p in (str(ROOT / "scripts"), str(ROOT)):
    # scripts/：直接 import live_quote（同目录）。ROOT：`import agent_tools.*`（仓库根的包）
    # ——cron 跑的是 `python /path/scripts/rt_probe.py`，sys.path[0] 只有 scripts/，
    # 缺 ROOT 时 probe_aidata 只能靠「main() 恰好先跑 probe_fuyao、后者顺手插了 ROOT」
    # 这个顺序巧合活着；顺序一改就每天误报一条通道 DOWN。契约写在导入点上。
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _env_kv(prefix: str = "") -> dict:
    """读 .env 键值。读失败返回空 dict——探针不能因为 .env 不可读就整个停更。

    停更的后果**不是静默**：`alert_checks.rt_stale_line` 盯 logs/rt_status.json 的
    mtime（alert.sh 2d-3），板子一旦停止更新就按小时报「看板失联」。这条链是本文件
    与告警面之间的契约：这里只管「能写就写」，写不出去由那边喊（2026-09-22 二轮审查
    HIGH-1：在此之前那句话是假的——全仓没有任何一处看板子 mtime）。"""
    out: dict = {}
    try:
        text = (ROOT / ".env").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        if prefix and not line.startswith(prefix):
            continue
        k, _, v = line.partition("=")
        if k.strip():
            out[k.strip()] = v.strip().strip('"')
    return out


def _bridge_urls() -> list:
    """候选桥地址：运行时覆盖（config/tdx_bridge.json，broker 断线自动发现
    会写这里）优先，其次 .env 声明地址——IP 变动自愈后看板不误报 DOWN。"""
    out: list = []
    try:
        ov = json.loads((ROOT / "config" / "tdx_bridge.json").read_text(encoding="utf-8"))
        u = str(ov.get("bridge_url") or "").rstrip("/")
        if u:
            out.append(u)
    except (OSError, json.JSONDecodeError):
        pass
    u = str(_env_kv("TDX_BRIDGE_").get("TDX_BRIDGE_URL", "")).rstrip("/") \
        or "http://192.168.31.13:8550"
    if u not in out:
        out.append(u)
    return out


def probe_bridge() -> dict:
    token = _env_kv("TDX_BRIDGE_").get("TDX_BRIDGE_TOKEN", "")
    import urllib.request

    last_err = "无候选桥地址"
    for url in _bridge_urls():
        try:
            req = urllib.request.Request(
                url + "/api/v1/tdx/call",
                data=json.dumps({"method": "get_market_snapshot",
                                 "params": {"stock_code": "600519.SH"}}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + token},
                method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read().decode())
            res = d.get("result") or {}
            now = float(res.get("Now") or 0)
            if now > 0:
                return {"ok": True, "now": now, "via": url,
                        "ts": datetime.now().isoformat(timespec="seconds")}
        except Exception as e:  # noqa: BLE001 单地址失败继续探下一候选
            last_err = str(e)[:120]
    return {"ok": False, "error": last_err}


def probe_fuyao() -> dict:
    """Fuyao 探活 = live_quote 的同一函数：**目标行真的匹配到有效价**才算 OK。

    2026-09-11 前只看 `len(items)>0`：上游忽略 thscode、返回全市场别家行情也判健康
    （day 里桥断时备胎恒给平安银行价，看板却全绿）。条数降级为 count_hint 提示。
    """
    try:
        import live_quote

        return live_quote.fuyao_check("600519.SH")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def probe_aidata() -> dict:
    """TdxAiData 通道探针 —— 三态，判据是**最近真的调用过、结果怎样**。

    旧判据是文件存在性（`/opt/tdx-aidata/TdxAiData.ini` 在不在）。那是「装了没」，
    不是「通不通」：2026-09-02 起 .so 内部挂死三周（617 轮里 502 轮烧在超时上），
    客户端文件一直在位 → 看板恒 `ok: true, fail_streak: 0`，告警永不触发。

    新判据读跨进程熔断状态 `logs/aidata_breaker.json`（tdx_aidata 每轮调用后写）：
      disabled  —— 人工闸门（.env 的 LIVE_QUOTES_DISABLE_AIDATA=1）：不是故障也不是
                   健康，单列「关闭」。既不该每天假告警，也不该粉饰成 OK。
      down      —— 熔断窗打开、或失败计数未清零（窗口过期但通道仍是坏的）。
      degraded  —— **熔断状态文件本身读不出来**（0 字节/JSON 断/权限）：通道也许好
                   也许坏，但「保护机制失效」是确定的——不能报健康。单列，是因为
                   把它说成 down 是另一种误诊（错在状态文件，不在通道）。
      ok        —— 有过**近期**成功记录且计数已清零。
      unproven  —— 没有近期证据：从没被调用过，或最后一次成功已超过 TTL。旧实现
                   在这两种情形都报「健康」，那正是假绿的本体；报「故障」又会让
                   升级瞬间刷屏。如实说「未知」。
    """
    try:
        from agent_tools.datasources import tdx_aidata as ta

        # 闸门判定两条腿都要有：`gate_reason()` 读 os.environ（正常运行时由 .env
        # 经 load_dotenv 灌入），`_disabled_from_env_file()` 直接读 .env。
        # 只靠前者会静默依赖**别人的副作用**：cron 的 `env -i` 下 os.environ 本来
        # 是干净的，今天能判出 disabled 纯粹因为 import 链上 price_tools 有模块级
        # load_dotenv()；谁把它挪进函数，看板就从「关闭」无声翻成一条陈旧的 DOWN
        # （breaker 里还留着禁用前的失败记录 → 每天一条假故障）。2026-09-22 审查 MEDIUM-3。
        # 两者取「任一为禁用」：闸门是安全开关，分歧时按更保守的一侧走。
        reason = ta.gate_reason() or _disabled_from_env_file()
        if reason:
            return {"ok": True, "state": "disabled", "note": reason}
        st = ta.read_breaker()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "state": "down", "error": f"熔断状态不可读: {str(e)[:80]}"}
    if st.get("_unreadable"):
        return {"ok": False, "state": "degraded",
                "error": f"熔断状态文件不可读（{st.get('_error') or '—'}）：熔断保护失效"}
    # 计数解析必须容忍怪类型：这个字段来自**跨进程落盘的共享文件**，写入方有
    # bug / 有人手改 / 磁盘写坏一半都会出现「合法 JSON + 怪类型」，而 `int("abc")`
    # 抛出去就是整个探针进程死掉 → 板子不写 → 六路告警面全部冻结（还看不出来）。
    raw_streak = st.get("fail_streak")
    streak = _as_int(raw_streak)
    # 注意两个 streak 不是一回事：本字段数的是**通道调用**连续失败了几次
    # （失败在第几层都可能），看板顶层的 fail_streak 由 apply_streaks 数**探测轮**
    # ——后者才是去抖的输入。两个数都留着，排查时「3 轮里失败了 7 次」有用。
    if st and ((streak or 0) > 0 or not st.get("ok")):
        out = {"ok": False, "state": "down", "channel_fail_streak": streak or 0,
               "error": str(st.get("error") or "调用失败")[:120],
               "last_ok_ts": st.get("last_ok_ts")}
        if streak is None and raw_streak is not None:
            # 数不出来就报 0 并说明，**不编一个数**——但失败本身是真的，照报 down
            out["note"] = (f"fail_streak 字段类型异常（{type(raw_streak).__name__}），"
                           f"按 0 次计")
        failed_at = _as_float(st.get("ts"))
        if failed_at is not None:
            fail_age = time.time() - failed_at
            out["fail_age_sec"] = round(fail_age)
            if fail_age > FAIL_EVIDENCE_STALE_SEC:
                # 未复测 ≠ 已恢复（仍是 down），但「自 <首次失败时刻> 起」那行文案
                # 是板子按**本轮**补写的，不说明年龄就会被读成正在发生的故障。
                prev = f"{out['note']}；" if out.get("note") else ""
                out["note"] = prev + (f"这条失败证据已是 {fail_age / 3600:.0f} 小时前，"
                                      f"此后没有调用记录（未复测，不等于已恢复）")
        return out
    if st.get("last_ok_ts"):
        age = _evidence_age_sec(st.get("last_ok_ts"))
        # 「最后一次成功在三小时前」不是健康，只是**没有近期证据** → 与「从没被调用
        # 过」同义。旧实现无时效上界：成功过一次就永远 OK，通道死了也看不见。
        # 解析不出（age is None）同样证明不了新鲜——**不能默认当新鲜**。
        if age is None:
            return {"ok": True, "state": "unproven", "last_ok_ts": st.get("last_ok_ts"),
                    "note": f"最后一次成功时刻无法解析（{str(st.get('last_ok_ts'))[:40]}）"}
        if age < -CLOCK_SKEW_TOL_SEC:
            # 未来时刻对任何 TTL 都「永远新鲜」：一次用错基准的写入就能把通道
            # 永久刷绿（比没有上界更坏的假绿）。小抖动（对时）不算。
            return {"ok": True, "state": "unproven", "last_ok_ts": st.get("last_ok_ts"),
                    "note": f"最后一次成功时刻在未来（{-age:.0f} 秒后），不予采信"}
        if age <= OK_EVIDENCE_TTL_SEC:
            return {"ok": True, "state": "ok", "last_ok_ts": st.get("last_ok_ts")}
        return {"ok": True, "state": "unproven", "last_ok_ts": st.get("last_ok_ts"),
                "note": f"最后一次成功在 {age / 60:.0f} 分钟前"
                        f"（>{OK_EVIDENCE_TTL_SEC / 60:.0f} 分钟），之后没有调用记录"}
    return {"ok": True, "state": "unproven"}


def _evidence_age_sec(last_ok_ts: Any) -> Optional[float]:
    """`last_ok_ts`（北京时间 ISO）距今秒数；解析不出 → None（= 无法证明新鲜）。"""
    try:
        d = datetime.fromisoformat(str(last_ok_ts))
    except ValueError:
        return None
    if d.tzinfo is None:            # 老产物可能没带时区；按北京口径解释
        d = d.replace(tzinfo=CN_TZ)
    return (datetime.now(CN_TZ) - d).total_seconds()


def _as_int(v: Any) -> Optional[int]:
    """状态文件的计数 → int；**解析不出返回 None**（调用方负责别编数）。

    与 tdx_aidata._as_int 的差别在失败语义：那边是写入侧、缺口按 0 继续累加即可；
    这边是读侧，要把「数不出来」和「0 次失败」分开说。共同点：都容忍坏形状——
    JSON 的 `Infinity`/`NaN` 是合法 token，`int(inf)` 抛 **OverflowError**；
    `True` 是 int 子类但不是计数。
    """
    if isinstance(v, bool) or v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(v: Any) -> Optional[float]:
    """状态文件的 epoch 时刻 → float；解析不出返回 None。`NaN` 视为无效。"""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if f != f else f      # NaN != NaN：时刻是 NaN 等于没有时刻


def probe_tencent() -> dict:
    """腾讯行情（免 key 公开单只，第三备胎：现价/开高低/量/五档简化）。"""
    try:
        import urllib.request

        with urllib.request.urlopen(
                "http://qt.gtimg.cn/q=sh600519", timeout=5) as r:
            raw = r.read().decode("gbk", "ignore")
        f = raw.split("=", 1)[1].split("~") if "=" in raw else []
        price = float(f[3]) if len(f) > 3 else 0
        return {"ok": price > 0, "now": price}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def probe_quantdb() -> dict:
    import glob

    for b in (ROOT.parent / "projects/quantmind/data/quantdb/1_kline_data/daily_backward",
              Path("/data/quantdb/1_kline_data/daily_backward")):
        if b.is_dir():
            parts = sorted(glob.glob(f"{b}/dt=*"))
            return {"ok": bool(parts), "latest": parts[-1].rsplit("dt=", 1)[-1] if parts else None}
    return {"ok": False, "error": "quantdb 目录缺失"}


def probe_account() -> dict:
    """账户通道探针（与行情通道分开的一条链路）。

    2026-09-10 实录：桥行情侧全通（快照/K线正常），account/query 却整日返
    asset=0、positions=[]——交易账号掉线。当日 7 轮盘中分析 + 3 次 09:35 调仓
    + 全部哨兵条件位静默哑火，而看板只探行情 → 零告警，收盘后复盘才发现。
    判据与 live_llm_trade._query_account_with_retry 同口径：asset > 0 才算通。
    """
    token = _env_kv("TDX_BRIDGE_").get("TDX_BRIDGE_TOKEN", "")
    env = _env_kv()
    body = {"account": env.get("TDX_ACCOUNT", ""),
            "account_type": env.get("TDX_ACCOUNT_TYPE", "") or "tdx"}
    import urllib.request

    last_err = "无候选桥地址"
    for url in _bridge_urls():
        try:
            req = urllib.request.Request(
                url + "/api/v1/account/query",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + token},
                method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read().decode())
            res = d.get("result") if isinstance(d.get("result"), dict) else d
            asset = float((res.get("asset") or {}).get("asset") or 0)
            n_pos = len(res.get("positions") or [])
            if asset > 0:
                return {"ok": True, "asset": asset, "positions": n_pos, "via": url,
                        "ts": datetime.now().isoformat(timespec="seconds")}
            last_err = f"asset={asset:,.0f} positions={n_pos}（行情通/账号掉线＝桥假活）"
        except Exception as e:  # noqa: BLE001 单地址失败继续探下一候选
            last_err = str(e)[:120]
    return {"ok": False, "error": last_err}


SOURCES = ("bridge", "fuyao", "tencent", "aidata", "quantdb", "account")


def _prev_board() -> dict:
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def apply_streaks(board: dict, prev: dict, ts: str) -> dict:
    """把「连续失败次数 / 首次失败时刻」并进看板（纯函数，便于测试）。

    单点 DOWN 多为 5-10 分钟自愈的抖动（fuyao 一天十几次），告警侧据此去抖；
    没有 streak 就只能「一次失败就报」→ 刷屏，或者不报 → 漏真故障。
    """
    for k in SOURCES:
        v = board.get(k)
        if not isinstance(v, dict):
            continue
        p = prev.get(k) if isinstance(prev.get(k), dict) else {}
        if v.get("ok"):
            v["fail_streak"] = 0
            v.pop("first_fail_ts", None)
        else:
            # 上一份板子也是**落盘的 JSON**：坏类型裸 int() 抛出去 = main() 写不了
            # 新板子 = 六路告警面冻在上一份上（与 probe_aidata 抛一样致命）。
            v["fail_streak"] = (_as_int(p.get("fail_streak")) or 0) + 1
            v["first_fail_ts"] = p.get("first_fail_ts") or ts
    return board


# ok=True 但不是「健康」的两态：显示成 OK 会把它们混进绿色里，显示成 DOWN 又是误报
_CELL_STATE = {"disabled": "关闭", "unproven": "未用"}


def _cell(board: dict, key: str, label: str) -> str:
    v = board.get(key) or {}
    if v.get("ok"):
        mark = _CELL_STATE.get(v.get("state") or "")
        return f"{label}={mark}" if mark else f"{label}=OK"
    err = str(v.get("error") or "").strip()
    detail = f"{v.get('fail_streak') or 0}次" + (f"/{err[:60]}" if err else "")
    # 非 ok 不等于通道 DOWN：degraded（熔断状态文件坏掉）说的是保护失效，
    # 统一打成 DOWN 就是另一种误诊——错在状态文件，不在通道。
    mark = "DEGRADED" if v.get("state") == "degraded" else "DOWN"
    return f"{label}={mark}({detail})"


# 与 tdx_aidata._GATE_VARS/_OFF_VALUES 同一套方言（本文件不能假设 os.environ 已被
# .env 灌过，理由见 probe_aidata 的注释）。
_GATE_VARS = ("TDX_AIDATA_DISABLE", "LIVE_QUOTES_DISABLE_AIDATA")
_OFF_VALUES = ("1", "true", "yes")


def _disabled_from_env_file() -> str:
    """直接读 .env 的闸门判定 → 原因文案；未禁用/读不到返回空串。

    `_env_kv` 是手写解析器（不认行内注释）：`VAR=1  # 应急` 会解析成
    `"1  # 应急"`。在这里把 `#` 之后砍掉——本函数的取值域只有 1/true/yes，
    `#` 不可能是值的一部分。**安全开关的「没认出来」是最坏的失败形态**
    （静默地把闸门当成没设），所以宁可在这里多一步。
    """
    kv = _env_kv()
    for var in _GATE_VARS:
        val = str(kv.get(var) or "").split("#", 1)[0].strip().strip('"').lower()
        if val in _OFF_VALUES:
            return f"TdxAiData 已禁用（.env 的 {var}={val}）"
    return ""


def _guarded(name: str, fn) -> dict:
    """单个探针抛异常不许带走整份板子。

    `probe_quantdb` 全函数无 try/except、`probe_bridge` 的 `_bridge_urls()` 在 try
    之外——任一处抛（或盘满时它读文件抛），main() 就死在写盘之前：**板子一字不写**，
    六路降级告警全部冻在上一份板上且表面全绿（2026-09-21 盘满的形态）。
    就地转成该路自己的一条 DOWN，用**独立状态词** `probe_error`：探针自己坏了不等于
    通道坏了（同 `degraded` 的分野），否则排错的人会去查桥而不是查探针。
    """
    try:
        r = fn()
        return r if isinstance(r, dict) else {"ok": False, "state": "probe_error",
                                              "error": f"{name} 探针返回了非字典：{type(r).__name__}"}
    except Exception as exc:  # noqa: BLE001 —— 探针任何异常都不许外溢，见 docstring
        return {"ok": False, "state": "probe_error",
                "error": f"{name} 探针自身异常：{type(exc).__name__}: {exc}"[:200]}


def main() -> int:
    ts = datetime.now().isoformat(timespec="seconds")
    board = {"ts": ts}
    for k in SOURCES:
        board[k] = _guarded(k, globals().get(f"probe_{k}", lambda: {"ok": False}))
    apply_streaks(board, _prev_board(), ts)
    tmp = OUT.with_name(OUT.name + ".tmp")   # 原子写：alert.sh 可能正在读
    try:
        tmp.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(OUT)
    except OSError as exc:
        # 写不出去**必须让人看见**：留一行带时刻的错误 + 非 0 退出码（cron/看门狗唯一
        # 的非人读信号），而不是一个裸 traceback 淹进 logs/rt_probe.log。真正兜住
        # 「六路静默冻结」的是 alert_checks.rt_stale_line（板子 mtime 哨兵）——
        # 这里只负责把原因写清楚。
        print(f"[{ts}] ⛔ 看板写不出去（{type(exc).__name__}: {exc}）：六路降级告警"
              f"将冻在上一份 {OUT.name} 上，直至恢复；先查磁盘/权限")
        return 1
    bad = [k for k in SOURCES if not (board.get(k) or {}).get("ok")]
    cells = " ".join(_cell(board, k, lab) for k, lab in
                     (("bridge", "桥"), ("fuyao", "Fuyao"), ("tencent", "腾讯"),
                      ("aidata", "AI数据"), ("quantdb", "quantdb"), ("account", "账户")))
    print(f"✅ 行情源: {cells}" + (f"  ⚠️ 降级: {','.join(bad)}" if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())