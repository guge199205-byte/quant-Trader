#!/usr/bin/env python3
"""把 .pine 策略转写成 Pyne-Python 模板，并可验证 / 回测 / 装进策略注册表。

背景：本机没有任何 Pine 解释器，PyneCore 的 `pyne compile` 是付费远程 API（已决定不订阅），
所以「让 .pine 跑起来」的唯一路径是让模型把它翻成 Pyne-Python（lab_strategies 那套格式），
再用现有 PyneCore 链路回测。转写产物一律先落到 data/pine_transpile/<id>/，**不直接执行**：

  candidate.py   模型产出的脚本（未验证）
  prompt.md      完整提示词（审计用：模型看到了什么）
  meta.json      模型/耗时/token/校验结果
  report.json    --validate 跑出来的回测统计

校验与执行分开：先静态查（语法 / 危险调用），再 --validate 用真实行情跑一遍。

用法::

    .venv/bin/python scripts/pine_to_pyne.py --id 0001                 # 转写
    .venv/bin/python scripts/pine_to_pyne.py --id 0001 --validate 600309.SH
    .venv/bin/python scripts/pine_to_pyne.py --id 0001 --install       # 装进 lab_strategies
    .venv/bin/python scripts/pine_to_pyne.py --list                    # 看已有产物
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# pynecore 只装在 pine_lab/.venv（主 .venv 没有）→ 回测前把它挂进 sys.path，
# 这样本脚本用主 .venv 就能跑（否则得切解释器，容易忘）。
_PINE_SITE = ROOT / "pine_lab/.venv/lib/python3.14/site-packages"
if _PINE_SITE.is_dir() and str(_PINE_SITE) not in sys.path:
    sys.path.append(str(_PINE_SITE))

OUT_DIR = Path(__file__).resolve().parents[1] / "data/pine_transpile"
LAB_DIR = ROOT / "backend/services/lab_strategies"
MODEL = "deepseek-v4-flash"
# 推理模型的 reasoning token 也计入 completion：0001 开思考烧掉 23755 token / 180s 才
# 勉强塞进 32k，0004 同样 32k 直接截断（策略还更短）。转写是照模板的机械活，不需要长
# 思考——默认 thinking=disabled，输出只剩代码，8k 富余。真截断了再回退开思考重试。
MAX_TOKENS = 32000          # 开思考时的上限
MAX_TOKENS_FAST = 8000      # 关思考时只出代码
NO_THINKING = {"thinking": {"type": "disabled"}}

# 危险调用黑名单：转写产物是要被执行的，先在静态阶段挡一道。
BANNED = [
    (r"\b(import|from)\s+(os|sys|subprocess|socket|shutil|requests|urllib|http|ctypes|multiprocessing)\b",
     "禁止导入标准库/网络模块"),
    (r"\b(eval|exec|compile|__import__|open)\s*\(", "禁止动态执行/文件访问"),
    (r"\b(getattr|setattr|globals|locals|vars|breakpoint)\s*\(", "禁止反射"),
]
REQUIRED = ("@pyne", "from pynecore.lib import", "def main(")

SYSTEM_PROMPT = """你是 Pine Script → Pyne-Python 的转写器。把用户给的 Pine 策略改写成下面这种 Pyne-Python 脚本。

## 目标格式（严格照抄结构）

```python
\"\"\"
@pyne

<中文一句话说明策略逻辑>
\"\"\"
from pynecore.lib import close, input, script, strategy, ta
from pynecore.types import Series


@script.strategy("<中文策略名>", overlay=True, pyramiding=0,
                 default_qty_type=strategy.percent_of_equity, default_qty_value=95,
                 initial_capital=100000,
                 commission_type=strategy.commission.percent, commission_value=0.05,
                 slippage=1)
def main(fast=input(5, "快线周期"), slow=input(20, "慢线周期")):
    f = ta.sma(close, fast)
    s = ta.sma(close, slow)
    up: Series[bool] = f > s
    if up and not up[1]:
        strategy.entry('L', strategy.long)
    elif not up and up[1]:
        strategy.close('L')
```

## 硬性规则

