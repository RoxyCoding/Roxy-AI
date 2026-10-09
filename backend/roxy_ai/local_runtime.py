"""Built-in local model runtime: llama.cpp's `llama-server` managed by the app.

No LM Studio or Ollama needed: the app finds GGUF model files on the PC,
starts `llama-server` with the chosen one (GPU offload, context size, vision
projector), and stops it when idle to free VRAM.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .procs import NO_WINDOW

logger = logging.getLogger(__name__)


# GGUF metadata ------------------------------------------------------------------------

_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def read_gguf_values(path: str | Path, wanted: set[str] | None = None,
                     want=lambda key: False) -> dict[str, object]:
    """Read string/number values from a GGUF header (arrays are skipped).

    Keys are taken if listed in `wanted` or accepted by `want(key)`.
    """
    found: dict[str, object] = {}
    wanted = wanted or set()
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            return found
        (version,) = struct.unpack("<I", f.read(4))
        if version < 2:
            return found
        _n_tensors, n_kv = struct.unpack("<QQ", f.read(16))

        def read_str() -> bytes:
            (n,) = struct.unpack("<Q", f.read(8))
            return f.read(n)

        def skip(vtype: int) -> None:
            if vtype in _SCALAR:
                f.seek(struct.calcsize(_SCALAR[vtype]), 1)
            elif vtype == 8:
                (n,) = struct.unpack("<Q", f.read(8))
                f.seek(n, 1)
            elif vtype == 9:
                etype, count = struct.unpack("<IQ", f.read(12))
                if etype in _SCALAR:
                    f.seek(struct.calcsize(_SCALAR[etype]) * count, 1)
                else:
                    for _ in range(count):
                        skip(etype)
            else:
                raise ValueError(f"unknown GGUF type {vtype}")

        for _ in range(n_kv):
            key = read_str().decode("utf-8", "replace")
            (vtype,) = struct.unpack("<I", f.read(4))
            if (key in wanted or want(key)) and vtype == 8:
                found[key] = read_str().decode("utf-8", "replace")
            elif (key in wanted or want(key)) and vtype in _SCALAR:
                (found[key],) = struct.unpack(_SCALAR[vtype], f.read(struct.calcsize(_SCALAR[vtype])))
            else:
                skip(vtype)
    return found


def read_gguf_string(path: str | Path, wanted: str) -> str | None:
    """Read one string value (e.g. tokenizer.chat_template) from a GGUF header."""
    value = read_gguf_values(path, {wanted}).get(wanted)
    return value if isinstance(value, str) else None


def gguf_layer_count(path: str | Path) -> int | None:
    """Number of transformer blocks (`<arch>.block_count`), used for CPU offload."""
    values = read_gguf_values(path, {"general.architecture"}, want=lambda k: k.endswith(".block_count"))
    arch = values.get("general.architecture")
    count = values.get(f"{arch}.block_count") if arch else None
    return int(count) if isinstance(count, int) else None


@dataclass
class OffloadSettings:
    """How the model is split between GPU and CPU. All values are user-chosen."""

    gpu_layers: int = 99  # layers on the GPU (99 = all)
    cpu_layers: int = 0  # if > 0: keep this many layers on the CPU instead (overrides gpu_layers)
    n_cpu_moe: int = 0  # MoE expert weights of the first N layers on the CPU (--n-cpu-moe)
    threads: int = 0  # CPU threads, 0 = llama.cpp default
    kv_on_cpu: bool = False  # keep the KV cache in system RAM (--no-kv-offload)


def offload_args(off: OffloadSettings, layer_count: int | None) -> list[str]:
    """llama-server arguments for the GPU/CPU split."""
    ngl = off.gpu_layers
    if off.cpu_layers > 0:
        # llama.cpp counts GPU layers (+1 for the output layer), so derive it from the model.
        total = (layer_count or 0) + 1
        ngl = max(0, total - off.cpu_layers) if layer_count else 0
    args = ["-ngl", str(max(0, ngl))]
    if off.n_cpu_moe > 0:
        args += ["--n-cpu-moe", str(off.n_cpu_moe)]
    if off.threads > 0:
        args += ["--threads", str(off.threads)]
    if off.kv_on_cpu:
        args.append("--no-kv-offload")
    return args


_SYSTEM_RAISE = re.compile(r"\{\{-?\s*raise_exception\(\s*['\"]System message must be at the beginning\.?['\"]\s*\)\s*-?\}\}")


def patch_chat_template(template: str) -> str | None:
    """Make a ChatML template accept system messages after the first turn.

    Codex sends developer/system messages mid-conversation; templates such as
    Qwen's reject that. Render them as ordinary system turns instead.
    Returns None when no change is needed.
    """
    if "<|im_start|>" not in template or not _SYSTEM_RAISE.search(template):
        return None
    turn = "{{- '<|im_start|>system\\n' + content + '<|im_end|>\\n' }}"
    return _SYSTEM_RAISE.sub(lambda _: turn, template)


# Model discovery ----------------------------------------------------------------------


@dataclass
class LocalModel:
    id: str
    path: str
    mmproj: str | None
    size: int


def default_model_dirs(data_root: Path) -> list[Path]:
    home = Path.home()
    return [data_root / "models", home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models",
            home / ".cache" / "llama.cpp"]


def find_models(dirs: list[Path]) -> list[LocalModel]:
    found: dict[str, LocalModel] = {}
    for root in dirs:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.gguf")):
            name = p.name.lower()
            if name.startswith("mmproj") or "-mmproj" in name:
                continue
            if re.search(r"-0000[2-9]-of-\d+\.gguf$", name):
                continue  # later shards of a split model; llama.cpp loads them from the first
            proj = sorted(q for q in p.parent.glob("*.gguf") if q.name.lower().startswith("mmproj") or "-mmproj" in q.name.lower())
            mid = p.stem
            if mid in found and found[mid].path != str(p):
                mid = f"{p.parent.name}/{p.stem}"
            found[mid] = LocalModel(mid, str(p), str(proj[0]) if proj else None, p.stat().st_size)
    return list(found.values())


def find_llama_server(configured: str = "") -> str | None:
    if configured:
        return configured if Path(configured).is_file() else shutil.which(configured)
    exe = shutil.which("llama-server")
    if exe:
        return exe
    if os.name == "nt":  # `winget install ggml.llamacpp` does not always put it on PATH for GUI apps
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        hits = glob.glob(str(base / "ggml.llamacpp*" / "llama-server.exe"))
        if hits:
            return hits[0]
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Server process -----------------------------------------------------------------------


class LlamaServer:
    def __init__(self, data_root: Path, log_dir: Path):
        self._templates = data_root / "llama" / "templates"
        self._log_dir = log_dir
        self._proc: subprocess.Popen | None = None
        self._key: tuple | None = None
        self.port = 0
        self.model_id = ""
        self.last_used = 0.0
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _template_file(self, model: LocalModel) -> str | None:
        """Write a patched copy of the model's chat template if it needs one."""
        try:
            template = read_gguf_string(model.path, "tokenizer.chat_template")
        except (OSError, ValueError, struct.error) as e:
            logger.warning("Could not read chat template from %s: %s", model.path, e)
            return None
        patched = patch_chat_template(template or "")
        if not patched:
            return None
        self._templates.mkdir(parents=True, exist_ok=True)
        out = self._templates / f"{hashlib.sha1(model.path.encode()).hexdigest()[:12]}.jinja"
        out.write_text(patched, encoding="utf-8")
        return str(out)

    async def ensure(self, exe: str, model: LocalModel, ctx: int, offload: OffloadSettings, on_status=None) -> str:
        """Start (or switch to) `model`; returns the server base URL once it is ready."""
        async with self._lock:
            key = (exe, model.path, model.mmproj, ctx, offload)
            if self.running and self._key == key:
                self.last_used = time.time()
                return self.base_url
            await self._stop_locked()
            if on_status:
                await on_status(f"ローカルモデル「{model.id}」を読み込み中…（初回は数十秒かかります）")
            self.port = _free_port()
            layers = await asyncio.to_thread(gguf_layer_count, model.path) if offload.cpu_layers > 0 else None
            if offload.cpu_layers > 0 and not layers:
                raise RuntimeError("モデルの層の数を読み取れないため、「CPUに置く層の数」を使えません。"
                                   "「GPUに載せる層の数」で指定してください。")
            args = [exe, "-m", model.path, "--host", "127.0.0.1", "--port", str(self.port),
                    "-c", str(ctx), *offload_args(offload, layers), "--jinja", "-a", model.id,
                    # Tool calls must come out as calls, not inside hidden "thinking" text.
                    "--reasoning", "off"]
            if model.mmproj:
                args += ["--mmproj", model.mmproj]
            template = await asyncio.to_thread(self._template_file, model)
            if template:
                args += ["--chat-template-file", template]
            log = open(self._log_dir / "llama-server.log", "a", encoding="utf-8")
            log.write(f"\n=== {time.ctime()} {' '.join(args)}\n")
            log.flush()
            self._proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                          creationflags=NO_WINDOW)
            log.close()
            self._key, self.model_id = key, model.id
            try:
                await self._wait_ready()
            except Exception:
                await self._stop_locked()
                raise
            self.last_used = time.time()
            return self.base_url

    async def _wait_ready(self, timeout: float = 300) -> None:
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=3) as client:
            while time.time() < deadline:
                if not self.running:
                    raise RuntimeError("llama-server が起動中に終了しました（モデルが大きすぎるか、ファイルが壊れている可能性）。"
                                       f"ログ: {self._log_dir / 'llama-server.log'}")
                try:
                    r = await client.get(f"{self.base_url}/health")
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError("ローカルモデルの読み込みがタイムアウトしました。")

    def start_idle_watch(self, minutes_fn) -> None:
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop(minutes_fn))

    async def _idle_loop(self, minutes_fn) -> None:
        while True:
            await asyncio.sleep(30)
            minutes = minutes_fn()
            if self.running and minutes > 0 and time.time() - self.last_used > minutes * 60:
                logger.info("Unloading idle local model %s", self.model_id)
                await self.stop()

    async def _stop_locked(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    await asyncio.to_thread(self._proc.wait, 10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
        self._key = None
        self.model_id = ""

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
