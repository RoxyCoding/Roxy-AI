"""Application log (rotating text) and operation log (JSON lines)."""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from collections import deque
from pathlib import Path

LOG_FILES = {"app": "app.log", "operations": "operations.jsonl", "mcp_monitor": "mcp_monitor.log"}

_ops = logging.getLogger("roxy.ops")


def setup_logging(log_dir: Path, debug: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = logging.handlers.RotatingFileHandler(log_dir / LOG_FILES["app"], maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    app.setFormatter(fmt)
    root.addHandler(app)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    root.addHandler(console)

    ops = logging.handlers.RotatingFileHandler(log_dir / LOG_FILES["operations"], maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    ops.setFormatter(logging.Formatter("%(message)s"))
    _ops.addHandler(ops)
    _ops.propagate = False
    _ops.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "mcp.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def op(event: str, **fields) -> None:
    """Record one user-visible operation (run, tool call, approval, error ...)."""
    _ops.info(json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False, default=str))


def tail(log_dir: Path, name: str, lines: int = 200) -> list[str]:
    fname = LOG_FILES.get(name)
    if not fname:
        raise KeyError(name)
    path = log_dir / fname
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n") for line in deque(f, maxlen=max(1, min(lines, 2000)))]