1. 只做多（A股口径）：只用 `strategy.entry('L', strategy.long)` / `strategy.close('L')`，
   **不要做空**；原策略的做空腿直接丢掉，并在注释里说明。
2. 所有可调参数走 `input(默认值, "中文名")` 写在 `main()` 签名里，不要写死数字。
3. 只能用 `pynecore.lib` 与 `pynecore.types`（Series）；禁止 import 任何其它模块。
4. 禁止画图 / 告警 / 表格 / 标签（plot、alert、table、label、line、box 一律不用）。
5. 禁止 `request.security`（多时间框架）、`request.*` 数据请求——取不到就退化成单周期逻辑。
6. 序列变量必须显式标注类型，例如 `up: Series[bool] = f > s`；上一根用 `x[1]`。
   **凡是要用 `[n]` 取历史的值（包括 `ta.*` 的返回值）都必须先赋给带 `Series[...]`
   标注的变量**（`r: Series[float] = ta.rsi(close, n)` 才能写 `r[3]`），
   否则运行时只是普通 float，`[n]` 会抛 TypeError。**不能对表达式直接取历史**
   （`(high + low)[1]` 会抛 TypeError），先赋值给变量再索引。
   `ta.macd` / `ta.bb` / `ta.dmi` / `ta.kc` / `ta.supertrend` 返回**元组**，必须解包：
   `macd_line, signal_line, hist = ta.macd(close, f, s, sig)`。
7. 不加未来函数：判断与开平仓只用当前及历史 bar。
8. 保持策略原意，宁可简化也不要臆造新逻辑；简化处在文件头注释里写清楚。
9. 输出必须是**纯 Python**，Pine 写法一律翻译掉——这是最常见的翻车点（会直接语法错误）：
   - 三元 `cond ? a : b` → `a if cond else b`
   - 不要 `var` 关键字（直接赋值；要保留状态的用带 `Series[...]` 标注的变量）
   - 不要 `:=` 赋值（用 `=`）
   - 缺失值用 `float('nan')`；取值用 `nz(x, 默认值)`；判断用 `na(x)`（`na` 是函数，不是值）
10. 用到的内置名必须 import 齐（第一行 `from pynecore.lib import ...`）——漏一个就是运行时
   NameError，这是实测最高频的失败。注意 `bar_index` / `time` / `na` / `nz` 是**顶层名字**，
   不在 `ta.` 下（`ta.bar_index` 会报 AttributeError）；`pynecore.lib.math` 里没有
   `isnan` / `nan`，判断缺失用 `na(x)`、字面缺失值写 `float('nan')`。
11. 装饰器参数一律用模板里的 `initial_capital=100000` / `default_qty_value=95`，不要沿用原策略
   的小本金（1000 本金 × 1% 仓位在 A 股算出不足 1 股，整场 0 笔成交，等于白跑）。
12. 输出**只有一个 python 代码块**，不要解释文字。

## 常用库

