#!/usr/bin/env python3
"""取价备胎的唯一入口：Fuyao 快照 → 腾讯行情（桥为主源，此处只做兜底/交叉校验）。

2026-09-11 实录（本模块存在的理由）：Fuyao `/api/a-share/prices/snapshot`
**忽略 thscode 参数**、返回全市场 5571 条（`data.item[0]` 恒为 000001.SZ 平安银行）。
旧实现在 `live_hourly_analysis.quote_fallback` 与 `rt_probe.probe_fuyao` 里各写了一份
`items[0]` 取价 / `len(items)>0` 探活 → 桥瞬时不可用时：

- 喂给 LLM 的备胎价一律 11.x（平安银行），
- 波动触发的「桥价 vs 备胎 ±2%」交叉校验把**真触发**整行丢掉（当日丢 156 条），
- 探活永远 OK（只要接口有返回），上游坏掉也看不出来。

本模块的硬约束：
1. **一律客户端按代码匹配**（`thscode`/6 位码，后缀不同不串行），匹配不到返回
   None —— 绝不退化取 `items[0]`；不臆造、不兜底、不猜。
2. 每个源取到的价先用 40% 口径（`live_ledger.sane_fill_price`，覆盖全市场最大
   涨跌停 ±30% 留余量）与**参照价**（源自带昨收 / 调用方给的桥价）交叉比对，
   离谱值丢弃并留痕（事件 `quote_bad`，alert=False：源质量问题，不必盘中打扰人）。
3. 返回值带**来源字符串**（"fuyao"/"tencent"），调用方据此在日志/提示词里标注
   口径，避免"不知道这个价哪来的"。
"""
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from live_ledger import sane_fill_price  # noqa: E402

FUYAO_SCRIPTS = ROOT / "dsh/skills/ths-fuyao/scripts"
FUYAO_SNAPSHOT_PATH = "/api/a-share/prices/snapshot"
TENCENT_URL = "http://qt.gtimg.cn/q={symbol}"
TENCENT_TIMEOUT_S = 4

_DIGITS = re.compile(r"\d{6}")


# ---------- 归一化 / 匹配 ----------

def _digits(code: str) -> str:
    m = _DIGITS.search(str(code or ""))
    return m.group(0) if m else ""


def _suffix(code: str) -> str:
    c = str(code or "").strip().upper()
    return c.rsplit(".", 1)[1] if "." in c else ""


def _row_code_str(row: dict) -> str:
    """行内代码原文：不同端点字段名不一（thscode/ticker/code/stock_code/symbol）。"""
    for key in ("thscode", "ticker", "code", "stock_code", "symbol"):
        v = str(row.get(key) or "").strip()
        if v:
            return v
    return ""


def _row_matches(row: dict, code: str) -> bool:
    """按 6 位码匹配；两侧都有市场后缀时后缀必须一致（.SS 与 .SH 视为同一市场）。"""
    target = _digits(code)
    src = _row_code_str(row)
    if not target or _digits(src) != target:
        return False
    rs, cs = _suffix(src), _suffix(code)
    if not rs or not cs:
        return True                      # 任一侧没后缀 → 只能按 6 位码认
    norm = {"SS": "SH"}
    return norm.get(rs, rs) == norm.get(cs, cs)


def tencent_symbol(code: str) -> str:
    """code → 腾讯 qt 代码。无后缀时按首位推断：6→沪，4/8/920→北交所，其余→深。
    （旧实现 `.SH/.SS` 之外一律 sz → 北交所 430xxx/83xxxx 取错源。）"""
    c = str(code or "").strip().upper()
    digits = _digits(c)
    if not digits:
        return ""
    suffix = _suffix(c)
    if suffix in ("SH", "SS"):
        market = "sh"
    elif suffix == "BJ":
        market = "bj"
    elif suffix == "SZ":
        market = "sz"
    elif digits[0] == "6":
        market = "sh"
    elif digits[0] in ("4", "8") or digits.startswith("920"):
        market = "bj"
    else:
        market = "sz"
    return market + digits


# ---------- 坏价闸 ----------

def sane_quote(px, ref) -> float | None:
    """坏 tick 闸：非法值或与参照价偏离 >40% → None（不臆造价格）。
    ref 缺失/<=0 时只做合法性判断。口径与 live_ledger.sane_fill_price 同源。"""
    try:
        v = float(px)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        r = float(ref) if ref is not None else 0.0
    except (TypeError, ValueError):
        r = 0.0
    if r > 0:
        _fixed, approx = sane_fill_price(v, r)
        if approx:
            return None
    return v


def _record_discard(code: str, source: str, px: float, ref: float) -> None:
    """坏价丢弃留痕（事件台账，alert=False：备胎源质量问题，不必盘中打扰人）。"""
    pct = (px / ref - 1) * 100 if ref else 0.0
    try:
        import live_fills

        live_fills.record_event(
            "quote_bad", code,
            f"{source} 备胎价 {px} 偏离参照 {ref} {pct:+.1f}%（超 40% 闸）已丢弃",
            alert=False)
    except Exception:  # noqa: BLE001 留痕失败不能反过来打断取价路径
        pass


