#!/usr/bin/env python3
"""nof0 前端静态服务。

python -m http.server 默认不跟随符号链接（nof0/data -> ../data 会 404），
这里显式 follow_symlinks=True，让相对路径请求也能读到实时数据。
"""
import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler


def main():
    parser = argparse.ArgumentParser(description="静态文件服务（跟随符号链接）")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dir", default=".")
    args = parser.parse_args()

    class FollowSymlinksHandler(SimpleHTTPRequestHandler):
        follow_symlinks = True  # 类属性；http.server 据此决定是否跟随符号链接

    handler = lambda *a, **k: FollowSymlinksHandler(*a, directory=args.dir, **k)
    httpd = HTTPServer(("0.0.0.0", args.port), handler)
    print(f"serving {args.dir} (follow_symlinks=True) on 0.0.0.0:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
