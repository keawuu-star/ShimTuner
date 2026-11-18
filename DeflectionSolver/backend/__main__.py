# -*- coding: utf-8 -*-
"""启动本地后端: python -m backend --port 0 (0 = 自动选空闲端口)."""

from __future__ import annotations

import argparse
import os
import socket
import sys


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ShimTuner 数值后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help="0 = 自动选择空闲端口")
    parser.add_argument("--log-level", default="warning")
    parser.add_argument("--print-port", action="store_true",
                        help="绑定成功后向 stdout 打印端口")
    args = parser.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if root not in sys.path:
        sys.path.insert(0, root)

    import uvicorn

    from .app import app

    port = args.port or _free_port(args.host)
    if args.print_port:
        print(f"SHIMTUNER_PORT={port}", flush=True)

    uvicorn.run(app, host=args.host, port=port,
                log_level=args.log_level, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
