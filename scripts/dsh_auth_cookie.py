#!/usr/bin/env python3
"""签发 dsh web 的浏览器 cookie，生成 dsh-proxy 的 nginx include。

背景
----
dsh **0.1.5 起 web 端强制 token/cookie 鉴权**（0.1.1-rc.2 没有这层）。而 arena 的
/harness 是个 `src` 写死、**不带 token** 的 iframe，唯一进门凭证就是 cookie。
（见 docs/AGENT_WORK_OBSERVABILITY.md §4.7）

做法
----
用 dsh **持久化**的签名密钥（$DSH_HOME/.credentials.yaml，容器重启不变）预先签好
cookie，由 dsh-proxy 在转发时注入请求头。用户因此无需手动做 token 交换。

签名格式严格对齐 `@deepseek-ai/dsh-client-connection` 的 BrowserAuth：
    secret = 解码 credentials 里 payload.secret（base64url → 32 字节）
    name   = "dsh-auth-" + b64url(sha256(authority))
    body   = b64url(json({"version":1,"authority":a,"issuedAt":t,"expiresAt":e}))
    sig    = b64url(hmac_sha256(key=secret_bytes, msg=body))
    value  = f"v1.{body}.{sig}"

⚠️ 两条硬约束（源码 authorizeIndex 实测）：
    1. `expiresAt - issuedAt` 必须 **<= cookieMaxAgeDays × 1天**
       （配置在 dsh/baymax.cordis.yml 的 `connection` 插件；本仓库设为 3650）
       → **改那个值之后必须重跑本脚本**，否则旧 cookie 会因超龄被拒。
    2. cookie 按 **authority(host:port) 精确绑定** → 用户从哪个地址访问就要签哪个；
       本脚本为下面 AUTHORITIES 列表逐个签发，dsh 只会用与请求 authority 匹配的那个。

用法
----
    python3 scripts/dsh_auth_cookie.py                 # 用默认 authority 列表
    python3 scripts/dsh_auth_cookie.py --days 3650     # 指定有效期上限
    python3 scripts/dsh_auth_cookie.py --authority 192.168.31.68:8093   # 追加/自定义（可重复）

输出：dsh/proxy/dsh-cookies.conf（nginx include，含 map 指令；mounted 进代理容器）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT / "dsh" / "proxy" / "dsh-cookies.conf"
CONTAINER = "baymax-dsh"
CRED_PATH_IN_CONTAINER = "/root/.dsh/.credentials.yaml"
HOST_CRED_PATH = ROOT / "dsh" / "root-dsh" / ".credentials.yaml"

# 与容器启动参数 --trusted-host 对齐（dsh 的 trusted-host fence 按 host:port 精确匹配）
DEFAULT_AUTHORITIES = [
    "192.168.31.68:8093",   # ① arena 前端 /harness 的 iframe 目标（最常用）
    "192.168.31.68:3081",   # ② 直连 dsh-proxy
    "127.0.0.1:8093",
    "localhost:8093",
]

COOKIE_PREFIX = "dsh-auth-"
PAYLOAD_VERSION = 1
DAY_MS = 86_400_000


def b64url(raw: bytes) -> str:
    return base64.b64encode(raw).decode().replace("+", "-").replace("/", "_").rstrip("=")


def b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def read_secret() -> bytes:
    """取持久化签名密钥。

    优先读宿主机绑定目录（若当前用户可读），否则经 docker exec 从容器内读——容器内是
    root，docker 组成员即可读，**不需要 sudo、不接触任何口令**。
    """
    text = ""
    if HOST_CRED_PATH.is_file():
        try:
            text = HOST_CRED_PATH.read_text(encoding="utf-8")
        except PermissionError:
            text = ""
    if not text:
        try:
            proc = subprocess.run(
                ["docker", "exec", CONTAINER, "cat", CRED_PATH_IN_CONTAINER],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemExit(f"❌ 读取凭据失败：{exc}") from None
        if proc.returncode != 0:
            raise SystemExit(
                f"❌ 从容器 {CONTAINER} 读取 {CRED_PATH_IN_CONTAINER} 失败："
                f"{(proc.stderr or '').strip()[:200]}"
            )
        text = proc.stdout

    match = re.search(r"^\s*secret:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise SystemExit("❌ 凭据文件里没找到 secret 字段（格式变了？）")
    try:
        secret = b64url_decode(match.group(1))
    except Exception:
        raise SystemExit("❌ secret 不是合法的 base64url") from None
    if len(secret) != 32:
        raise SystemExit(f"❌ secret 解码后应为 32 字节，实际 {len(secret)}")
    return secret


def mint(secret: bytes, authority: str, days: int) -> tuple[str, str]:
    """返回 (cookie 名, cookie 值)。"""
    name = COOKIE_PREFIX + b64url(hashlib.sha256(authority.encode()).digest())
    issued = int(time.time() * 1000)
    expires = issued + days * DAY_MS
    body = b64url(json.dumps(
        {"version": PAYLOAD_VERSION, "authority": authority,
         "issuedAt": issued, "expiresAt": expires},
        separators=(",", ":"), sort_keys=True,
    ).encode())
    sig = b64url(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return name, f"v1.{body}.{sig}"


def main() -> int:
    ap = argparse.ArgumentParser(description="签发 dsh web cookie 并生成 nginx include")
    ap.add_argument("--days", type=int, default=3600,
                    help="cookie 有效期天数；必须 <= dsh 的 cookieMaxAgeDays（默认 3650，这里取 3600 留余量）")
    ap.add_argument("--authority", action="append", default=[],
                    help="追加要签的 authority(host:port)，可重复")
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    args = ap.parse_args()

    authorities = list(dict.fromkeys(DEFAULT_AUTHORITIES + args.authority))
    secret = read_secret()

    pairs = []
    for authority in authorities:
        name, value = mint(secret, authority, args.days)
        pairs.append((authority, name, value))

    cookie_header = "; ".join(f"{name}={value}" for _, name, value in pairs)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "# 本文件由 scripts/dsh_auth_cookie.py 自动生成 —— 请勿手改。\n"
        "# 用途：dsh-proxy 转发时注入浏览器 cookie（dsh 0.1.5 起 web 强制 token/cookie 鉴权）。\n"
        "# 重新生成：python3 scripts/dsh_auth_cookie.py && docker restart baymax-dsh-proxy\n"
        "map $http_cookie $dsh_auth_cookie {\n"
        f'    default       "$http_cookie; {cookie_header}";\n'
        f'    "~*dsh-auth-" "{cookie_header}";\n'
        f'    ""            "{cookie_header}";\n'
        "}\n",
        encoding="utf-8",
    )
    args.out.chmod(0o644)

    print(f"✅ 已签发 {len(pairs)} 个 authority 的 cookie，有效期 {args.days} 天")
    for authority, name, _ in pairs:
        print(f"   {authority:24s} {name}")
    print(f"   写入: {args.out}")
    print("   生效: docker restart baymax-dsh-proxy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
