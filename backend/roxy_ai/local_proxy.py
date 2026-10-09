"""Responses-API translation between Codex and local model servers.

Codex sends MCP tools (e.g. the Blender MCP) as `namespace` tool groups and
expects calls back as `function_call` items with separate `name` and
`namespace`. Local servers (llama.cpp, LM Studio, Ollama) only understand
plain `function` tools and silently drop namespaces, so a local model would
never see the Blender tools. This module flattens namespaces on the way in
(`mcp__blender__` + `get_scene_info` -> `mcp__blender__get_scene_info`) and
splits the names again on the way out, including inside the SSE stream.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

# Tool types a local server can execute; the rest are Codex/OpenAI-hosted features.
UNSUPPORTED_TOOL_TYPES = {"web_search", "web_search_preview", "image_generation", "tool_search", "local_shell"}
# Codex bookkeeping tools a small local model only gets distracted by.
DROPPED_FUNCTIONS = {"get_goal", "create_goal", "update_goal"}


def flat_name(namespace: str, name: str) -> str:
    if not namespace:
        return name
    return f"{namespace}{name}" if namespace.endswith("_") else f"{namespace}__{name}"


def flatten_request(body: dict, keep_namespaces: set[str] | None = None) -> tuple[dict, dict[str, tuple[str, str]]]:
    """Return (request for the local server, flat name -> (namespace, name)).

    `keep_namespaces` limits which tool groups reach the model (e.g. only the
    Blender MCP): Codex also attaches every MCP server and app from the user's
    global setup, far more than a local model's context can hold.
    """
    mapping: dict[str, tuple[str, str]] = {}
    tools: list[dict] = []
    for tool in body.get("tools") or []:
        ttype = tool.get("type")
        if ttype == "namespace":
            ns = tool.get("name") or tool.get("namespace") or ""
            if keep_namespaces is not None and ns.rstrip("_") not in keep_namespaces:
                continue
            for sub in tool.get("tools") or []:
                if sub.get("type", "function") != "function":
                    continue
                full = flat_name(ns, sub.get("name", ""))
                mapping[full] = (ns, sub.get("name", ""))
                desc = sub.get("description") or ""
                if tool.get("description") and not desc:
                    desc = tool["description"]
                tools.append({**sub, "type": "function", "name": full, "description": desc})
        elif ttype in UNSUPPORTED_TOOL_TYPES or tool.get("name") in DROPPED_FUNCTIONS:
            continue
        else:
            tools.append(tool)
    out = dict(body)
    if "tools" in body:
        out["tools"] = tools
    # Earlier calls in the history must use the same flat names the model sees.
    if isinstance(body.get("input"), list):
        fixed = []
        for item in body["input"]:
            if isinstance(item, dict) and item.get("type") == "function_call" and item.get("namespace"):
                full = flat_name(item["namespace"], item.get("name", ""))
                mapping.setdefault(full, (item["namespace"], item.get("name", "")))
                item = {k: v for k, v in item.items() if k != "namespace"}
                item["name"] = full
            fixed.append(item)
        out["input"] = fixed
    return out, mapping


def restore_names(obj: Any, mapping: dict[str, tuple[str, str]]) -> Any:
    """Split flattened names back into namespace + name anywhere in a response object."""
    if not mapping:
        return obj
    if isinstance(obj, dict):
        if obj.get("type") == "function_call" and obj.get("name") in mapping:
            ns, name = mapping[obj["name"]]
            obj = {**obj, "name": name, "namespace": ns}
        return {k: restore_names(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [restore_names(v, mapping) for v in obj]
    return obj


async def restore_stream(lines: AsyncIterator[str], mapping: dict[str, tuple[str, str]]) -> AsyncIterator[bytes]:
    """Rewrite `data:` lines of a Responses SSE stream."""
    async for line in lines:
        if line.startswith("data:") and mapping:
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    line = "data: " + json.dumps(restore_names(json.loads(payload), mapping), ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
        yield (line + "\n").encode("utf-8")


class LocalProxy:
    """Forwards /v1/* to the current local server, translating /responses."""

    def __init__(self) -> None:
        self.upstream = ""  # e.g. http://127.0.0.1:18080 (set by the local adapter)
        self.dump_dir = os.environ.get("ROXY_LOCAL_PROXY_DUMP") or ""
        self.keep_namespaces: set[str] | None = None  # e.g. {"mcp__blender"}; None = all
        self.drop_functions: set[str] = set()  # extra plain functions to hide (e.g. the shell)

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.api_route("/api/local-llm/v1/{path:path}", methods=["GET", "POST"])
        async def forward(path: str, request: Request):
            if not self.upstream:
                return JSONResponse({"error": {"message": "ローカルLLMのサーバーが起動していません"}}, status_code=503)
            url = f"{self.upstream}/v1/{path}"
            if request.method == "GET":
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(url, params=request.query_params)
                return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
            body = await request.json()
            mapping: dict[str, tuple[str, str]] = {}
            if path.rstrip("/") == "responses":
                if self.dump_dir:
                    with open(os.path.join(self.dump_dir, "local_request.json"), "w", encoding="utf-8") as f:
                        json.dump(body, f, ensure_ascii=False, indent=1)
                body, mapping = flatten_request(body, self.keep_namespaces)
                if self.drop_functions:
                    body["tools"] = [t for t in body.get("tools") or [] if t.get("name") not in self.drop_functions]
            client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))
            upstream = client.build_request("POST", url, json=body)
            resp = await client.send(upstream, stream=True)
            if not body.get("stream"):
                data = await resp.aread()
                await resp.aclose()
                await client.aclose()
                try:
                    content = json.dumps(restore_names(json.loads(data), mapping), ensure_ascii=False).encode()
                except json.JSONDecodeError:
                    content = data
                return Response(content, status_code=resp.status_code, media_type="application/json")

            async def stream():
                try:
                    async for chunk in restore_stream(resp.aiter_lines(), mapping):
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()

            return StreamingResponse(stream(), status_code=resp.status_code,
                                     media_type=resp.headers.get("content-type", "text/event-stream"))

        return router
