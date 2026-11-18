# -*- coding: utf-8 -*-
"""启动本地后端：python -m backend --port 0（0 = 自动选空闲端口）。"""

from __future__ import annotations

import argparse
import socket


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowSolver 数值后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help="0 = 自动选择空闲端口")
    parser.add_argument("--log-level", default="warning")
    parser.add_argument("--print-port", action="store_true",
                        help="绑定成功后向 stdout 打印端口")
    args = parser.parse_args(argv)

    port = args.port or _free_port(args.host)
    if args.print_port:
        print(port, flush=True)

    import uvicorn
    uvicorn.run("backend.app:app", host=args.host, port=port,
                log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
