"""影子账**接线**的静态防线（2026-09-19）。

影子代价账的全部价值建立在"每条闸门触发时都留了痕"这个前提上。而闸门是**会长出来**的：
本项目至今每一次加闸都是往两条买入路径（09:35 主入口 / 整点轮）各补一段
`if ...: print(...); continue`——补 print 是顺手的，补影子账不是。少接一处，
报告上不会出现"缺了这一条"，只会出现"这条规则很省钱"。**假绿比没有更危险**。

所以用 AST 静态检查（不是运行时——运行时只覆盖走过的分支）钉四件事：

  1. 出现 `check_buy(` / `compute_order(` / `filter_affordable(` 的函数里，
     必须有 `ghost_ledger.veto(` 调用（同一函数域内）；
  2. `veto(...)` 的规则参数必须是**词表引用**（`gate_rules.X` / `bd.rule` /
     `o["rule"]`），不许写字面量字符串——写死的字符串就是下一条"改了名字历史断档"
     的规则；
  3. 词表里的每条规则都得有出处：要么被两条买入路径引用，要么被回填脚本引用
     （只登记不接线的规则 = 登记表在自我安慰）；
  4. 入口文件里引用的 `gate_rules.XXX` 必须真的在词表里（防改名漏改）。

不做的事：不检查"每条分支是否都接全"（静态上不可判，分支条件千变万化）。
那一条靠人工评审 + `gate_registry.json` 的 evidence 字段承载。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_ghost_wiring.py -q
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gate_rules as GR  # noqa: E402

#: 需要接影子账的入口（有新买入路径必须先加进来，否则本文件对它是瞎的）
ENTRY_FILES = ("live_llm_trade.py", "live_hourly_analysis.py", "ghost_backfill_logs.py")
GATE_CALLS = ("check_buy", "compute_order", "filter_affordable")


def _tree(name: str) -> ast.Module:
    return ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"))


def _called_name(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")


def _walk_calls(tree: ast.Module, names: tuple[str, ...]):
    """产出 (所属函数名, Call 节点)；模块级调用归属 "<module>"。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) in names:
            out.append((node, getattr(node, "lineno", 0)))
    return out


def _functions(tree: ast.Module) -> dict:
    """函数名 → 该函数体内的所有 Call 节点（按源码行区间切分）。"""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lo, hi = fn.lineno, max((getattr(n, "lineno", 0) for n in ast.walk(fn)), default=0)
        out[fn.name] = [n for n in ast.walk(tree)
                        if isinstance(n, ast.Call) and lo <= getattr(n, "lineno", 0) <= hi]
    return out


def _file_exists(name: str) -> bool:
    return (ROOT / "scripts" / name).is_file()


@pytest.mark.parametrize("fname", [f for f in ENTRY_FILES if _file_exists(f)])
def test_every_gate_call_site_has_a_ghost_recorder(fname):
    """闸门所在函数必须同时有影子账写入——同函数域内，不做跨函数推导。

    同函数是刻意的：跨函数（比如"调了 helper 就算接了"）会让检查退化成
    "库里某处有 record"，一处没接照样绿。
    """
    tree = _tree(fname)
    funcs = _functions(tree)
    missing = []
    for fn, calls in funcs.items():
        gated = [c for c in calls if _called_name(c) in GATE_CALLS]
        recorded = [c for c in calls if _called_name(c) == "veto"
                    or _called_name(c) == "record_event"]
        if gated and not recorded:
            missing.append(f"{fname}:{fn}（{sorted({_called_name(c) for c in gated})}）")
    assert not missing, ("这些函数里有闸门调用却没有影子账记录（加了闸没接线）：\n  "
                         + "\n  ".join(missing))


@pytest.mark.parametrize("fname", [f for f in ENTRY_FILES if _file_exists(f)])
def test_veto_rule_argument_is_a_vocabulary_reference(fname):
    """规则参数不许是字面量字符串：写死的 id 就是下一条"改文案断历史"的规则。"""
    tree = _tree(fname)
    bad = []
    for node, line in _walk_calls(tree, ("veto", "record_event")):
        if node.args and isinstance(node.args[0], ast.Constant):
            pass                       # 第 0 参是 agent（字符串，正常）
        rule = None
        if len(node.args) >= 3:
            rule = node.args[2]        # veto(agent, code, rule, now, ...)
        else:
            rule = next((k.value for k in node.keywords if k.arg == "rule"), None)
        if rule is None:
            continue
        if isinstance(rule, ast.Constant) and isinstance(rule.value, str):
            bad.append(f"{fname}:{line} 规则写成字面量 {rule.value!r}")
        elif isinstance(rule, ast.Attribute) and not (
                isinstance(rule.value, ast.Name) and rule.value.id == "gate_rules"):
            # `bd.rule`（buy_gate.BuyDecision）是允许的：那个值本身在 buy_gate 里
            # 由词表赋入，且 tests/test_buy_gate.py 逐分支钉了它的取值。
            if rule.attr != "rule":
                bad.append(f"{fname}:{line} 规则来自 {rule.attr}（不是词表引用）")
    assert not bad, "veto 的规则参数必须是 gate_rules 词表引用：\n  " + "\n  ".join(bad)


def test_every_vocabulary_rule_is_reachable_from_some_emitter():
    """词表里的每条规则都得有人发得出来——只登记不接线 = 登记表在自我安慰。

    扫描面是 scripts/ 全体（排除词表自身，否则定义里的字符串常量会让检查永真）：
    规则可以由入口直接发（`gate_rules.LIMIT_UP`），也可以由带 `rule` 字段的
    返回值带出（buy_gate / live_trade_picks 里的词表引用同样计入），
    回填脚本按字符串 id 发（历史数据无类型可依）。
    """
    src = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted((ROOT / "scripts").glob("*.py"))
                    if p.name != "gate_rules.py")
    names = {v: k for k, v in vars(GR).items()
             if k.isupper() and isinstance(v, str) and v in GR.ALL}
    unused = [r for r in GR.ALL
              if f"gate_rules.{names.get(r, '?')}" not in src and f'"{r}"' not in src]
    assert not unused, f"词表里没有任何调用点的规则（登记了却没人发）：{unused}"


def test_entry_files_only_reference_known_rules():
    """防改名漏改：文件里出现的 gate_rules.XXX 必须真的在词表里。"""
    known = {c for c in dir(GR) if c.isupper()}
    bad = []
    for fname in ENTRY_FILES:
        if not _file_exists(fname):
            continue
        for node in ast.walk(_tree(fname)):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "gate_rules" and node.attr.isupper()
                    and node.attr not in known):
                bad.append(f"{fname} 引用了不存在的 gate_rules.{node.attr}")
    assert not bad, "\n  " + "\n  ".join(bad)
