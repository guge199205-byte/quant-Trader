"""
@pyne

冒烟用例：手写的 PyneCore 策略（模拟 `pyne compile` 的产物），用来验证
「quantdb 数据 → Pine 运行时 → 逐笔成交/统计 CSV」这条链路是否通。

链路验证不需要 PyneSys API key（编译才需要）。本文件跑通即证明：
运行时可用、A股后复权日线能喂进去、strategy.* 的成交与统计能落盘。
"""
from pynecore.lib import close, input, script, strategy, ta
from pynecore.types import Series

__pyne__ = True   # 标记：本文件是 Pyne 代码（运行时按 Pyne 语义转换）


@script.strategy("SMA Cross Smoke", overlay=True)
def main(
    fast=input(5, "Fast"),
    slow=input(20, "Slow"),
):
    f = ta.sma(close, fast)
    s = ta.sma(close, slow)
    above: Series[bool] = close > f and f > s
    if above and not above[1]:
        strategy.entry('L', strategy.long)
    elif not above and above[1]:
        strategy.close('L')


if __name__ == "__main__":
    from pynecore.standalone import run

    run(__file__)