- 均线/通道：`ta.sma(close, n)` `ta.ema(close, n)` `ta.rma` `ta.wma` `ta.highest(high, n)` `ta.lowest(low, n)`
- 波动/动量：`ta.atr(n)` `ta.rsi(close, n)` `ta.macd(close, f, s, sig)` `ta.stoch(close, high, low, n)`
- 交叉：`ta.crossover(a, b)` `ta.crossunder(a, b)`
- 其它：`ta.change(x)` `ta.stdev(close, n)` `math.abs/max/min/round`（`from pynecore.lib import math`）
- 缺失值：`nz(x, 默认值)` 取值、`na(x)` 判断（`na` 是函数）；字面缺失值写 `float('nan')`
- 开平仓：`strategy.entry('L', strategy.long)` `strategy.close('L')` `strategy.position_size`
- 常量：`strategy.long` `strategy.percent_of_equity` `strategy.commission.percent`
"""


def _env() -> dict:
    env: dict[str, str] = {}
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in env:
                env[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return env


def _chat(messages: list[dict], model: str,
          think: bool = False) -> tuple[str, dict | None, bool]:
    """直连 OpenAI 兼容接口（重试 1 次）。返回 (文本, usage, 是否被截断)。

    ``think=False``（默认）关掉推理：转写产物是照模板的代码，长思考只会把额度烧光。
    """
    env = _env()
    base = env.get("OPENAI_API_BASE", "").rstrip("/")
    key = env.get("OPENAI_API_KEY", "")
    if not base or not key:
        raise RuntimeError("OPENAI_API_BASE/OPENAI_API_KEY 缺失（.env）")
    import requests

    payload = {"model": model, "messages": messages,
               "temperature": 0.1,
               "max_tokens": MAX_TOKENS if think else MAX_TOKENS_FAST}
    if not think:
        payload.update(NO_THINKING)
    last: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.post(f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 json=payload, timeout=600)
            resp.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt == 0:
                print(f"  ⚠️ 调用失败，重试: {exc}")
    else:
        raise last  # type: ignore[misc]
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = str(msg.get("content") or "").strip() or str(msg.get("reasoning_content") or "").strip()
    usage = data.get("usage") or None
    if usage:
        usage = {k: int(usage.get(k) or 0)
                 for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return content, usage, choice.get("finish_reason") == "length"


def call_llm(system: str, user: str, model: str,
             think: bool = False) -> tuple[str, dict | None, bool]:
    """单轮 system+user 调用。"""
    return _chat([{"role": "system", "content": system},
                  {"role": "user", "content": user}], model, think=think)


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip() + "\n"


def _strip_strings_and_comments(code: str) -> str:
    """把字符串与注释抹成空格（保留换行与列位），供「只看代码」的检查用。

    位置一一对应是刻意的：改写函数要靠它把「干净文本里的下标」映射回原文。
    """
    out: list[str] = []
    i, n = 0, len(code)
    quote = ""
    while i < n:
        ch = code[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                out.append("  ")
                i += 2
            elif code.startswith(quote, i):
                out.append(" " * len(quote))
                i += len(quote)
                quote = ""
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue
        if ch in "\"'":
            quote = ch * 3 if code.startswith(ch * 3, i) else ch
            out.append(" " * len(quote))
            i += len(quote)
            continue
        if ch == "#":
            while i < n and code[i] != "\n":
                out.append(" ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_top_level(s: str, target: str, start: int = 0) -> int:
    """在括号/方括号/花括号之外找 target，找不到返回 -1。"""
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == target:
            return i
    return -1


def _ternary_to_if(expr: str) -> str:
    """`cond ? a : b` → `a if cond else b`（递归处理嵌套，Pine 的 ?: 是右结合）。"""
    q = _find_top_level(expr, "?")
    if q < 0:
        s = expr.strip()
        if s.startswith("(") and s.endswith(")"):      # 括号里的嵌套三元
            inner = _ternary_to_if(s[1:-1])
            if inner != s[1:-1]:
                return f"({inner})"
        return expr
    c = _find_top_level(expr, ":", q + 1)
    if c < 0:
        return expr
    cond, a, b = expr[:q].strip(), expr[q + 1:c].strip(), expr[c + 1:].strip()
    return f"({_ternary_to_if(a)}) if ({_ternary_to_if(cond)}) else ({_ternary_to_if(b)})"


def _split_comment(orig: str, clean: str) -> tuple[str, str]:
    """按「干净文本里该位置起全是空白」认出真注释，字符串里的 # 不算。"""
    for i, ch in enumerate(orig):
        if ch == "#" and clean[i:].strip() == "":
            return orig[:i], orig[i:]
    return orig, ""


def _split_prefix(clean_line: str) -> int:
    """赋值 / return 语句的右值起点（行里没有这类前缀时返回 -1）。"""
    m = re.match(r"^\s*return\s+", clean_line)
    if m:
        return m.end()
    m = re.match(r"^\s*[A-Za-z_]\w*\s*(?::\s*[^=\n]+?)?\s*=\s*", clean_line)
    return m.end() if m else -1


