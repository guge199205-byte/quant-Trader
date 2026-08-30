"""新闻情绪策略对比：三模式并排回测。

模式:
  - follow:  利好→做多, 利空→做空 (顺势)
  - fade:    全部→做空 (fade the news)
  - long_only: 利好→做多, 利空→跳过 (只做多)
"""
import sys
import os
import json
import importlib.util
from pathlib import Path

# 动态导入回测脚本
script_path = Path(__file__).resolve().parent / "backtest_news_sentiment.py"
spec = importlib.util.spec_from_file_location("backtest_news_sentiment", script_path)
bt = importlib.util.module_from_spec(spec)

# 先修改配置再导入（用 exec 方式）
# 更简单的方法：直接修改脚本的全局变量
import importlib.machinery
loader = importlib.machinery.SourceFileLoader("backtest_news_sentiment", str(script_path))

# 实际上最简单的方法是直接 exec 脚本内容并修改配置
# 让我们用 subprocess 的方式，通过环境变量控制模式

import subprocess

MODES = [
    ("follow", "利好做多/利空做空"),
    ("fade", "全部做空 (fade)"),
    ("long_only", "只做多"),
]

results = {}

for mode_key, mode_name in MODES:
    print(f"\n{'='*60}")
    print(f"运行模式: {mode_name} ({mode_key})")
    print(f"{'='*60}")

    # 读取脚本，替换 STRATEGY_MODE
    with open(script_path) as f:
        code = f.read()

    # 替换配置行
    import re
    code = re.sub(r'STRATEGY_MODE = "(follow|fade|long_only)"', f'STRATEGY_MODE = "{mode_key}"', code)

    # 写入临时脚本
    tmp_path = script_path.parent / f"_tmp_bt_{mode_key}.py"
    with open(tmp_path, "w") as f:
        f.write(code)

    # 运行
    result = subprocess.run(
        ["docker", "exec", "quantmind", "python3", f"/app/scripts/_tmp_bt_{mode_key}.py"],
        capture_output=True, text=True, timeout=180
    )

    # 清理
    tmp_path.unlink()

    # 解析输出
    output = result.stdout
    if result.stderr:
        print(result.stderr[:500])

    # 提取关键指标
    def extract(pattern, text):
        m = re.search(pattern, text)
        return m.group(1) if m else "N/A"

    total_ret = extract(r"累计收益率\s*\|\s*([+-][\d.]+)%", output)
    max_dd = extract(r"最大回撤\s*\|\s*([\d.]+)%", output)
    sharpe = extract(r"夏普比率\s*\|\s*([\d.]+)", output)
    n_trades = extract(r"总交易次数\s*\|\s*(\d+)", output)
    win_rate = extract(r"胜率\s*\|\s*([\d.]+)%", output)
    total_pnl = extract(r"总盈亏\s*\|\s*([+-][\d,]+)", output)
    n_long = extract(r"做多\s*(\d+)", output)
    n_short = extract(r"做空\s*(\d+)", output)

    # 出场原因
    stop_loss_pnl = extract(r"stop_loss\s*\|\s*\d+\s*\|\s*([+-][\d,]+)", output)
    trailing_pnl = extract(r"trailing_stop\s*\|\s*\d+\s*\|\s*([+-][\d,]+)", output)
    max_hold_pnl = extract(r"max_hold\s*\|\s*\d+\s*\|\s*([+-][\d,]+)", output)

    results[mode_key] = {
        "name": mode_name,
        "total_ret": total_ret,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl.replace(",", ""),
        "n_long": n_long,
        "n_short": n_short,
        "stop_loss_pnl": stop_loss_pnl.replace(",", "") if stop_loss_pnl != "N/A" else "N/A",
        "trailing_pnl": trailing_pnl.replace(",", "") if trailing_pnl != "N/A" else "N/A",
        "max_hold_pnl": max_hold_pnl.replace(",", "") if max_hold_pnl != "N/A" else "N/A",
    }

    print(output.split("## 收益指标")[1].split("## 交易统计")[0] if "## 收益指标" in output else "解析失败")


# 打印对比表
print("\n\n")
print("=" * 80)
print("三模式策略对比")
print("=" * 80)
print()
print(f"| 指标 | follow (顺势) | fade (全做空) | long_only (只做多) |")
print(f"|------|---------------|---------------|---------------------|")
for metric, label in [
    ("total_ret", "累计收益"),
    ("max_dd", "最大回撤"),
    ("sharpe", "夏普比率"),
    ("n_trades", "交易次数"),
    ("win_rate", "胜率"),
    ("total_pnl", "总盈亏"),
]:
    vals = []
    for m in ["follow", "fade", "long_only"]:
        v = results.get(m, {}).get(metric, "N/A")
        if metric in ("total_ret", "max_dd", "win_rate"):
            vals.append(f"{v}%")
        elif metric == "total_pnl":
            vals.append(f"{v} 元")
        else:
            vals.append(str(v))
    print(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")

print()
print(f"| 出场原因 | follow | fade | long_only |")
print(f"|----------|--------|------|-----------|")
for reason, key in [("stop_loss", "stop_loss_pnl"), ("trailing_stop", "trailing_pnl"), ("max_hold", "max_hold_pnl")]:
    vals = []
    for m in ["follow", "fade", "long_only"]:
        v = results.get(m, {}).get(key, "N/A")
        vals.append(f"{v} 元" if v != "N/A" else "N/A")
    print(f"| {reason} | {vals[0]} | {vals[1]} | {vals[2]} |")

# 保存
with open((_find_repo_root(Path(__file__).resolve()) / "data") / "strategy_comparison.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存到 data/strategy_comparison.json")