def _checked(px, ref, code: str, source: str) -> float | None:
    """sane_quote + 越界留痕。空值/非数 = 源没数据，不留痕（防噪声）。"""
    v = sane_quote(px, ref)
    if v is not None:
        return v
    try:
        f = float(px)
    except (TypeError, ValueError):
        return None
    try:
        r = float(ref) if ref is not None else 0.0
    except (TypeError, ValueError):
        r = 0.0
    if f > 0 and r > 0 and not (0.6 <= f / r <= 1.4):
        _record_discard(code, source, f, r)
    return None


# ---------- 源：Fuyao ----------

def _fuyao_get(path: str, params: dict) -> dict:
    """Fuyao REST 取数（测试替换点）。Key 解析/脱敏复用 ths-fuyao skill 脚本。"""
    p = str(FUYAO_SCRIPTS)
    if p not in sys.path:
        sys.path.insert(0, p)
    from ths_fuyao import get

    return get(path, params)


def _fuyao_items(d: dict) -> list:
    items = (d.get("data") or {}).get("item") or []
    return [r for r in items if isinstance(r, dict)]


def _source_price(px, own_ref, ref, code: str, source: str) -> float | None:
    """源价两级坏价闸：先与源自带参照（昨收）比，再与调用方参照价（桥价）比。"""
    v = _checked(px, own_ref, code, source)
    if v is None or ref is None:
        return v
    return _checked(v, ref, code, source)


def _fuyao_match_price(items: list, code: str, ref=None) -> float | None:
    """目标行 → 价（先与本行昨收比对，再与调用方参照价比对），匹配不到 None。"""
    for row in items:
        if not _row_matches(row, code):
            continue
        return _source_price(row.get("last_price"), row.get("prev_price"),
                             ref, code, "fuyao")
    return None


def fuyao_snapshot(code: str, ref=None) -> float | None:
    """Fuyao 快照价。匹配不到目标行 → None（绝不取 items[0]）；异常 → None。"""
    try:
        d = _fuyao_get(FUYAO_SNAPSHOT_PATH, {"thscode": code})
    except Exception:  # noqa: BLE001 备胎失败按无价处理，调用方继续降级
        return None
    if not isinstance(d, dict) or d.get("code") != 0:
        return None
    return _fuyao_match_price(_fuyao_items(d), code, ref)


def fuyao_check(code: str = "600519.SH") -> dict:
    """探活（rt_probe 用）：目标行真的匹配到有效价才算 OK。

    旧判据 `len(items) > 0` 只证明"接口有返回"，上游忽略参数/返回别家行情也判健康。
    条数降级为 count_hint 提示，不作为健康判据。
    """
    try:
        d = _fuyao_get(FUYAO_SNAPSHOT_PATH, {"thscode": code})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}
    if not isinstance(d, dict):
        return {"ok": False, "error": "响应不是 JSON 对象"}
    items = _fuyao_items(d)
    if d.get("code") != 0:
        return {"ok": False, "count_hint": len(items),
                "error": str(d.get("message") or f"code={d.get('code')}")[:120]}
    px = _fuyao_match_price(items, code)
    if px is None:
        return {"ok": False, "count_hint": len(items),
                "error": f"目标 {code} 未在快照结果中匹配到有效价"}
    return {"ok": True, "count_hint": len(items), "now": px, "error": None}


# ---------- 源：腾讯 ----------

def _tencent_raw(symbol: str) -> str:
    """腾讯行情原文（测试替换点）。GBK 编码、`v_xx="..."` 格式。"""
    if not symbol:
        return ""
    with urllib.request.urlopen(TENCENT_URL.format(symbol=symbol),
                                timeout=TENCENT_TIMEOUT_S) as r:
        return r.read().decode("gbk", "ignore")


def tencent_quote(code: str, ref=None) -> float | None:
    """腾讯行情价（f[3]），与本行昨收 f[4] / 调用方参照价比对后返回；失败 None。"""
    try:
        raw = _tencent_raw(tencent_symbol(code))
        fields = raw.split("=", 1)[1].split("~") if "=" in raw else []
        if len(fields) < 4:
            return None
        prev = fields[4] if len(fields) > 4 else None
        return _source_price(fields[3], prev, ref, code, "tencent")
    except Exception:  # noqa: BLE001
        return None


# ---------- 统一入口 ----------

def fallback_quote(code: str, ref=None) -> tuple[float | None, str]:
    """备胎取价：Fuyao → 腾讯。返回 (价, 来源)，全失败 (None, "")。

    ref（可选）= 调用方手里的参照价（如桥价）：源价与它偏离 >40% 视为坏值丢弃。
    **不接受"取不到就随便给个数"**：无价时调用方必须走各自的降级分支。
    """
    for source, fetch in (("fuyao", fuyao_snapshot), ("tencent", tencent_quote)):
        px = fetch(code, ref)
        if px is not None:
            return px, source
    return None, ""
