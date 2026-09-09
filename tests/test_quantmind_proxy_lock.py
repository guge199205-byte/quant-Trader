"""quantmind 代理的 token 刷新绝不能把事件循环锁死。

2026-09-09 线上事故（14:18:59 JST，用户报「API 连接失败：timeout of 20000ms」）：
`deploy.sh` 重启 baymax-api → 进程内 token 缓存清空 → 数据平台页面同时发出 5 个
`/api/quantmind/*` 请求 → 它们都进 `get_token()` 的刷新分支 → 第一个协程拿到
`threading.Lock` 后 `await _login()`（让出控制权），第二个协程在**事件循环线程**上
调 `Lock.acquire()` 死等 → 持锁者永远排不上调度、锁永不释放。现象：端口在听、
CPU 0%、无 traceback、所有端点（含 async）全无响应，只能重启容器。
日志实证：那一刻 baymax-api 日志停在 05:18:59，quantmind 侧**没有**收到任何
login 请求（连接都没来得及发出去）。

这里用子进程 + 硬超时复现：真死锁时连 asyncio 的超时都排不上事件循环，
必须靠外部掐断，所以断言方式就是「子进程必须自己跑完」。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_quantmind_proxy_lock.py -q
"""
import pathlib
import subprocess
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 空缓存 + 慢登录 + 两个并发调用 = 事故当天的真实条件
REPRO = textwrap.dedent(
    """
    import asyncio, sys
    sys.path.insert(0, sys.argv[1])
    from backend.services import quantmind_proxy as qm

    async def slow_login():
        await asyncio.sleep(0.2)   # 真实环境是 0.3~4.2s 的 HTTP 登录
        return "tok"

    async def main():
        qm._login = slow_login
        qm._token_cache["value"] = None
        qm._token_cache["expires_at"] = 0.0
        return await asyncio.gather(qm.get_token(), qm.get_token())

    print(asyncio.run(main()))
    """
)


def test_concurrent_token_refresh_does_not_deadlock():
    # Arrange & Act：子进程跑并发刷新，外部硬超时兜底
    try:
        p = subprocess.run(
            [sys.executable, "-c", REPRO, str(ROOT)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=ROOT,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "并发刷新 token 把事件循环锁死了：get_token 在 await 期间持 threading.Lock"
        ) from None

    # Assert：两个协程都拿到 token，且子进程正常退出
    assert p.returncode == 0, p.stderr
    assert p.stdout.count("tok") == 2, p.stdout
