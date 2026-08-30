#!/usr/bin/env python3
"""固化 langchain-mcp-adapters 的 mcp>=1.0 兼容补丁。

背景：mcp 1.x 将 streamable_http_client 改名为 streamablehttp_client，
而 langchain-mcp-adapters 0.3.0 的 sessions.py 仍 import 旧名，
导致 MultiServerMCPClient 初始化失败。此脚本在安装依赖后重打补丁。
"""

import sys
from pathlib import Path


def main() -> int:
    site_packages = _find_site_packages()
    if site_packages is None:
        print("❌ 未找到 site-packages，请确认在项目 venv 中运行")
        return 1

    sessions_py = site_packages / "langchain_mcp_adapters" / "sessions.py"
    if not sessions_py.exists():
        print(f"⚠️  未安装 langchain-mcp-adapters（{sessions_py} 不存在），跳过")
        return 0

    src = sessions_py.read_text(encoding="utf-8")

    if "streamablehttp_client as streamable_http_client" in src:
        print("✅ 补丁已存在，跳过")
        return 0

    old_import = (
        "from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client"
    )
    new_import = (
        "from mcp.client.streamable_http import create_mcp_http_client\n"
        "try:\n"
        "    from mcp.client.streamable_http import streamable_http_client\n"
        "except ImportError:  # mcp>=1.0 renamed it to streamablehttp_client\n"
        "    from mcp.client.streamable_http import streamablehttp_client as streamable_http_client"
    )
    if old_import not in src:
        print("❌ 未找到预期 import 语句，sessions.py 结构可能已变化")
        return 1
    src = src.replace(old_import, new_import)

    # 适配新函数签名：http_client= 参数在 mcp>=1.0 已移除
    old_call = (
        "        streamable_http_client(\n"
        "            url,\n"
        "            http_client=client,\n"
        "            terminate_on_close=terminate_on_close,\n"
        "        ) as (read, write, _),"
    )
    new_call = (
        "        streamable_http_client(\n"
        "            url,\n"
        "            headers=headers,\n"
        "            timeout=timeout_seconds,\n"
        "            sse_read_timeout=sse_read_timeout_seconds,\n"
        "            terminate_on_close=terminate_on_close,\n"
        "            httpx_client_factory=client_factory,\n"
        "            auth=auth,\n"
        "        ) as (read, write, _),"
    )
    if old_call in src:
        src = src.replace(old_call, new_call)
        # 移除不再使用的外部 client 创建
        old_client = (
            "    client = client_factory(\n"
            "        headers=headers,\n"
            "        timeout=httpx.Timeout(timeout_seconds, read=sse_read_timeout_seconds),\n"
            "        auth=auth,\n"
            "    )\n"
            "\n"
        )
        src = src.replace(old_client, "")
    elif "http_client=client" in src:
        print("⚠️  http_client= 调用点已变化，仅打了 import 补丁；请人工检查")
        sessions_py.write_text(src, encoding="utf-8")
        return 2

    sessions_py.write_text(src, encoding="utf-8")
    print(f"✅ 补丁已应用: {sessions_py}")
    return 0


def _find_site_packages() -> Path | None:
    for base in Path(sys.prefix).glob("lib/python*/site-packages"):
        return base
    return None


if __name__ == "__main__":
    sys.exit(main())