def _fix_ternaries(code: str) -> tuple[str, int]:
    """逐行把右值里的 `? :` 翻成 Python 条件表达式。"""
    clean = _strip_strings_and_comments(code)
    out: list[str] = []
    fixed = 0
    for orig, cl in zip(code.splitlines(), clean.splitlines()):
        idx = _split_prefix(cl) if "?" in cl else -1
        if idx < 0:
            out.append(orig)
            continue
        q = _find_top_level(cl, "?", idx)
        if q < 0 or _find_top_level(cl, ":", q + 1) < 0:
            out.append(orig)
            continue
        body, comment = _split_comment(orig[idx:], cl[idx:])
        stripped = body.rstrip()
        pad = body[len(stripped):]          # 注释前的空格要留着，不然注释会贴到代码上
        new_body = _ternary_to_if(stripped)
        if new_body == stripped:
            out.append(orig)
            continue
        out.append(orig[:idx] + new_body + pad + comment)
        fixed += 1
    text = "\n".join(out)
    return text + ("\n" if code.endswith("\n") else ""), fixed


def _fix_walrus_assign(code: str) -> tuple[str, int]:
    """Pine 的 `:=` 赋值 → `=`（只改代码区，字符串/注释里的不动）。"""
    clean = _strip_strings_and_comments(code)
    pat = re.compile(r"^(\s*)([A-Za-z_]\w*)\s*:=\s*", re.M)
    out: list[str] = []
    last = fixed = 0
    for m in pat.finditer(code):
        if clean[m.start():m.end()] != code[m.start():m.end()]:
            continue
        out.append(code[last:m.start()])
        out.append(f"{m.group(1)}{m.group(2)} = ")
        last = m.end()
        fixed += 1
    out.append(code[last:])
    return "".join(out), fixed


def depine(code: str) -> tuple[str, list[str]]:
    """机械翻掉最顽固的两处 Pine 残留：`? :` 三元与 `:=`。

    模型连着修两轮仍会漏（0400/0778 就是这么挂的），而这两处改写纯语法、无歧义。
    改写记录进 meta.auto_fixes；静态闸照旧跑，改不干净的仍会被挡下。
    """
    notes: list[str] = []
    code, n = _fix_walrus_assign(code)
    if n:
        notes.append(f"自动改写 {n} 处 `:=` → `=`")
    code, n = _fix_ternaries(code)
    if n:
        notes.append(f"自动改写 {n} 处 `? :` → `if/else`")
    return code, notes


# pynecore.lib 里必须显式导入的名字——漏一个就是运行时 NameError（实测最高频的翻车点）。
_LIB_NAMES = (
    "close", "open", "high", "low", "volume", "hl2", "hlc3", "ohlc4",
    "na", "nz", "fixnan", "bar_index", "last_bar_index", "time", "time_close",
    "time_tradingday", "timestamp", "hour", "minute", "second",
    "dayofweek", "dayofmonth", "weekofyear", "month", "year", "math", "ta", "array", "matrix",
    "map", "strategy", "input", "script", "barstate", "timeframe", "syminfo", "session",
    "request", "color", "alert", "label", "line", "box", "table",
)
_DEF_RE = re.compile(r"^\s*def\s+(\w+)\s*\(([^)]*)\)", re.M)
_TARGET_RE = re.compile(r"^\s*(\w+)\s*(?::[^=\n]+)?=", re.M)
_IMPORT_RE = re.compile(r"from\s+pynecore\.lib\s+import\s+([^\n]+)")
# 这些 ta 函数在 pynecore 里返回元组，赋给单个变量后下标会拿到「另一个指标」而不是历史值。
_MULTI_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::\s*[^=\n]+?)?\s*=\s*"
                       r"ta\.(bb|dmi|kc|macd|supertrend)\s*\(", re.M)
_EXPR_INDEX_RE = re.compile(r"\)\s*\[\s*\d+\s*\]")


def _imported_names(code: str) -> set[str]:
    names: set[str] = set()
    for m in _IMPORT_RE.finditer(code):
        for part in m.group(1).split(","):
            name = part.strip().split(" as ")[0].strip()
            if name:
                names.add(name)
    return names


def _defined_names(code: str) -> set[str]:
    names = {m[0] for m in _DEF_RE.findall(code)}
    for _, params in _DEF_RE.findall(code):
        for part in params.split(","):
            name = part.strip().split(":")[0].split("=")[0].strip()
            if name and name.isidentifier():
                names.add(name)
    names.update(_TARGET_RE.findall(code))
    return names


