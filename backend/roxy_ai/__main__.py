"""Start the backend.

    python -m roxy_ai [--port 0] [--token TOKEN] [--parent-pid PID] [--debug]

Prints one line `ROXY_READY {"port": ..., "token": ...}` on stdout once it is
listening; the desktop shell reads it to connect. Without --token a random
token is generated per launch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import sys

import uvicorn

from .api import create_app
from .config import Paths, data_dir
from .logs import setup_logging
from .procs import install_kill_on_close_job, watch_parent


def main() -> None:
    parser = argparse.ArgumentParser(prog="roxy_ai")
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument("--token", default=os.environ.get("ROXY_AI_TOKEN") or "")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    paths = Paths(data_dir())
    setup_logging(paths.logs, args.debug)
    log = logging.getLogger("roxy_ai")
    if install_kill_on_close_job():
        log.info("Child processes are bound to this process (job object)")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    token = args.token or secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]

    app = create_app(paths, token)
    config = uvicorn.Config(app, log_level="info" if args.debug else "warning", ws_max_size=64 * 1024 * 1024)
    server = uvicorn.Server(config)

    if args.parent_pid:
        watch_parent(args.parent_pid, lambda: setattr(server, "should_exit", True))

    async def serve():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(0.05)
        if server.started:
            print("ROXY_READY " + json.dumps({"port": port, "token": token}), flush=True)
            log.info("Listening on 127.0.0.1:%d (data: %s)", port, paths.root)
        await task

    asyncio.run(serve())


if __name__ == "__main__":
    main()
