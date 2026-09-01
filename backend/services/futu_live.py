"""富途 OpenD 直连服务（BayMax 自有实现，不经任何外部平台）。

Live 页港股实盘/模拟账户、当日订单、已平仓查询。futu SDK 的连接/等待模型与
asyncio 事件循环混用会死锁，故每次调用起独立子进程（futu_subprocess.py），
结果经临时文件回传。单次调用 ~4s（RSA 握手 + OpenSecTradeContext 主导，
OpenD 串行化）；前端 Live.tsx 挂 15s 后台轮询预热规避点击延迟。

连接配置（.env 可覆盖）：
  FUTU_OPEND_HOST — OpenD 网关地址；未设置时自动探测：先试默认路由网关
                    （容器场景，OpenD 端口发布在宿主机），不通再回退 127.0.0.1
                    （宿主机直跑场景），结果缓存
  FUTU_OPEND_PORT — 默认 11111
  FUTU_RSA_KEY    — OpenD RSA 私钥文件；默认 <项目根>/config/futu/rsa.key
                    （config/ 目录经 compose 挂载进容器，rsa.key 不入库）
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = Path(__file__).resolve().parent / "futu_subprocess.py"
_SUBPROC_TIMEOUT = 30.0  # RSA 握手 + OpenSecTradeContext ~4s，留足余量
_HOST_CACHE: str | None = None  # 探测成功的 OpenD 地址（进程内缓存）


def _default_gateway() -> str:
    """读 /proc/net/route 取默认网关（docker bridge 网络里即宿主机）。"""
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            next(f)  # 表头
            for line in f:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    hex_gw = parts[2]
                    return ".".join(str(int(hex_gw[i : i + 2], 16)) for i in (6, 4, 2, 0))
    except (OSError, StopIteration, ValueError):
        pass
    return "127.0.0.1"


def _opend_host() -> str:
    """OpenD 地址：env 显式指定优先；否则探测（网关→127.0.0.1）并缓存。"""
    global _HOST_CACHE  # noqa: PLW0603
    env_host = os.getenv("FUTU_OPEND_HOST", "").strip()
    if env_host:
        return env_host
    if _HOST_CACHE:
        return _HOST_CACHE
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    for cand in (_default_gateway(), "127.0.0.1"):
        try:
            with socket.create_connection((cand, port), timeout=1.0):
                _HOST_CACHE = cand
                return cand
        except OSError:
            continue
    return "127.0.0.1"


def _rsa_key() -> str:
    env_key = os.getenv("FUTU_RSA_KEY", "").strip()
    if env_key:
        return env_key
    return str(_ROOT / "config" / "futu" / "rsa.key")


async def run_op(op: str, payload: dict | None = None) -> dict:
    """起子进程执行一个 futu op，返回其 JSON 输出；失败抛 RuntimeError。"""
    fd, out_name = tempfile.mkstemp(prefix="baymax_futu_", suffix=".json")
    os.close(fd)
    out_path = Path(out_name)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_SCRIPT), _opend_host(),
            os.getenv("FUTU_OPEND_PORT", "11111"), _rsa_key(), op,
            json.dumps(payload or {}), str(out_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROC_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("futu 子进程超时（OpenD 无响应或 RSA 配置错误）") from None
        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(f"futu 子进程退出码 {proc.returncode}: {detail or '无输出'}")
        try:
            return json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RuntimeError(f"futu 子进程无有效输出: {e}") from e
    finally:
        out_path.unlink(missing_ok=True)


async def query_account(env: str = "SIMULATE") -> dict:
    """单 env 账户（资产/持仓）。env: REAL / SIMULATE。"""
    return await run_op("account", {"env": env.upper()})


async def query_account_both() -> dict:
    """一次握手查 REAL+SIMULATE 两套账户。"""
    return await run_op("account_both", {"env": "REAL"})


async def query_orders(env: str = "SIMULATE") -> dict:
    """当日订单历史（order_list_query）。"""
    return await run_op("orders", {"env": env.upper()})


async def query_closed(env: str = "SIMULATE") -> dict:
    """已平仓行（qty==0 且 realized_pl!=0）。"""
    return await run_op("closed", {"env": env.upper()})


async def query_snapshot(codes: list[str]) -> dict:
    """实时快照：{snapshot: {code: {name, last_price, prev_close, day_chg, ...}}}。"""
    return await run_op("snapshot", {"codes": [str(c) for c in codes]})


async def place_order(order: dict, env: str = "SIMULATE", market: str = "HK") -> dict:
    """富途下单（HK/US 实盘/模拟）：order {code, price, quantity, order_type, trd_side}。"""
    return await run_op("place", {"order": order, "env": env.upper(),
                                  "market": str(market).upper()})


async def cancel_order(order_id: str, env: str = "SIMULATE", market: str = "HK") -> dict:
    """撤单（HK/US 实盘/模拟）。"""
    return await run_op("cancel", {"order_id": order_id, "env": env.upper(),
                                   "market": str(market).upper()})