def _import_problems(code: str) -> list[str]:
    imported = _imported_names(code)
    defined = _defined_names(code)
    clean = _strip_strings_and_comments(code)
    out: list[str] = []
    for name in _LIB_NAMES:
        if name in imported or name in defined:
            continue
        if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", clean):
            out.append(f"用了 {name} 但没从 pynecore.lib 导入")
    return out


def _namespace_problems(code: str) -> list[str]:
    clean = _strip_strings_and_comments(code)
    out: list[str] = []
    if re.search(r"\bta\.bar_index\b", clean):
        out.append("bar_index 不在 ta 下：从 pynecore.lib 导入后用裸名 bar_index")
    if re.search(r"\bmath\.(isnan|is_nan|nan|inf)\b", clean):
        out.append("pynecore.lib.math 没有 isnan/nan：判断缺失用 na(x)，字面缺失值写 float('nan')")
    for m in _MULTI_RE.finditer(clean):
        out.append(f"ta.{m.group(2)}() 返回元组，不能赋给单个变量：解包成多个变量"
                   f"（如 `a, b, c = ta.{m.group(2)}(...)`）再取历史")
    if _EXPR_INDEX_RE.search(clean):
        out.append("对表达式直接取历史（如 `(high + low)[1]`）：先赋给带 Series[...] 标注的变量再索引")
    return out


def _sizing_problems(code: str) -> list[str]:
    """本金×仓位太小 → A股算出不足 1 股 → 整场 0 笔成交，看起来像「策略不灵」其实是白跑。"""
    clean = _strip_strings_and_comments(code)
    cap = re.search(r"initial_capital\s*=\s*([0-9_]+)", clean)
    qty = re.search(r"default_qty_value\s*=\s*([0-9.]+)", clean)
    typ = re.search(r"default_qty_type\s*=\s*strategy\.(\w+)", clean)
    if not (cap and qty and typ) or typ.group(1) != "percent_of_equity":
        return []
    amount = int(cap.group(1).replace("_", "")) * float(qty.group(1)) / 100
    if amount >= 1000:
        return []
    return [f"仓位金额过小（本金 {cap.group(1)} × {qty.group(1)}% ≈ {amount:.0f} 元）："
            f"A股按手成交会算出不足 1 股、整场 0 笔成交，"
            f"统一改成 initial_capital=100000 / default_qty_value=95"]


def static_check(code: str) -> list[str]:
    """语法 + 危险调用 + 导入/命名空间静态检查。返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    for pat, why in BANNED:
        hit = re.search(pat, code)
        if hit:
            problems.append(f"{why}：{hit.group(0).strip()}")
    for need in REQUIRED:
        if need not in code:
            problems.append(f"缺少必需结构：{need}")
    problems += _namespace_problems(code)
    problems += _import_problems(code)
    problems += _sizing_problems(code)
    try:
        compile(code, "<candidate>", "exec")
    except SyntaxError as exc:
        problems.append(f"语法错误：{exc.msg}（第 {exc.lineno} 行）")
    return problems


def _load_pine(item_id: str) -> dict:
    from backend.services import pine_library as pl

    return pl.get_source(item_id)


def _pine_prompt(got: dict) -> str:
    return (f"策略标题：{got.get('title') or got.get('file')}\n"
            f"分类：{got.get('category')}\n"
            f"Pine 版本：v{got.get('version') or '?'}\n\n"
            f"```pine\n{got['source']}\n```")


REPAIR_PROMPT = """你上一次的转写没通过静态检查 / 回测。下面列出的问题**必须逐条真的修掉**，
输出完整脚本（一个 python 代码块，不要解释），其它逻辑保持原样。

## 必须修掉的问题

```
{error}
```

## 当前脚本

```python
{code}
```

## 修法（照这些例子改，别只改注释）

- 取历史值：`ta.atr(n)[1]`、`(high + low)[1]` 都会抛 TypeError。先赋值再索引：
  `atr_val: Series[float] = ta.atr(n)`，然后写 `atr_val[1]`。
- 元组返回：`m = ta.macd(close, 12, 26, 9)` 拿到的是元组，下标取到的是别的指标；
  改成 `macd_line, signal_line, hist = ta.macd(close, 12, 26, 9)`。
