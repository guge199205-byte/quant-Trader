"""TCA 接线的静态不变量（AST）——漏接一个写入点，运行时**没有任何症状**。

2026-09-19 接线把三段价（ref/limit/fill）写进成交流水。这类改动最危险的失败
形态不是崩溃，而是**静默少记**：报告照常出，只是样本少一批，没人看得出。
人眼审不住散落在 4 个执行路径里的 dict 字面量，所以钉两条机器可读的不变量：

  1. **成交行必须带 TCA 字段**：任何写进流水的行，只要带 `"fill"` 键（= 真成交）
     或 `"mode": "fill_confirm"`（= 对账补记的真成交），就必须展开
     `**exec_cost.tape_fields(...)`，且参数齐全（ref/limit/fill/量/三个时刻）。
  2. **settle_place_fill 的价格参数 = 委托限价**（两侧同义）：买入路径必须传
     `o["limit_price"]`，**不是**基准价 `o["price"]`。历史漂移实录：买入侧传的是
     基准价，而该参数被用来（a）桥没回报成交价时的回退记账价、（b）「剩余未成交
     （限价 ¥X）」告警文案、（c）pending 在途价 → 三条全错，且无人能看见。

新增成交写入点如果没接线，本文件红灯——这正是它存在的理由（同 ghost 的
test_ghost_wiring：有闸门调用却无影子记录的函数 = 红灯）。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import exec_cost  # noqa: E402

SCRIPTS = ROOT / "scripts"
#: 成交行的必备 tape_fields 参数（side 在 exec_cost.FIELDS 里，一并要求）
REQUIRED_KW = ("side", "ref_px", "limit_px", "fill_px", "wanted", "filled",
               "decided_ts", "submit_ts", "fill_ts", "path")
#: 对账补记行走 pending 在途价，ref/decided 由 pending 条目带着（老条目可能没有）
REQUIRED_KW_CONFIRM = ("side", "ref_px", "limit_px", "fill_px", "wanted", "filled",
                       "decided_ts", "submit_ts", "fill_ts", "path")


def _tree(name: str) -> ast.Module:
    return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))


def _log_dicts(tree: ast.Module) -> list[ast.Dict]:
    """所有 log_line({...}) 的 dict 字面量。"""
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "log_line" and n.args
                and isinstance(n.args[0], ast.Dict)):
            out.append(n.args[0])
    return out


def _keys(d: ast.Dict) -> list:
    return [k.value for k in d.keys if isinstance(k, ast.Constant)]


def _tape_calls(d: ast.Dict) -> list[ast.Call]:
    """dict 里 `**expr` 展开的 tape_fields 调用（没有则空）。"""
    out = []
    for k, v in zip(d.keys, d.values):
        if k is None and isinstance(v, ast.Call):
            fn = v.func
            if isinstance(fn, ast.Name) and fn.id == "tape_fields":
                out.append(v)
            elif isinstance(fn, ast.Attribute) and fn.attr == "tape_fields":
                out.append(v)
    return out


def _kwarg_names(call: ast.Call) -> set:
    return {k.arg for k in call.keywords if k.arg}


def _fill_rows(name: str) -> list[ast.Dict]:
    return [d for d in _log_dicts(_tree(name)) if "fill" in _keys(d)]


def _confirm_rows(name: str) -> list[ast.Dict]:
    return [d for d in _log_dicts(_tree(name))
            if "fill_confirm" in [v.value for v in d.values
                                  if isinstance(v, ast.Constant)]]


# ---------------------------------------------------------------- 成交行接线

def test_exec_paths_write_tape_fields_on_every_fill_row():
    """4 个执行路径的成交行（带 "fill" 键）必须展开 tape_fields 且参数齐全。"""
    for name in ("live_llm_trade.py", "live_hourly_analysis.py"):
        rows = _fill_rows(name)
        assert len(rows) == 2, f"{name} 成交行数变了（现在 {len(rows)}）——接线要跟着改"
        for d in rows:
            calls = _tape_calls(d)
            assert calls, f"{name}: 成交行没有 **exec_cost.tape_fields(...)"
            missing = set(REQUIRED_KW) - _kwarg_names(calls[0])
            assert not missing, f"{name}: tape_fields 缺参数 {sorted(missing)}"


def test_reconcile_confirm_row_writes_tape_fields():
    """对账补记行（fill_confirm）也是真成交：余量由它记账，不接线就整批失踪。"""
    rows = _confirm_rows("live_fills.py")
    assert len(rows) == 1, f"fill_confirm 行数变了（现在 {len(rows)}）"
    calls = _tape_calls(rows[0])
    assert calls, "live_fills.py: fill_confirm 行没有 **exec_cost.tape_fields(...)"
    missing = set(REQUIRED_KW_CONFIRM) - _kwarg_names(calls[0])
    assert not missing, f"fill_confirm: tape_fields 缺参数 {sorted(missing)}"


def test_wired_files_import_exec_cost():
    """接线文件必须在模块层 import exec_cost（函数里 import 会随路径漂移）。"""
    for name in ("live_llm_trade.py", "live_hourly_analysis.py", "live_fills.py"):
        mods = {a.name for n in ast.walk(_tree(name)) if isinstance(n, ast.Import)
                for a in n.names}
        assert "exec_cost" in mods, f"{name} 没有模块级 import exec_cost"


# ---------------------------------------------------------------- 限价口径

def test_settle_place_fill_price_arg_is_the_limit_on_all_exec_paths():
    """settle_place_fill 的价格参数是**委托限价**（两侧同义）。

    买入必须传 o["limit_price"]：该参数进 pending 在途价（桥不回价时的回退记账价）、
    「剩余未成交（限价 ¥X）」告警文案、fill_abort 流水价。传基准价 = 三条全错。
    """
    found = {"buy": 0, "sell": 0}
    for name in ("live_llm_trade.py", "live_hourly_analysis.py"):
        for n in ast.walk(_tree(name)):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "settle_place_fill"):
                continue
            side = ast.literal_eval(n.args[3]) if isinstance(n.args[3], ast.Constant) else ""
            price_src = ast.unparse(n.args[5])
            assert side in found, f"{name}: 意外的方向参数 {side!r}"
            found[side] += 1
            if side == "buy":
                # ast.unparse 会把字符串引号归一为单引号
                assert price_src == "o['limit_price']", \
                    f"{name}: 买入的 settle price 应为限价，现为 {price_src}"
            else:
                assert price_src == "limit", \
                    f"{name}: 卖出的 settle price 应为限价，现为 {price_src}"
    assert found == {"buy": 2, "sell": 2}, f"settle_place_fill 调用点变了：{found}"


def test_sell_limit_has_one_source():
    """0.99 卖价缓冲只许有一处（live_fills.sell_limit）——此前两个文件各写一遍。"""
    import live_fills as F

    assert F.sell_limit(10.0) == 9.9
    assert F.sell_limit(33.40) == 33.07          # round(33.066, 2)
    assert "SELL_LIMIT_BUFFER" in F.__dict__
    for name in ("live_llm_trade.py", "live_hourly_analysis.py"):
        src = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "0.99" not in src, f"{name} 里还有内联的 0.99 卖价缓冲"


def test_exec_cost_field_vocabulary_is_closed():
    """tape_fields 写出的键 = exec_cost.FIELDS（报告只认这个词表）。"""
    got = exec_cost.tape_fields(side="buy", ref_px=1.0, limit_px=1.1, fill_px=1.05,
                                wanted=100, filled=100, decided_ts="d", submit_ts="s",
                                fill_ts="f", path="p")
    assert set(got) == set(exec_cost.FIELDS)
