import asyncio
import json
import struct

import httpx
import pytest

from roxy_ai.adapters import local_llm
from roxy_ai.adapters.base import RunRequest
from roxy_ai.adapters.local_llm import LocalLLMAdapter
from roxy_ai.config import SettingsStore
from roxy_ai.local_proxy import flatten_request, restore_names, restore_stream
from roxy_ai.local_runtime import find_models, patch_chat_template, read_gguf_string


def _adapter(tmp_path, **local):
    s = SettingsStore(tmp_path / "settings.json")
    s.update({"providers": {"local": local}})
    a = LocalLLMAdapter(s, tmp_path, tmp_path)
    a.attach_proxy("http://127.0.0.1:9/api/local-llm/v1", "tok")
    return a


async def _fake_version(self):
    return "codex-cli test"


# Translation layer ------------------------------------------------------------------


def test_namespace_tools_are_flattened_and_restored():
    body = {
        "model": "m", "stream": True,
        "tools": [
            {"type": "function", "name": "exec_command", "parameters": {}},
            {"type": "namespace", "name": "mcp__blender__", "description": "Blender",
             "tools": [{"type": "function", "name": "get_scene_info", "description": "Facts", "parameters": {"type": "object"}}]},
            {"type": "web_search"},
        ],
        "input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "name": "look", "namespace": "mcp__blender__", "arguments": "{}", "call_id": "c1"},
        ],
    }
    out, mapping = flatten_request(body)
    assert [t["name"] for t in out["tools"]] == ["exec_command", "mcp__blender__get_scene_info"]
    assert out["input"][1]["name"] == "mcp__blender__look" and "namespace" not in out["input"][1]
    reply = {"output": [{"type": "function_call", "name": "mcp__blender__get_scene_info", "arguments": "{}"},
                        {"type": "function_call", "name": "exec_command"}]}
    fixed = restore_names(reply, mapping)
    assert fixed["output"][0] == {"type": "function_call", "name": "get_scene_info", "namespace": "mcp__blender__", "arguments": "{}"}
    assert fixed["output"][1] == {"type": "function_call", "name": "exec_command"}


def test_stream_lines_are_rewritten():
    mapping = {"mcp__blender__look": ("mcp__blender__", "look")}
    event = {"type": "response.output_item.done", "item": {"type": "function_call", "name": "mcp__blender__look"}}

    async def lines():
        for line in ["event: response.output_item.done", "data: " + json.dumps(event), "", "data: [DONE]"]:
            yield line

    async def collect():
        return [chunk.decode() async for chunk in restore_stream(lines(), mapping)]

    out = asyncio.run(collect())
    data = json.loads(out[1][6:])
    assert data["item"] == {"type": "function_call", "name": "look", "namespace": "mcp__blender__"}
    assert out[3].strip() == "data: [DONE]"


# Built-in runtime ---------------------------------------------------------------------


def _write_gguf(path, kv):
    def s(b):
        b = b.encode()
        return struct.pack("<Q", len(b)) + b
    data = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv))
    for key, value in kv:
        data += s(key)
        if isinstance(value, str):
            data += struct.pack("<I", 8) + s(value)
        elif isinstance(value, list):  # array of strings
            data += struct.pack("<IIQ", 9, 8, len(value)) + b"".join(s(v) for v in value)
        else:
            data += struct.pack("<I", 4) + struct.pack("<I", value)
    path.write_bytes(data)


def test_gguf_reader_and_template_patch(tmp_path):
    tmpl = ("{%- for message in messages %}{%- if message.role == \"system\" %}{%- if not loop.first %}"
            "{{- raise_exception('System message must be at the beginning.') }}{%- endif %}{%- endif %}"
            "<|im_start|>{%- endfor %}")
    model = tmp_path / "m.gguf"
    _write_gguf(model, [("general.architecture", "qwen"), ("tokenizer.ggml.tokens", ["a", "b"]),
                        ("some.count", 7), ("tokenizer.chat_template", tmpl)])
    assert read_gguf_string(model, "tokenizer.chat_template") == tmpl
    patched = patch_chat_template(tmpl)
    assert "raise_exception" not in patched and "<|im_start|>system" in patched
    assert patch_chat_template("{{ messages }}") is None


def test_model_discovery_pairs_mmproj_and_skips_shards(tmp_path):
    d = tmp_path / "models" / "a"
    d.mkdir(parents=True)
    for name in ["qwen-Q4.gguf", "mmproj-f16.gguf", "big-00001-of-00002.gguf", "big-00002-of-00002.gguf"]:
        (d / name).write_bytes(b"GGUF")
    models = {m.id: m for m in find_models([tmp_path / "models"])}
    assert set(models) == {"qwen-Q4", "big-00001-of-00002"}
    assert models["qwen-Q4"].mmproj.endswith("mmproj-f16.gguf")


