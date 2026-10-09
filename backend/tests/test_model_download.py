import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from roxy_ai.model_download import DownloadManager, parse_source, plan_files


def test_parse_ollama_and_hf_links():
    assert parse_source("https://ollama.com/richardyoung/qwythos-9b-abliterated") == (
        "ollama", {"namespace": "richardyoung", "model": "qwythos-9b-abliterated", "tag": "latest"})
    assert parse_source("ollama.com/library/qwen3:8b")[1] == {"namespace": "library", "model": "qwen3", "tag": "8b"}
    assert parse_source("https://ollama.com/qwen3:4b/tags")[1]["tag"] == "4b"
    kind, info = parse_source("https://huggingface.co/u/repo/blob/main/m-Q4_K_M.gguf")
    assert kind == "hf" and info["url"] == "https://huggingface.co/u/repo/resolve/main/m-Q4_K_M.gguf"
    for bad in ["https://example.com/x.gguf", "https://huggingface.co/u/repo", "https://ollama.com/"]:
        with pytest.raises(ValueError):
            parse_source(bad)


def _registry(blob: bytes, with_projector=False):
    digest = hashlib.sha256(blob).hexdigest()
    layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{digest}", "size": len(blob)},
              {"mediaType": "application/vnd.ollama.image.params", "digest": "sha256:00", "size": 3}]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/manifests/" in request.url.path:
            return httpx.Response(200, json={"layers": layers})
        if request.url.path.endswith(digest):
            start = int(request.headers.get("range", "bytes=0-")[6:].rstrip("-") or 0)
            return httpx.Response(206 if start else 200, content=blob[start:])
        return httpx.Response(404)

    return handler


def test_ollama_download_resumes_and_verifies(tmp_path, monkeypatch):
    blob = b"GGUF" + bytes(range(256)) * 100
    handler = _registry(blob)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def go():
        # A partial file from an interrupted earlier attempt.
        async with real(transport=httpx.MockTransport(handler)) as c:
            _, plans = await plan_files("https://ollama.com/u/m:q4", tmp_path, c)
        plans[0].dest.parent.mkdir(parents=True)
        Path(str(plans[0].dest) + ".part").write_bytes(blob[:1000])
        done = []
        mgr = DownloadManager(tmp_path, on_done=lambda: done.append(True))
        job = mgr.start("https://ollama.com/u/m:q4")
        await mgr._tasks[job["id"]]
        return mgr.jobs[job["id"]], done, plans[0].dest

    job, done, dest = asyncio.run(go())
    assert job.status == "done", job.error
    assert dest.read_bytes() == blob and dest.name == "m-q4.gguf" and done == [True]
    assert job.done == job.total == len(blob)


def test_ollama_download_rejects_corrupt_blob(tmp_path, monkeypatch):
    good = b"GGUF-good"
    handler = _registry(good)

    def corrupt(request):
        r = handler(request)
        return httpx.Response(200, content=b"GGUF-evil") if "/blobs/" in request.url.path else r

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(corrupt), **kw))

    async def go():
        mgr = DownloadManager(tmp_path)
        job = mgr.start("https://ollama.com/u/m")
        await mgr._tasks[job["id"]]
        return mgr.jobs[job["id"]]

    job = asyncio.run(go())
    assert job.status == "error" and "sha256" in job.error
    assert not list(tmp_path.rglob("*.gguf"))


def test_download_api_validates_source(tmp_path):
    import json as _json
    from fastapi.testclient import TestClient
    from roxy_ai.api import create_app
    from roxy_ai.config import Paths

    (tmp_path / "settings.json").write_text(_json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    with TestClient(create_app(Paths(tmp_path), "t")) as c:
        h = {"Authorization": "Bearer t"}
        r = c.post("/api/local-models/downloads", json={"source": "https://example.com/x"}, headers=h)
        assert r.status_code == 400 and "ollama.com" in r.json()["detail"]
        assert c.get("/api/local-models/downloads", headers=h).json() == []
