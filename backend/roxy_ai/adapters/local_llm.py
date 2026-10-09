"""Local LLMs through Codex's agent loop.

Runtimes:
- builtin (default): the app runs llama.cpp's `llama-server` itself on GGUF
  files found on the PC. No LM Studio / Ollama needed.
- lmstudio / ollama: use an already running local server.

Codex (`codex app-server`) drives the model, so local runs get the same
streaming, approvals, interrupt and direct Blender MCP use as the cloud AIs.
Requests go through LocalProxy (see local_proxy.py), which converts Codex's
namespaced MCP tools into plain functions local servers understand.
Nothing leaves the PC and no login is needed.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from ..local_proxy import LocalProxy
from ..local_runtime import (LlamaServer, LocalModel, OffloadSettings, default_model_dirs, find_llama_server,
                             find_models)
from ..procs import run_capture
from .base import AdapterUnavailable, ModelInfo, ProviderStatus, RunRequest, StatusUpdate
from .codex import CodexAdapter

RUNTIMES = {
    # id: (display name, default base URL, how to start it)
    "builtin": ("内蔵 llama.cpp", "", ""),
    "lmstudio": ("LM Studio", "http://127.0.0.1:1234",
                 "LM Studio を起動し、Developer タブでサーバーを開始してください（または設定の「サーバーを起動」）。"),
    "ollama": ("Ollama", "http://127.0.0.1:11434", "Ollama アプリを起動してください（または `ollama serve`）。"),
}
PROVIDER_ID = "roxy-local"
LLAMA_HINT = ("llama.cpp（llama-server）が見つかりません。PowerShell で `winget install ggml.llamacpp` を実行するか、"
              "設定で llama-server.exe の場所を指定してください。")


class LocalLLMAdapter(CodexAdapter):
    id = "local"
    name = "ローカルLLM"

    def __init__(self, settings, data_root: Path, log_dir: Path):
        super().__init__(settings)
        self._data_root = data_root
        self.llama = LlamaServer(data_root, log_dir)
        self.proxy = LocalProxy()
        self._proxy_base = ""
        self._token = ""
        self._local: dict[str, LocalModel] = {}

    def attach_proxy(self, base_url: str, token: str) -> None:
        """Called by the API once the backend's address is known."""
        self._proxy_base, self._token = base_url, token

    def _conf(self) -> dict:
        # Codex CLI path, sandbox and approval policy are shared with the Codex settings.
        providers = self._settings.get()["providers"]
        return {**providers.get("codex", {}), **providers.get("local", {})}

    def _runtime(self) -> tuple[str, str, str, str]:
        conf = self._conf()
        rid = conf.get("runtime") or "builtin"
        if rid not in RUNTIMES:
            rid = "builtin"
        name, default_url, hint = RUNTIMES[rid]
        base = (conf.get("base_url") or default_url).rstrip("/")
        return rid, name, base, hint

    def models_dir(self) -> Path:
        """Where downloads go (also searched first). Defaults to <data>/models."""
        custom = (self._conf().get("models_dir") or "").strip()
        return Path(custom) if custom else self._data_root / "models"

    def _model_dirs(self) -> list[Path]:
        conf = self._conf()
        extra = [Path(d) for d in conf.get("model_dirs") or [] if d]
        defaults = [] if (conf.get("models_dir") or "").strip() else default_model_dirs(self._data_root)
        dirs = [self.models_dir(), *extra, *defaults]
        return list(dict.fromkeys(dirs))

    # Status / models ------------------------------------------------------------------

    async def _external_models(self, rid: str, base: str) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=5) as client:
            if rid == "lmstudio":
                try:
                    r = await client.get(f"{base}/api/v0/models")
                    r.raise_for_status()
                    rows = [m for m in r.json().get("data", []) if m.get("type") in ("llm", "vlm")]
                    rows.sort(key=lambda m: m.get("state") != "loaded")  # loaded models first
                    return [ModelInfo(id=m["id"], name=m["id"] + ("（読み込み済み）" if m.get("state") == "loaded" else ""),
                                      description=f"{m.get('arch', '')} {m.get('quantization', '')}".strip(),
                                      supports_images=m.get("type") == "vlm") for m in rows]
                except (httpx.HTTPError, ValueError, KeyError):
                    pass  # older LM Studio: fall back to the OpenAI-compatible list
                r = await client.get(f"{base}/v1/models")
                r.raise_for_status()
                return [ModelInfo(id=m["id"], name=m["id"], supports_images=False)
                        for m in r.json().get("data", []) if "embed" not in m["id"].lower()]
            r = await client.get(f"{base}/api/tags")
            r.raise_for_status()
            out = []
            for m in r.json().get("models", []):
                caps: list[str] = []
                try:
                    show = await client.post(f"{base}/api/show", json={"model": m["name"]})
                    caps = show.json().get("capabilities") or []
                except (httpx.HTTPError, ValueError):
                    pass
                if caps and "completion" not in caps:
                    continue  # embedding-only
                out.append(ModelInfo(id=m["name"], name=m["name"], supports_images="vision" in caps))
            return out

    async def status(self, refresh: bool = False) -> ProviderStatus:
        if self._status and not refresh and time.time() - self._status_at < 60:
            return self._status
        rid, rname, base, hint = self._runtime()
        st = ProviderStatus(id=self.id, name=self.name, available=False, image_mimes=sorted(self.image_mimes()))
        st.auth_ok = True  # local models need no login
        if rid == "builtin":
            exe = find_llama_server(self._conf().get("llama_server_path") or "")
            models = find_models(self._model_dirs())
            self._local = {m.id: m for m in models}
            loaded = self.llama.model_id if self.llama.running else ""
            st.account = f"{rname}" + (f"（読み込み中のモデル: {loaded}）" if loaded else "")
            st.models = [ModelInfo(id=m.id, name=f"{m.id}（{m.size / 1e9:.1f}GB）" + ("・読み込み済み" if m.id == loaded else ""),
                                   description=m.path, supports_images=bool(m.mmproj)) for m in models]
            if not exe:
                st.detail = LLAMA_HINT
            elif not models:
                st.detail = ("GGUF 形式のモデルが見つかりません。モデルファイル（.gguf）を "
                             f"{self._data_root / 'models'} に置くか、設定でモデルのフォルダを追加してください。")
            else:
                st.available = True
        else:
            st.account = f"{rname}（{base}）"
            try:
                st.models = await self._external_models(rid, base)
                st.available = True
                if not st.models:
                    st.detail = f"{rname} にチャット用のモデルがありません。"
            except httpx.HTTPError:
                st.detail = f"{rname} のサーバー（{base}）に接続できません。{hint}"
        if st.available:
            st.version = await self._version()
            if not st.version:
                st.available = False
                st.detail = "ローカルLLMの実行には Codex CLI が必要です（npm i -g @openai/codex）。"
        st.supports_images = any(m.supports_images for m in st.models)
        self._models = [{"id": m.id, "isDefault": False, "inputModalities": ["text", "image"] if m.supports_images else ["text"]}
                        for m in st.models]
        self._status, self._status_at = st, time.time()
        return st

    def model_supports_images(self, model: str) -> bool:
        return any(m["id"] == model and "image" in m["inputModalities"] for m in self._models)

    # Actions ("login" is the per-provider action endpoint) ------------------------------

    async def login(self, method: str, api_key: str | None = None) -> dict:
        rid, rname, _, hint = self._runtime()
        if method == "unload":
            await self.llama.stop()
            self._status = None
            return {"ok": True}
        if method != "start_server":
            raise AdapterUnavailable("ローカルLLMにログインは不要です。")
        if rid != "lmstudio":
            raise AdapterUnavailable(hint or "内蔵ランタイムは送信時に自動で起動します。")
        lms = shutil.which("lms") or str(Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms"))
        if not Path(lms).is_file() and not shutil.which("lms"):
            raise AdapterUnavailable("LM Studio の CLI（lms）が見つかりません。")
        _, out = await run_capture([lms, "server", "start"], timeout=120)
        # `lms` exits 0 even when it fails, so judge by whether the server answers.
        st = await self.status(refresh=True)
        if not st.available:
            last = out.strip().splitlines()[-1] if out.strip() else ""
            return {"ok": False, "error": f"{rname} のサーバーを起動できませんでした。LM Studio アプリを起動してから再試行してください。{last}"}
        return {"ok": True}

    async def logout(self) -> dict:
        raise AdapterUnavailable("ローカルLLMにログアウトはありません。")

    async def usage(self, refresh: bool = False):
        return None  # local models have no plan quota

    # Running ----------------------------------------------------------------------------

    def _ctx(self) -> int:
        return int(self._conf().get("ctx_size") or 32768)

    def _thread_params(self, req: RunRequest) -> dict:
        params = super()._thread_params(req)
        config = params.setdefault("config", {})
        # Local models are small: keep the tools to what the task needs.
        config.setdefault("features", {})["plugins"] = False
        config["web_search"] = "disabled"
        config["model_providers"] = {
            PROVIDER_ID: {
                "name": "Roxy local",
                "base_url": self._proxy_base,
                "wire_api": "responses",
                "http_headers": {"Authorization": f"Bearer {self._token}"},
                "request_max_retries": 1,
                "stream_max_retries": 1,
            }
        }
        if self._runtime()[0] == "builtin":
            ctx = self._ctx()
            config["model_context_window"] = ctx
            config["model_auto_compact_token_limit"] = int(ctx * 0.8)
        params["modelProvider"] = PROVIDER_ID
        if req.mcp_servers:
            # Small local models tend to try a shell command named "blender" instead.
            names = ", ".join(f"mcp__{n.replace('-', '_')}__*" for n in req.mcp_servers)
            hint = (f"Blender の確認・操作には必ず関数ツール（{names}、例: mcp__blender__get_scene_info）を直接呼び出してください。"
                    "シェル（exec_command）で blender や MCP のコマンドを実行してはいけません。")
            params["developerInstructions"] = (params.get("developerInstructions") or "") + "\n" + hint
        return params

    async def run(self, req: RunRequest, emit: Any, approve: Any):
        if not self._proxy_base:
            raise AdapterUnavailable("ローカルLLMの中継が初期化されていません。アプリを再起動してください。")
        rid, rname, base, _ = self._runtime()
        # Check first: with no server, Codex would keep retrying instead of failing.
        st = await self.status(refresh=True)
        if not st.available or not st.models:
            raise AdapterUnavailable(st.detail or "ローカルLLMのモデルがありません。")
        if not req.model:
            req.model = st.models[0].id
        elif not any(m.id == req.model for m in st.models):
            raise AdapterUnavailable(f"モデル「{req.model}」が見つかりません。モデル一覧を更新して選び直してください。")
        if rid == "builtin":
            conf = self._conf()
            exe = find_llama_server(conf.get("llama_server_path") or "")

            async def on_status(text: str) -> None:
                await emit(StatusUpdate(text))

            try:
                offload = OffloadSettings(
                    gpu_layers=int(conf.get("gpu_layers", 99)),
                    cpu_layers=int(conf.get("cpu_layers", 0) or 0),
                    n_cpu_moe=int(conf.get("n_cpu_moe", 0) or 0),
                    threads=int(conf.get("threads", 0) or 0),
                    kv_on_cpu=bool(conf.get("kv_on_cpu", False)),
                )
                base = await self.llama.ensure(exe, self._local[req.model], self._ctx(), offload, on_status=on_status)
            except RuntimeError as e:
                raise AdapterUnavailable(str(e))
            self.llama.start_idle_watch(lambda: int(self._conf().get("idle_unload_minutes", 15)))
            self._status = None  # "loaded" marker changed
        self.proxy.upstream = base
        # Only our MCP servers (the Blender MCP) are shown to the local model.
        self.proxy.keep_namespaces = {f"mcp__{name}".replace("-", "_") for name in req.mcp_servers}
        # Small models reach for the shell instead of the MCP tools; hide it unless allowed.
        allow_shell = bool(self._conf().get("allow_shell", False))
        self.proxy.drop_functions = set() if allow_shell else {"exec_command", "write_stdin"}
        log_path = self.llama._log_dir / "llama-server.log"
        try:
            log_offset = log_path.stat().st_size if rid == "builtin" else 0
        except OSError:
            log_offset = 0
        try:
            result = await super().run(req, emit, approve)
            if rid == "builtin" and result.status == "error":
                try:
                    with log_path.open("rb") as log:
                        log.seek(log_offset)
                        failed_log = log.read()
                    if b"ErrorOutOfDeviceMemory" in failed_log:
                        result.error = ("GPUメモリが不足したため、ローカルモデルの応答が中断されました。"
                                        "設定 → AI → ローカルLLMで「KVキャッシュをメインメモリに置く」を有効にするか、"
                                        "CPUに置く層を増やす・コンテキスト長を小さくして再試行してください。")
                except OSError:
                    pass
            return result
        finally:
            self.llama.last_used = time.time()

    def max_image_edge(self) -> int:
        return 1024  # local vision models are small; keep images light

    async def shutdown(self) -> None:
        await super().shutdown()
        await self.llama.stop()
