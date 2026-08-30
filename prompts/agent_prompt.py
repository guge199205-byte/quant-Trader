import os

from dotenv import load_dotenv

load_dotenv()
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Add project root directory to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
from tools.general_tools import get_config_value
from tools.price_tools import (all_nasdaq_100_symbols, all_sse_50_symbols,
                               format_price_dict_with_names, get_open_prices,
                               get_today_init_position, get_yesterday_date,
                               get_yesterday_open_and_close_price,
                               get_yesterday_profit)

all_hk_symbols = [
    "00700.HK", "09988.HK", "03690.HK", "01810.HK", "00941.HK",
    "00005.HK", "01299.HK", "00939.HK", "03988.HK", "00011.HK",
    "02318.HK", "02628.HK", "01398.HK", "00998.HK", "00388.HK",
    "01093.HK", "09618.HK", "09999.HK", "02020.HK", "01024.HK",
    "02331.HK", "02688.HK", "00288.HK", "00016.HK", "00027.HK",
    "01928.HK", "00267.HK", "00175.HK", "02382.HK", "06862.HK",
]

STOP_SIGNAL = "<FINISH_SIGNAL>"

agent_system_prompt = """
你是一名股票基本面分析交易助手。

你的目标：
- 通过调用可用工具进行思考和推理。
- 你需要分析各只股票的价格及其收益表现。
- 你的长期目标是通过这个投资组合实现收益最大化。
- 在做决策之前，尽可能通过搜索工具收集更多信息来辅助决策。

思考标准：
- 清晰展示关键中间步骤：
  - 读取昨日持仓和今日价格输入
  - 更新每只标的的估值并调整权重（如果策略需要）

注意事项：
- 操作过程中无需请求用户许可，可直接执行
- 你必须通过调用工具来执行操作，直接输出操作指令不会被接受
- 决策前先调用 baymax_memory 的 read_memory 回顾历史心得（如果可用）
- 收盘后用 baymax_memory 的 append_memory 沉淀今日经验（如果可用）

以下是你需要的信息：

当前时间：
{date}

你当前的持仓（股票代码后的数字代表持股数量，CASH 后的数字代表可用现金）：
{positions}

你所持股票的当前市值：
{yesterday_close_price}

当前买入价格：
{today_buy_price}

当你认为任务已完成时，输出
{STOP_SIGNAL}
"""


def get_agent_system_prompt(
    today_date: str, signature: str, market: str = "us", stock_symbols: Optional[List[str]] = None
) -> str:
    print(f"signature: {signature}")
    print(f"today_date: {today_date}")
    print(f"market: {market}")

    # Auto-select stock symbols based on market if not provided
    if stock_symbols is None:
        stock_symbols = all_sse_50_symbols if market == "cn" else all_nasdaq_100_symbols

    # Get yesterday's buy and sell prices
    yesterday_buy_prices, yesterday_sell_prices = get_yesterday_open_and_close_price(
        today_date, stock_symbols, market=market
    )
    today_buy_price = get_open_prices(today_date, stock_symbols, market=market)
    today_init_position = get_today_init_position(today_date, signature)
    # yesterday_profit = get_yesterday_profit(today_date, yesterday_buy_prices, yesterday_sell_prices, today_init_position)
    
    return agent_system_prompt.format(
        date=today_date,
        positions=today_init_position,
        STOP_SIGNAL=STOP_SIGNAL,
        yesterday_close_price=yesterday_sell_prices,
        today_buy_price=today_buy_price,
        # yesterday_profit=yesterday_profit
    )


if __name__ == "__main__":
    today_date = get_config_value("TODAY_DATE")
    signature = get_config_value("SIGNATURE")
    if signature is None:
        raise ValueError("SIGNATURE environment variable is not set")
    print(get_agent_system_prompt(today_date, signature))
