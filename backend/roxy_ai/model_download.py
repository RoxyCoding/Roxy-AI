"""Download GGUF models for the built-in local runtime.

Sources:
- Ollama library links: https://ollama.com/<user>/<model>[:tag] (or <model> for
  official models). The Ollama registry stores the weights as a plain GGUF
  blob, so no Ollama install is needed; a vision projector layer, if any, is
  saved next to it as mmproj-*.gguf.
- Hugging Face file links ending in .gguf (blob/ or resolve/ URLs).

Files go to <data>/models/<folder>/, are written as *.part, resume after an
interruption, and Ollama blobs are verified against their sha256 digest.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

logger = logging.getLogger(__name__)

OLLAMA_REGISTRY = "https://registry.ollama.ai/v2"
MODEL_MEDIA = "application/vnd.ollama.image.model"
PROJECTOR_MEDIA = "application/vnd.ollama.image.projector"


@dataclass
class FilePlan:
    url: str
    dest: Path
    size: int = 0
    sha256: str = ""  # hex digest to verify, if known


@dataclass
class Download:
    id: str
    source: str
    name: str
    status: str = "preparing"  # preparing | downloading | verifying | done | error | cancelled
    total: int = 0
    done: int = 0
    error: str = ""
    path: str = ""
    started_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return asdict(self)


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-.") or "model"


def parse_source(source: str) -> tuple[str, dict]:
    """Return (kind, info) for an Ollama or Hugging Face model link."""
    s = source.strip()
    u = urlparse(s if "://" in s else f"https://{s}")
    host = u.netloc.lower()
    if host.endswith("ollama.com") or host.endswith("ollama.ai"):
        path = u.path.strip("/")
        if path.startswith("library/"):
            path = path[len("library/"):]
        path = path.split("/tags")[0]
        name, _, tag = path.partition(":")
        parts = [p for p in name.split("/") if p]
        if not parts or len(parts) > 2:
            raise ValueError("Ollama のモデルURLを認識できません（例: https://ollama.com/<user>/<model>）")
        ns, model = (parts[0], parts[1]) if len(parts) == 2 else ("library", parts[0])
        return "ollama", {"namespace": ns, "model": model, "tag": tag or "latest"}
    if host.endswith("huggingface.co") or host.endswith("hf.co"):
        path = u.path
        if not path.lower().endswith(".gguf"):
            raise ValueError("Hugging Face は .gguf ファイルへのリンクを指定してください（ファイル一覧の各ファイルのリンク）")
        path = path.replace("/blob/", "/resolve/", 1)
        if "/resolve/" not in path:
            raise ValueError("Hugging Face の .gguf ファイルのリンク（.../blob/... または .../resolve/...）を指定してください")
        repo = path.strip("/").split("/resolve/")[0]
        return "hf", {"url": f"https://huggingface.co{path}", "repo": repo, "file": unquote(path.rsplit("/", 1)[-1])}
    raise ValueError("対応しているのは ollama.com と huggingface.co のモデルリンクです")


async def plan_files(source: str, models_dir: Path, client: httpx.AsyncClient) -> tuple[str, list[FilePlan]]:
    kind, info = parse_source(source)
    if kind == "ollama":
        ns, model, tag = info["namespace"], info["model"], info["tag"]
        r = await client.get(f"{OLLAMA_REGISTRY}/{ns}/{model}/manifests/{tag}",
                             headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        if r.status_code == 404:
            raise ValueError(f"Ollama にモデル「{ns}/{model}:{tag}」が見つかりません")
        r.raise_for_status()
        layers = r.json().get("layers", [])
        weights = [l for l in layers if l.get("mediaType") == MODEL_MEDIA]
        if not weights:
            raise ValueError("このモデルにはダウンロードできる重み（GGUF）がありません")
        name = f"{model}-{tag}" if ns == "library" else f"{ns}-{model}-{tag}"
        folder = models_dir / _safe(name)
        plans = []
        for layer, filename in [(weights[0], f"{_safe(model)}-{_safe(tag)}.gguf")] + [
            (l, f"mmproj-{_safe(model)}.gguf") for l in layers if l.get("mediaType") == PROJECTOR_MEDIA
        ][:1]:
            digest = layer["digest"]
            plans.append(FilePlan(f"{OLLAMA_REGISTRY}/{ns}/{model}/blobs/{digest}", folder / filename,
                                  int(layer.get("size", 0)), digest.split(":", 1)[1] if digest.startswith("sha256:") else ""))
        return name, plans
    folder = models_dir / _safe(info["repo"].replace("/", "-"))
    return info["file"], [FilePlan(info["url"], folder / _safe(info["file"]))]


class DownloadManager:
    def __init__(self, models_dir, on_done=None):
        # A Path, or a callable returning the current download folder (it is a setting).
        self._models_dir = models_dir if callable(models_dir) else (lambda: models_dir)
        self.jobs: dict[str, Download] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._on_done = on_done

    def list(self) -> list[dict]:
        return [j.public() for j in sorted(self.jobs.values(), key=lambda j: j.started_at, reverse=True)]

    def start(self, source: str) -> dict:
        parse_source(source)  # validate before starting (raises ValueError)
        for j in self.jobs.values():
            if j.source == source.strip() and j.status in ("preparing", "downloading", "verifying"):
                return j.public()
        job = Download(id=uuid.uuid4().hex, source=source.strip(), name=source.strip())
        self.jobs[job.id] = job
        self._tasks[job.id] = asyncio.create_task(self._run(job))
        return job.public()

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()

    async def _run(self, job: Download) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), follow_redirects=True) as client:
                job.name, plans = await plan_files(job.source, Path(self._models_dir()), client)
                job.total = sum(p.size for p in plans)
                job.status = "downloading"
                for plan in plans:
                    await self._fetch(client, plan, job)
                job.path = str(plans[0].dest)
                job.status = "done"
            if self._on_done:
                self._on_done()
        except asyncio.CancelledError:
            job.status = "cancelled"
        except (httpx.HTTPError, ValueError, OSError) as e:
            logger.warning("model download failed: %s", e)
            job.status, job.error = "error", str(e) or e.__class__.__name__

    async def _fetch(self, client: httpx.AsyncClient, plan: FilePlan, job: Download) -> None:
        plan.dest.parent.mkdir(parents=True, exist_ok=True)
        if plan.dest.is_file() and (not plan.size or plan.dest.stat().st_size == plan.size):
            job.done += plan.dest.stat().st_size
            return  # already downloaded
        part = plan.dest.with_suffix(plan.dest.suffix + ".part")
        have = part.stat().st_size if part.is_file() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        async with client.stream("GET", plan.url, headers=headers) as r:
            if r.status_code == 416:  # already complete
                have = part.stat().st_size
            else:
                r.raise_for_status()
                if have and r.status_code != 206:
                    have = 0  # server ignored Range: start over
                if not plan.size:
                    length = int(r.headers.get("content-length") or 0)
                    plan.size = have + length
                    job.total += plan.size
                job.done += have
                with open(part, "ab" if have else "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
                        job.done += len(chunk)
        if plan.sha256:
            job.status = "verifying"
            digest = await asyncio.to_thread(_sha256, part)
            if digest != plan.sha256:
                part.unlink(missing_ok=True)
                raise ValueError("ダウンロードしたファイルの検証（sha256）に失敗しました。再試行してください。")
            job.status = "downloading"
        part.replace(plan.dest)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()