- 缺 import：用到的名字（na / nz / bar_index / time / timeframe / timestamp …）都要写进
  `from pynecore.lib import ...`，用不到的就从 import 里删掉。`ta.bar_index` 不存在，
  用 `from pynecore.lib import bar_index` 后写裸名 `bar_index`。
- 缺失值：`math.isnan` / `math.nan` 不存在——判断用 `na(x)`，字面值写 `float('nan')`。
- Pine 语法残留：`cond ? a : b` → `a if cond else b`；`x := v` → `x = v`；删掉 `var`。
- 仓位：装饰器统一 `initial_capital=100000` / `default_qty_value=95`（percent_of_equity）。
- 列表长度：`[False] * n` 里的 n 必须是 int，写成 `int(n_levels)`。
"""


def transpile(item_id: str, model: str = MODEL) -> dict:
    got = _load_pine(item_id)
    user = _pine_prompt(got)
    out = OUT_DIR / item_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.md").write_text(
        f"# system\n\n{SYSTEM_PROMPT}\n\n# user\n\n{user}\n", encoding="utf-8")

    t0 = time.time()
    think_used = False
    raw, usage, truncated = call_llm(SYSTEM_PROMPT, user, model, think=False)
    if truncated:
        # 关思考还截断（罕见）→ 开思考重来一次：慢得多但保真度更高
        print("  ⚠️ 关思考输出被截断，改用思考模式重试")
        think_used = True
        raw, usage, truncated = call_llm(SYSTEM_PROMPT, user, model, think=True)
    if truncated:
        raise RuntimeError("模型输出被 max_tokens 截断（关思考与开思考都试过了）")
    code = extract_code(raw)
    code, fixes = depine(code)
    (out / "candidate.py").write_text(code, encoding="utf-8")
    problems = static_check(code)
    meta = {"id": item_id, "title": got.get("title"), "category": got.get("category"),
            "pine_version": got.get("version"), "pine_indent_ok": got.get("indent_ok"),
            "model": model, "think": think_used,
            "elapsed_sec": round(time.time() - t0, 1), "usage": usage,
            "auto_fixes": fixes, "problems": problems,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return meta


def repair(item_id: str, error: str, model: str = MODEL) -> dict:
    """把回测报错喂回模型，改一版候选（只写文件，不执行——执行归沙箱）。"""
    out = OUT_DIR / item_id
    cand = out / "candidate.py"
    meta_path = out / "meta.json"
    if not cand.exists() or not meta_path.exists():
        raise RuntimeError(f"没有候选可修：{cand}（先转写一次）")
    code = cand.read_text(encoding="utf-8")
    user = _pine_prompt(_load_pine(item_id))
    t0 = time.time()
    raw, usage, truncated = _chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": user},
         {"role": "user", "content": REPAIR_PROMPT.format(error=error[-2000:], code=code)}],
        model, think=False)
    if truncated:
        raise RuntimeError("修复输出被 max_tokens 截断")
    new_code = extract_code(raw)
    new_code, fixes = depine(new_code)
    cand.write_text(new_code, encoding="utf-8")
    problems = static_check(new_code)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["problems"] = problems
    meta.setdefault("repairs", []).append({
        "error": error[-500:], "usage": usage, "problems": problems,
        "auto_fixes": fixes,
        "elapsed_sec": round(time.time() - t0, 1),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def validate(item_id: str, symbol: str, adj: str = "backward") -> dict:
    from backend.services import market_lab as ml

    out = OUT_DIR / item_id
    cand = out / "candidate.py"
    if not cand.exists():
        raise SystemExit(f"没有候选文件：{cand}（先跑 --id {item_id}）")
    t0 = time.time()
    res = ml.run_script_file(cand, symbol, adj=adj)
    stats = res.get("stats") or {}
    trades = res.get("trades") or []
    report = {"id": item_id, "symbol": symbol, "adj": adj,
              "elapsed_sec": round(time.time() - t0, 1),
              "stats": stats, "trades": len(trades),
              # 逐笔与净值一并落盘：界面要画净值曲线 / 列成交，重跑一次回测没必要。
              "trade_rows": trades, "equity": res.get("equity") or [],
              "meta": res.get("meta"),
              "validated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return report


def install(item_id: str) -> dict:
    """把候选装进 lab_strategies/（复制文件 + 打印注册表条目）。"""
    out = OUT_DIR / item_id
    cand = out / "candidate.py"
    if not cand.exists():
        raise SystemExit(f"没有候选文件：{cand}")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8")) if (out / "meta.json").exists() else {}
    if meta.get("problems"):
        raise SystemExit(f"静态检查未通过，先修：{meta['problems']}")
    slug = re.sub(r"[^a-z0-9_]", "", f"pine_{item_id}_{(meta.get('title') or '')}".lower())[:40]
    dst = LAB_DIR / f"{slug}.py"
    dst.write_text(cand.read_text(encoding="utf-8"), encoding="utf-8")
    name = meta.get("title") or item_id
    snippet = (f'    "{slug}": {{\n'
               f'        "file": "{slug}.py", "name": "{name}",\n'
               f'        "desc": "由 .pine 转写（{item_id}），参数未逐一核对。",\n'
               f'        "params": [],\n'
               f'    }},')
    return {"installed": str(dst), "registry_entry": snippet}


def _list() -> int:
    if not OUT_DIR.exists():
        print("还没有转写产物")
        return 0
    for d in sorted(OUT_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = {}
        if (d / "meta.json").exists():
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        rep = (d / "report.json").exists()
        ok = "静态OK" if meta and not meta.get("problems") else (
            "静态FAIL" if meta else "未转写")
        print(f"{d.name}  {ok:8s} 回测{'有' if rep else '无'}  {meta.get('title', '')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=".pine → Pyne-Python 转写与验证")
    ap.add_argument("--id", help="策略库 id（如 0001）")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--validate", metavar="SYMBOL", help="用该标的跑一次真实回测")
    ap.add_argument("--adj", default="backward")
    ap.add_argument("--install", action="store_true", help="装进 lab_strategies")
    ap.add_argument("--force", action="store_true", help="已有候选时强制重新转写")
    ap.add_argument("--no-transpile", action="store_true",
                    help="只校验已有候选（不调模型）。队列 worker 的沙箱回测段走这个。")
    ap.add_argument("--repair", metavar="ERROR",
                    help="用回测报错修一版候选（需已有 candidate.py；只写文件不执行）")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        return _list()
    if not args.id:
        ap.print_help()
        return 1

    if args.install:
        print(json.dumps(install(args.id), ensure_ascii=False, indent=2))
        return 0

    cand = OUT_DIR / args.id / "candidate.py"
    meta_path = OUT_DIR / args.id / "meta.json"
    if args.repair:
        try:
            meta = repair(args.id, args.repair, model=args.model)
        except RuntimeError as exc:
            print(f"修复失败：{exc}")
            return 3
        last = meta["repairs"][-1]
        print(f"修复完成（{last['elapsed_sec']}s，{last['usage']}）")
    elif args.no_transpile:
        if not cand.exists() or not meta_path.exists():
            print(f"没有候选文件：{cand}（先转写一次）")
            return 2
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    elif cand.exists() and meta_path.exists() and not args.force:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        print(f"复用已有候选：{meta.get('title')}（--force 可重新转写）")
    else:
        try:
            meta = transpile(args.id, model=args.model)
        except RuntimeError as exc:
            # 队列 worker 拿最后一行当界面上的失败原因，所以这里只打一行、不打栈
            print(f"转写失败：{exc}")
            return 3
        print(f"转写完成：{meta['title']}（{meta['elapsed_sec']}s，{meta['usage']}）")
    if meta["problems"]:
        print("静态检查未通过：")
        for p in meta["problems"]:
            print("  -", p)
        return 2
    print("静态检查通过")

    if args.validate:
        rep = validate(args.id, args.validate, adj=args.adj)
        s = rep["stats"]
        net = (s.get("Net profit") or {}).get("pct")
        dd = (s.get("Max equity drawdown") or {}).get("pct")
        bh = (s.get("Buy & hold return") or {}).get("pct")
        print(f"回测 {args.validate}：净收益 {net:.1f}% · 最大回撤 {dd:.1f}% · "
              f"买入持有 {bh:.1f}% · 成交 {rep['trades']} 笔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
