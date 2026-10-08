"""通达信 TdxAiData 官方数据接口（实时行情源）。

背景（2026-08-28 通达信测试版新增）：官方提供 TdxAiData.dll/so + tqServer.py，
不依赖本地客户端，Windows/Linux/Mac 通用，行情实时，支持分钟线/分笔/订阅。
文档：help.tdx.com.cn/quant/docs（TdxAiData接口调用章）。

依赖文件（TdxAiData 安装目录，须已就位）：
  libTdxAiData.so  /  TdxAiData.ini（含 token + 服务器地址 aihs.tdx.com.cn:7709）  /  tqServer.py
  TDX_AIDATA_DIR 环境变量指向该目录（默认 /opt/tdx-aidata）

实测约束（2026-08-31）：
  - 测试版接口有突发限流：服务端间歇性返回 "Token Insufficient"（错误码 13），
    实测放行 2-3 次请求后冷却数分钟（与商城积分余额无关，用户确认积分充足）
    → 本模块指数退避重试（10s/20s/40s/60s），并把请求压到最少（批量）
  - count 参数模式不可用（必失败且会带坏同进程后续连接）→
    一律用 start_time/end_time 区间模式，count 由 _start_for_count 换算
  - stock_list 支持多只股票批量，一次调用拿全部 → 用 get_klines_batch
  - TdxAiData 只做数据（K线/分时/分笔/订阅），不提供交易功能；下单仍走 8550 桥
  - 服务器不可达 → 调用抛 RuntimeError，调用方应回退桥行情
  - ⚠️ available() 只代表「可导入」，**不代表通道可用**：通道级故障时 .so 内部
    会自行重连（stdout 打「[错误码 11] 连接失败」）并长时间不返回，实测
    `get_klines("600519.SH", interval="5m")` 60s 未返回。判活必须看熔断状态
    （breaker_open()），闸门必须用 gate_reason() 而不是 available()。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CN_TZ = ZoneInfo("Asia/Shanghai")

AIDATA_DIR = os.getenv("TDX_AIDATA_DIR", "/opt/tdx-aidata")

# 退避阶梯（秒）。2026-09-22 由 [10,20,40,60] 截短：该阶梯是给「突发限流」设计的，
# 但对通道级故障只是放大器——5 次往返 + 130s 睡眠，叠加 .so 内建连接超时后
# 单次调用最坏 ≈380s（617 轮实录：502 轮走到这条路径）。
# 跨轮重试已由熔断短路兜住，阶梯只需覆盖单次抖动。
# **时长仍需调用方兜底**：挂死型故障在 .so 内部，异常回不来，本模块管不到
# ——做时间盒的调用方必须在超时分支调 record_fail()，否则熔断永远不开。
_DEFAULT_RETRY_GAPS = [5.0, 10.0]

# 熔断：连续 _BREAKER_THRESHOLD 次失败即开窗，窗口逐步拉长。
# 状态**落盘**（跨进程）——进程内标记拦不住 cron 的下一轮（每轮新进程），
# 这正是 2026-09-21 止血（.env 开关）没管住 live_l2_capture 的原因。
#
# 落 logs/ 而不是 data/：① 已被 .gitignore 忽略，不会误入库；② logs/ 是既定的
# 跨进程/跨容器共享状态目录（rt_status.json、broker_account_cache.json 都在此）。
# 必须是**模块级 Path**：tests/conftest 的兜底网只扫 isinstance(val, Path)。
BREAKER_FILE = ROOT / "logs" / "aidata_breaker.json"
_BREAKER_THRESHOLD = 3
# 冷却阶梯（秒）：限流是分钟级自愈的（本模块原始故障形态），一上来就封 30 分钟
# 会过度压制；持续故障则逐级升到 1 小时，避免每 5 分钟一轮的 cron 反复重打。
# 末位兜底 3600 > cron 间隔（300s）→ 不会形成热循环。
_BREAKER_COOLDOWN_LADDER = (300.0, 900.0, 1800.0, 3600.0)

# 禁用开关。与 live_quotes._aidata_allowed 同语义（1/true/yes），不引入第二套方言。
_GATE_VARS = ("TDX_AIDATA_DISABLE", "LIVE_QUOTES_DISABLE_AIDATA")
_OFF_VALUES = ("1", "true", "yes")

_tqs = None
_load_error: Optional[str] = None
_last_ok_write = 0.0            # last_ok_ts 的写盘节流（见 record_ok）
_OK_WRITE_MIN_GAP_SEC = 60.0


def gate_reason() -> str:
    """禁用开关 → 原因文案；未禁用返回空串。

    每次现读 os.environ（**不用** AIDATA_DIR 那种 import 时常量）：.env 置位后
    对**所有**调用点立即生效，而不是只对解析它的那一个模块生效。
    """
    for var in _GATE_VARS:
        val = (os.environ.get(var) or "").strip().lower()
        if val in _OFF_VALUES:
            # 回显**实际的**变量与值：写死 `=1` 会在 `=true`/`=yes` 时指向错的东西
            # （排查的人会去 .env 搜一个不存在的字面量）。
            return f"TdxAiData 已禁用（{var}={val}）"
    return ""


def _retry_gaps() -> List[float]:
    """退避阶梯，可用 TDX_AIDATA_RETRY_GAPS="5,10" 覆盖（限流容忍度现场可调）。"""
    raw = os.getenv("TDX_AIDATA_RETRY_GAPS", "")
    if raw.strip():
        try:
            return [float(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            pass
    return list(_DEFAULT_RETRY_GAPS)


def _load() -> Any:
    """懒加载 tqServer.tqs（首次调用时 import）。

    闸门先于缓存判断：置位后立即生效（含进程内已加载的情形），且**不写
    _load_error**——那是「加载失败」的缓存，禁用是可撤销的临时态，写进去就
    再也开不回来了。
    """
    global _tqs, _load_error
    if gate_reason():
        return None
    if _tqs is not None or _load_error is not None:
        return _tqs
    try:
        import sys

        if AIDATA_DIR not in sys.path:
            sys.path.insert(0, AIDATA_DIR)
        from tqServer import tqs  # type: ignore

        _tqs = tqs
    except Exception as exc:  # noqa: BLE001
        _load_error = f"TdxAiData 不可用（{AIDATA_DIR}）: {exc}"
    return _tqs


def available() -> bool:
    """已加载且未被禁用。**只代表可导入**，不代表通道可用（见模块 docstring）。"""
    return _load() is not None


def _require() -> Any:
    t = _load()
    if t is None:
        raise RuntimeError(gate_reason() or _load_error or "TdxAiData 未加载")
    return t


# ---------- 熔断（跨进程状态，logs/aidata_breaker.json） ----------

def _iso(epoch: float) -> str:
    """epoch → 北京时区 ISO（显式 ZoneInfo；本机时区是 JST，裸 strftime 会标错）。"""
    return datetime.fromtimestamp(epoch, CN_TZ).isoformat(timespec="seconds")


def read_breaker() -> dict:
    """读熔断状态。

    **「从没跑过」与「写坏了」必须分开**：前者是正常初态（返回 `{}`），后者返回
    `{"_unreadable": True, "_error": ...}`。旧实现把两者一并吞成 `{}`，后果是
    熔断机制静默失效——`breaker_open()` 恒 None（fail-open，这个方向本身是对的：
    状态文件坏掉不该挡住调用）、看板显示健康、`probe_aidata` 里那条「不可读 →
    down」的响路对**最可能的损坏形态**（写盘中途盘满 → 0 字节，2026-09-21 盘满
    事故已把状态文件截成过 0 字节）根本不可达。坏掉要看得见。
    """
    try:
        raw = BREAKER_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        if BREAKER_FILE.exists():      # 存在却读不了（权限/盘满）≠ 从没跑过
            return {"_unreadable": True, "_error": f"读取失败: {exc}"[:200]}
        return {}
    except ValueError as exc:          # 坏字节 → UnicodeDecodeError
        return {"_unreadable": True, "_error": f"编码损坏: {exc}"[:200]}
    try:
        d = json.loads(raw)
    except ValueError as exc:
        return {"_unreadable": True, "_error": f"JSON 损坏: {exc}"[:200]}
    if not isinstance(d, dict):
        return {"_unreadable": True, "_error": f"顶层不是对象: {type(d).__name__}"}
    return d


def _as_int(v: Any) -> int:
    """状态文件里的计数容忍被写坏（"3" 可救，"abc" 归零）——不因状态坏而炸调用。

    两个容易漏的形状：JSON 的 `Infinity`/`NaN` 是**合法 token**，`int(inf)` 抛的是
    `OverflowError`（不是 ValueError）——漏了它，`record_fail` 读回旧值那一步就会
    变成异常，AiData 的每一次失败都换一个 OverflowError（比失败本身更难查）；
    `True` 是 int 子类，但不是计数。
    """
    if isinstance(v, bool):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_float(v: Any) -> float:
    """同 `_as_int`：`open_until` 被写成 "明天"/NaN 时按「未熔断」处理，不抛。"""
    if isinstance(v, bool):
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return 0.0 if f != f else f       # NaN → 0.0（「未熔断」，与 docstring 一致）


def _warn(msg: str) -> None:
    """熔断状态自身的故障没有别的出口：走 stderr，带北京时间戳（本机是 JST）。"""
    print(f"[{_iso(time.time())}] ⚠️ AiData 熔断状态: {msg}", file=sys.stderr)


def _write_breaker(**fields) -> None:
    """原子写；失败**留痕**（写不进去 = 熔断保护失效，不能无声无息）。

    tmp 名带 pid：cron 轮与手动轮可能同时写，共用一个 .tmp 会互相踩——赢家
    可能把对方写了一半的内容 replace 上去，产出一个损坏的状态文件。
    """
    try:
        BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BREAKER_FILE.with_name(f"{BREAKER_FILE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(BREAKER_FILE)
    except OSError as exc:
        _warn(f"写入失败（{BREAKER_FILE}）: {exc}")


def breaker_open(now: Optional[float] = None) -> Optional[dict]:
    """熔断窗口内 → 返回状态 dict；否则 None（窗口过期自动放行，不重置计数）。"""
    b = read_breaker()
    until = _as_float(b.get("open_until"))
    if until and (now if now is not None else time.time()) < until:
        return b
    return None


def _cooldown_for(streak: int) -> float:
    """第 streak 次连续失败对应的冷却时长（阶梯末位封顶）。"""
    idx = min(max(streak - _BREAKER_THRESHOLD, 0), len(_BREAKER_COOLDOWN_LADDER) - 1)
    return _BREAKER_COOLDOWN_LADDER[idx]


def record_fail(reason: str) -> dict:
    """记一次失败；连续达阈值即开窗。返回落盘后的状态。

    **这是公开 API：外部时间盒调用方在超时时应当调用它**——通道级故障的形态是
    `.so` 内部挂死（stdout 打「[错误码 11] 连接失败」），异常根本回不到
    `_call_with_retry`，只有做时间盒的调用方看得见超时。超时若不上报，熔断就永远
    不开（这正是 2026-09-02 起三周静默的形状）。

    ⚠️ **当前仓库内没有这样的调用方**（`live_l2_capture._timebox` 已随 AiData
    分钟路径退役删除，见 P0-3）。所以挂死型故障目前只有进程自己卡住这一条结局，
    没有熔断保护；这不影响「失败一次就记一次」的常规路径。恢复保护需要重加时间盒
    （线程 + 超时；watchdog 只报不杀），属独立议题。
    """
    prev = read_breaker()
    streak = _as_int(prev.get("fail_streak")) + 1
    now = time.time()
    cool = _cooldown_for(streak) if streak >= _BREAKER_THRESHOLD else 0.0
    state = {
        "ok": False,
        "error": str(reason)[:200],
        "fail_streak": streak,
        # 首次失败时刻不被后续失败覆盖 = 「自 Y 时刻起连续失败」（对齐 rt_probe）
        "first_fail_ts": prev.get("first_fail_ts") or _iso(now),
        "open_until": now + cool if cool else 0,
        "open_until_cn": "",
        "last_ok_ts": prev.get("last_ok_ts") or "",
        "ts": now,
        "ts_cn": _iso(now),
    }
    state["open_until_cn"] = _iso(state["open_until"]) if state["open_until"] else ""
    _write_breaker(**state)
    return state


def record_ok() -> None:
    """记一次成功：清零、清窗口、推进 last_ok_ts。

    有失败痕迹 → 必写（恢复要立刻可见）；否则最多每 _OK_WRITE_MIN_GAP_SEC 写一次
    ——last_ok_ts 是 probe_aidata 判「从未成功过」的依据，但也犯不上每次调用都写盘。
    """
    global _last_ok_write
    prev = read_breaker()
    dirty = bool(prev.get("fail_streak") or prev.get("open_until"))
    now = time.time()
    if not dirty and now - _last_ok_write < _OK_WRITE_MIN_GAP_SEC:
        return
    _last_ok_write = now
    _write_breaker(ok=True, error="", fail_streak=0, first_fail_ts="",
                   open_until=0, open_until_cn="", last_ok_ts=_iso(now),
                   ts=now, ts_cn=_iso(now))


def _call_with_retry(fn, *args, empty_is_channel: bool = True, **kwargs) -> Any:
    """tqs 调用带退避重试；熔断窗口内直接抛错（**零网络、零 sleep**）。

    全部失败后空结果返回 None，异常则抛出（原契约不变）。

    `empty_is_channel` = **空结果是否算通道级故障**。空结果确实是限流的既定签名
    （错误码 13 是 .so 打到 stdout 的，不抛异常），但只有**批量**调用才成立：单票
    调用（`get_klines([symbol])`、`get_quote`、分时、分笔）返回空是**正常业务结果**
    ——停牌/退市/代码写错/当天无成交。把它计成通道级，等于用一次「查了个停牌股」
    把整条通道封 5 分钟，且熔断文件里写下的诊断是「空结果（限流/无数据）」：
    把**参数错**说成了限流。异常不含糊（调用本身没走通，与票无关），仍照记。

    ⚠️ 挂死型故障回不到这里（`.so` 内部卡住不返回），没有外部时间盒就无人上报。

    **已知的观测缺口（2026-09-22 审查 M-1，刻意不做）**：单票调用若**全部**返回空
    （通道级限流也可能是这个形状），这里既不计数也不告警——于是「从没被调用过」与
    「调用过、次次为空」在熔断状态文件里长得一样。要分得开就得让计数**按票去重**
    （同一个停牌股连续 10 次空是正常业务，10 只不同的票连续空才是通道），那是
    `_call_with_retry` 拿不到的信息（它只有 fn，没有标的），得改四处调用点的签名并
    新增一个告警状态。当前不做，理由有二：① 生产上 `LIVE_QUOTES_DISABLE_AIDATA=1`
    闸门开着，单票路径一次都不跑，改完**无法在真实链路上验证**——加一条测不到的
    告警路径本身就是这类事故的温床；② 单票空的直接后果（拿不到数据）调用方全都
    显式回退到桥，损失的是可观测量而不是数据。**闸门摘掉时先补这个缺口。**
    """
    reason = gate_reason()
    if reason:
        raise RuntimeError(reason)
    open_state = breaker_open()
    if open_state:
        raise RuntimeError(
            f"TdxAiData 熔断中（连续 {open_state.get('fail_streak')} 次失败，"
            f"自 {open_state.get('first_fail_ts')}，至 {open_state.get('open_until_cn') or '—'}）"
            f"：{open_state.get('error') or ''}")
    last: Optional[Exception] = None
    empty = False
    fail_reason = ""
    for gap in _retry_gaps() + [0]:
        # 每次尝试前复查：其它调用方刚开了窗 / 孤儿线程睡醒后看到闸门已关
        # —— 立刻退出，不再触网。
        if gate_reason():
            raise RuntimeError(gate_reason())
        if breaker_open():
            raise RuntimeError("TdxAiData 熔断中（重试中止）") from last
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            empty = False
            fail_reason = str(exc)
        else:
            if result:
                record_ok()
                return result
            last = None
            empty = True
            fail_reason = "空结果（限流/无数据）"
        if gap:
            time.sleep(gap)
    # **一次调用 = 一次失败计数**。旧实现把 record_fail 放在循环里（每次尝试各记一次）：
    # 退避阶梯 [5,10]+末次 = 3 次尝试 = 一次全败的调用即打满阈值，且冷却阶梯按 streak
    # 取档 ⇒ **第二次**失败的调用就把窗口推到阶梯顶（1 小时），而那是「持续多轮」才该
    # 到的档位。循环注释里「本轮第 1 次失败就已开窗」也不成立：streak 从 0 起记时前
    # 两次尝试都不开窗，短路从未发生——唯一的效果是阶梯走了 3 倍快。
    if last is not None:
        record_fail(fail_reason)
        raise RuntimeError(f"TdxAiData 调用异常: {last}") from last
    if empty and empty_is_channel:
        record_fail(fail_reason)
    return None          # 原契约：空结果不抛，返回 None 由调用方回退桥


# ---------- K线（实时，含分钟线） ----------

_PERIOD_MAP = {
    "daily": "1d", "weekly": "1w", "monthly": "1mon",
    "1m": "1m", "5m": "5m", "10m": "10m", "15m": "15m", "30m": "30m",
    "1h": "1h", "45d": "45d", "1q": "1q", "1y": "1y",
}
_BAR_MINUTES = {"1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
_DAY_MARGIN = {"1d": 2, "1w": 15, "1mon": 45, "45d": 90, "1q": 130, "1y": 520}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _start_for_count(period: str, count: int, end: str) -> str:
    """count → 区间模式 start_time（tqs 的 count 参数该 token 不可用）。
    分钟周期按 bar 时长×2 回推；日历周期按根数×日数裕量回推（含周末/节假日）。"""
    minutes = _BAR_MINUTES.get(period)
    try:
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        end_dt = datetime.now()
    if minutes:
        start_dt = end_dt - timedelta(minutes=minutes * count * 2 + 30)
    else:
        start_dt = end_dt - timedelta(days=_DAY_MARGIN.get(period, 2) * count)
    return start_dt.strftime("%Y-%m-%d %H:%M:%S")


def _val(data: dict, field: str, symbol: str, i: int) -> Any:
    """从 {field: DataFrame|{symbol: [...]}} 中取第 i 个值。"""
    f = data.get(field)
    if f is None:
        return None
    try:
        if hasattr(f, "iloc"):  # pandas DataFrame
            return f[symbol].iloc[i]
        if isinstance(f, dict):
            return f.get(symbol, [])[i]
        return f[i]
    except Exception:  # noqa: BLE001
        return None


def _dates(data: dict) -> List[str]:
    """K线日期序列（DataFrame index；无 pandas 兜底模式无日期，空串占位）。"""
    sample = next(iter(data.values())) if data else None
    if sample is None:
        return []
    if hasattr(sample, "index"):
        return [str(d.date()) if hasattr(d, "date") else str(d) for d in sample.index]
    closes = data.get("Close") or {}
    lst = list(closes.values())[0] if isinstance(closes, dict) and closes else None
    return [""] * len(lst) if lst else []


def _bars_from(data: dict, symbol: str) -> List[Dict[str, Any]]:
    """tqs.get_market_data 结果 → [{"date","open","high","low","close","volume","amount"}]
    限流/缺数时接口会返回 NaN 残缺行，close 无效的 bar 直接丢弃（上游视为无行情回退）。"""
    import math

    dates = _dates(data)
    bars = []
    for i in range(len(dates)):
        close = _val(data, "Close", symbol, i)
        try:
            c = float(close)
            valid = math.isfinite(c) and c > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            continue  # NaN/None close → 残缺行
        bars.append({
            "date": dates[i],
            "open": _val(data, "Open", symbol, i),
            "high": _val(data, "High", symbol, i),
            "low": _val(data, "Low", symbol, i),
            "close": close,
            "volume": _val(data, "Volume", symbol, i),
            "amount": _val(data, "Amount", symbol, i),
        })
    return bars


def _query_market_data(symbols: List[str], period: str,
                       start: str, end: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "stock_list": symbols,
        "period": period,
        "start_time": start,
        "end_time": end,
        "dividend_type": "front",
    }
    # 判据在这里收口（不交给调用方传）：**只有多票批量**的整包空才是通道级。
    # 单票空是停牌/退市/无成交的正常业务结果，见 _call_with_retry 的 empty_is_channel。
    data = _call_with_retry(_require().get_market_data,
                            empty_is_channel=len(symbols) > 1, **params)
    return data or {}


def get_klines(symbol: str, interval: str = "daily",
               start: str = "", end: str = "", count: int = 0) -> List[Dict[str, Any]]:
    """单只股票K线，与桥 get_klines 同返回格式
    [{"date","open","high","low","close","volume","amount"}]，按日期升序。
    count>0 时自动换算为时间区间并截取最后 count 根。"""
    period = _PERIOD_MAP.get(interval, interval)
    end = end or _now_str()
    if count and not start:
        start = _start_for_count(period, count, end)
    data = _query_market_data([symbol], period, start, end)
    bars = _bars_from(data, symbol)
    if count and len(bars) > count:
        bars = bars[-count:]
    return bars


def get_klines_batch(symbols: List[str], interval: str = "daily",
                     start: str = "", end: str = "", count: int = 0) -> Dict[str, List[Dict[str, Any]]]:
    """多只股票K线，一次 tqs 调用（省 token 配额），返回 {symbol: bars}。"""
    symbols = [s for s in symbols if s]
    if not symbols:
        return {}
    period = _PERIOD_MAP.get(interval, interval)
    end = end or _now_str()
    if count and not start:
        start = _start_for_count(period, count, end)
    data = _query_market_data(symbols, period, start, end)
    out = {}
    for sym in symbols:
        bars = _bars_from(data, sym)
        if count and len(bars) > count:
            bars = bars[-count:]
        out[sym] = bars
    return out


def get_quote(symbol: str) -> Dict[str, Any]:
    """实时快照（get_market_snapshot，最新价/涨跌幅）。**单票**：空结果不算通道故障。"""
    return _call_with_retry(_require().get_market_snapshot,
                            empty_is_channel=False, stock_code=symbol)


# ---------- 分时 / 分笔 ----------

def get_minute_data(symbol: str, date: str) -> Dict[str, Any]:
    """指定日期分时数据 {Time, Price, Average, Volume, TotalNum}。

    tqs 类未封装 get_minute_data（仅底层原生 _tdx() 有），直接透传；
    参数名是 codestr/date（位置传参，避免命名不匹配）。**单票**：空不算通道故障。
    """
    return _call_with_retry(_require()._tdx().get_minute_data,
                            symbol, date, empty_is_channel=False)


def get_tick_data(symbol: str, date: str, startxh: int = 0,
                  wantnum: int = 100) -> Dict[str, Any]:
    """分笔成交 {BSFlag, Price, Time, Volume, TotalNum}。**单票**：空不算通道故障。"""
    return _call_with_retry(_require().get_tick_data,
                            empty_is_channel=False,
                            stock_code=symbol, date=date, startxh=startxh, wantnum=wantnum)


# ---------- 实时订阅 ----------

def subscribe(symbols: List[str]) -> Any:
    """订阅实时行情（返回回调句柄/结果，具体形态以 tqServer 实现为准）。"""
    return _require().subscribe(stock_list=symbols)


def unsubscribe(symbols: List[str]) -> Any:
    return _require().unsubscribe(stock_list=symbols)


def _test() -> None:
    """自检：可用性 + 日K/5分钟线 + 批量 + 实时快照。"""
    if not available():
        print(f"✗ {_load_error}")
        return
    print("✓ TdxAiData 可用")
    bars = get_klines("600519.SH", interval="daily", count=5)
    print(f"  日K {len(bars)} 根, 最新: {bars[-1] if bars else None}")
    m5 = get_klines("600519.SH", interval="5m", count=3)
    print(f"  5m {len(m5)} 根, 最新: {m5[-1] if m5 else None}")
    batch = get_klines_batch(["600519.SH", "000858.SZ"], interval="daily", count=3)
    for sym, b in batch.items():
        print(f"  批量 {sym}: {len(b)} 根, 最新收盘 {b[-1]['close'] if b else None}")
    try:
        snap = get_quote("600519.SH")
        keys = list(snap.keys())[:8] if isinstance(snap, dict) else type(snap).__name__
        print(f"  快照 keys: {keys}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 快照失败: {e}")


if __name__ == "__main__":
    _test()