def test_builtin_status_lists_gguf_models(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    (models / "qwen-9b.gguf").write_bytes(b"GGUF" + b"0" * 10)
    monkeypatch.setattr(local_llm, "find_llama_server", lambda configured="": "C:/llama-server.exe")
    monkeypatch.setattr(local_llm, "default_model_dirs", lambda root: [models])
    monkeypatch.setattr(LocalLLMAdapter, "_version", _fake_version)
    st = asyncio.run(_adapter(tmp_path).status(refresh=True))
    assert st.available and [m.id for m in st.models] == ["qwen-9b"] and not st.models[0].supports_images
    monkeypatch.setattr(local_llm, "find_llama_server", lambda configured="": None)
    st = asyncio.run(_adapter(tmp_path).status(refresh=True))
    assert not st.available and "winget install ggml.llamacpp" in st.detail


# External runtimes --------------------------------------------------------------------


@pytest.fixture()
def fake_server(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v0/models":
            return httpx.Response(200, json={"data": [
                {"id": "text-embedding-x", "type": "embeddings", "state": "not-loaded"},
                {"id": "qwen-9b", "type": "llm", "state": "not-loaded"},
                {"id": "qwen-vl", "type": "vlm", "state": "loaded"},
            ]})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(local_llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(LocalLLMAdapter, "_version", _fake_version)


def test_lmstudio_models_loaded_first(tmp_path, fake_server):
    st = asyncio.run(_adapter(tmp_path, runtime="lmstudio").status(refresh=True))
    assert st.available and [m.id for m in st.models] == ["qwen-vl", "qwen-9b"]
    assert st.models[0].supports_images and not st.models[1].supports_images


def test_external_server_down_is_reported(tmp_path, monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused")

    real = httpx.AsyncClient
    monkeypatch.setattr(local_llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(down), **kw))
    st = asyncio.run(_adapter(tmp_path, runtime="lmstudio").status(refresh=True))
    assert not st.available and "LM Studio" in st.detail


def test_threads_go_through_the_relay(tmp_path):
    req = RunRequest(conversation_id="c", parts=[], cwd=".", model="qwen-9b")
    params = _adapter(tmp_path)._thread_params(req)
    provider = params["config"]["model_providers"][local_llm.PROVIDER_ID]
    assert params["modelProvider"] == local_llm.PROVIDER_ID
    assert provider["base_url"] == "http://127.0.0.1:9/api/local-llm/v1"
    assert provider["http_headers"] == {"Authorization": "Bearer tok"} and provider["wire_api"] == "responses"
    assert params["config"]["model_context_window"] == 32768
    assert params["config"]["web_search"] == "disabled"


def test_only_kept_namespaces_reach_the_model():
    body = {"tools": [
        {"type": "function", "name": "exec_command"},
        {"type": "function", "name": "update_goal"},
        {"type": "namespace", "name": "mcp__blender", "tools": [{"type": "function", "name": "look"}]},
        {"type": "namespace", "name": "mcp__codex_apps__github", "tools": [{"type": "function", "name": "search"}]},
    ]}
    out, mapping = flatten_request(body, {"mcp__blender"})
    assert [t["name"] for t in out["tools"]] == ["exec_command", "mcp__blender__look"]
    assert mapping == {"mcp__blender__look": ("mcp__blender", "look")}


def test_cpu_offload_arguments(tmp_path):
    from roxy_ai.local_runtime import OffloadSettings, gguf_layer_count, offload_args

    model = tmp_path / "m.gguf"
    _write_gguf(model, [("general.architecture", "qwen3"), ("qwen3.block_count", 32)])
    assert gguf_layer_count(model) == 32
    assert offload_args(OffloadSettings(), 32) == ["-ngl", "99"]
    assert offload_args(OffloadSettings(gpu_layers=12), 32) == ["-ngl", "12"]
    # CPU layers win over gpu_layers: 33 offloadable (32 blocks + output) - 10 on the CPU.
    assert offload_args(OffloadSettings(gpu_layers=99, cpu_layers=10), 32) == ["-ngl", "23"]
    assert offload_args(OffloadSettings(cpu_layers=99), 32) == ["-ngl", "0"]
    assert offload_args(OffloadSettings(n_cpu_moe=6, threads=12, kv_on_cpu=True), 32) == [
        "-ngl", "99", "--n-cpu-moe", "6", "--threads", "12", "--no-kv-offload"]


def test_custom_models_dir_is_used_and_default_still_searched(tmp_path, monkeypatch):
    custom = tmp_path / "D-drive-models"
    a = _adapter(tmp_path, models_dir=str(custom))
    assert a.models_dir() == custom
    dirs = a._model_dirs()
    assert dirs[0] == custom and (tmp_path / "models") in dirs and len(dirs) == len(set(dirs))
    (tmp_path / "x").mkdir()
    assert _adapter(tmp_path / "x").models_dir() == tmp_path / "x" / "models"
