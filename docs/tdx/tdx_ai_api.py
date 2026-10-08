"""
tdx_ai_api.py — 通达信 TdxAiData 数据接口的统一引入入口（方案 B：薄封装，指向中央 bundle）。

为什么这样做：
    C:\\new_tdx_test\\TdxAiData 是通达信安装自带目录，会随客户端更新而变化。
    本模块【不拷贝】任何 DLL / 文件，只在运行时指向这个“活”目录，
    因此通达信一更新，你就自动用上最新的 tqServer.py 与 TdxAiData.dll，无需手动同步。

在新项目里怎么用：
    把本文件（tdx_ai_api.py，纯代码、无二进制）复制到项目根目录，然后：

        from tdx_ai_api import tqs

        data = tqs.get_market_data(
            field_list=["Open", "High", "Low", "Close"],
            stock_list=["600000.SH"],
            period="1d",
            start_time="2025-01-01",
            end_time="2025-01-31",
        )

目录位置变了怎么办（通达信更新到别的路径）：
    不用改代码，设一个环境变量即可：
        PowerShell（临时）：  $env:TDX_AI_DATA_DIR = "D:\\path\\to\\TdxAiData"
        或在系统环境变量里永久设置 TDX_AI_DATA_DIR

两个必须知道的坑：
    1) 首次调用任意 tqs 接口时，tqServer 会 os.chdir 到 DLL 目录读取 TdxAiData.ini，
       之后进程工作目录会停在 DLL 目录（如 C:\\new_tdx_test\\TdxAiData）。
       → 你自己的文件读写请一律用【绝对路径】，或用本模块导出的 PROJECT_CWD 拼绝对路径。
    2) 必须使用【64 位 Python】（DLL 是 x64，32 位会加载失败）。
    3) 通达信更新后若接口签名有变化，调用处可能需相应调整；更新后建议先跑一次自检脚本。
"""

from __future__ import annotations

import os
import sys

# 通达信自带的 TdxAiData 目录（可用环境变量 TDX_AI_DATA_DIR 覆盖）
TDX_AI_DATA_DIR = os.environ.get("TDX_AI_DATA_DIR", r"C:\new_tdx_test\TdxAiData")

# 导入时（任何 tqs 调用之前）记录启动目录，供你用绝对路径读写项目自己的文件
PROJECT_CWD = os.getcwd()


def _bootstrap() -> None:
    """把中央 bundle 目录接入当前解释器：定位 tqServer.py、DLL 及其依赖。"""
    if not os.path.isdir(TDX_AI_DATA_DIR):
        raise FileNotFoundError(
            f"未找到 TdxAiData 目录：{TDX_AI_DATA_DIR}\n"
            "请确认通达信已安装；若目录位置变了，设置环境变量 TDX_AI_DATA_DIR 指向它"
            "（该目录应包含 tqServer.py 与 TdxAiData.dll）。"
        )
    if not os.path.isfile(os.path.join(TDX_AI_DATA_DIR, "tqServer.py")):
        raise FileNotFoundError(
            f"{TDX_AI_DATA_DIR} 下没有 tqServer.py：通达信可能更新了目录结构，"
            "请检查路径或更新环境变量 TDX_AI_DATA_DIR。"
        )

    # 1) 让 import 能找到该目录下的 tqServer.py
    if TDX_AI_DATA_DIR not in sys.path:
        sys.path.insert(0, TDX_AI_DATA_DIR)

    # 2) 显式指定原生库绝对路径（不设时 tqServer 默认用自身同目录的 DLL）
    lib = "TdxAiData.dll" if os.name == "nt" else "libTdxAiData.so"
    os.environ.setdefault("TDX_AI_DATA_LIB", os.path.join(TDX_AI_DATA_DIR, lib))

    # 3) Windows：把该目录加入 DLL 搜索路径，确保 TdxAsioComm64.dll 等依赖可被加载
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(TDX_AI_DATA_DIR)


_bootstrap()

# 必须在 _bootstrap() 设置好 sys.path 之后再导入
from tqServer import tqs  # noqa: E402

__all__ = ["tqs", "TDX_AI_DATA_DIR", "PROJECT_CWD"]
