import json

import pytest
from PIL import Image

from roxy_ai.attachments import AttachmentStore, safe_name
from roxy_ai.context import plan_context, render_handoff
from roxy_ai.db import Database
from roxy_ai.mcp_monitor import mcp_server_command, parse_scene_header
from roxy_ai.adapters.codex import _mcp_result_payload, _tool_from_message
from roxy_ai.adapters.base import mcp_tool_parts


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_conversation_roundtrip_and_rewind(db):
    p = db.create_project("P", blender_port=9877)
    c = db.create_conversation(p["id"], "claude", "default")
    m1 = db.add_message(c["id"], "user", "立方体を作って")
    m2 = db.add_message(c["id"], "assistant", "作りました", provider="claude", tool_calls=[{"name": "blender.execute_blender_code"}])
    db.add_message(c["id"], "user", "色を青に")
    assert [m["seq"] for m in db.list_messages(c["id"])] == [1, 2, 3]
    assert db.get_message(m2["id"])["tool_calls"][0]["name"] == "blender.execute_blender_code"
    db.set_ai_session(c["id"], "claude", "sess-1", 2)
    db.delete_messages_from(c["id"], 2)
    assert [m["id"] for m in db.list_messages(c["id"])] == [m1["id"]]
    assert db.get_ai_session(c["id"], "claude")["session_id"] == "sess-1"
    db.clear_ai_sessions(c["id"])
    assert db.get_ai_session(c["id"], "claude") is None


def test_search_matches_title_and_content(db):
    p = db.create_project("P")
    c1 = db.create_conversation(p["id"], "claude", title="椅子のモデリング")
    c2 = db.create_conversation(p["id"], "codex", title="別件")
    db.add_message(c2["id"], "user", "テクスチャの椅子について")
    ids = {r["conversation_id"] for r in db.search("椅子")}
    assert ids == {c1["id"], c2["id"]}
    assert db.search("存在しない語") == []


def test_mark_interrupted_streams(db):
    p = db.create_project("P")
    c = db.create_conversation(p["id"], "claude")
    m = db.add_message(c["id"], "assistant", "途中", status="streaming")
    assert db.mark_interrupted_streams() == 1
    assert db.get_message(m["id"])["status"] == "error"


def _msgs():
    return [
        {"seq": 1, "role": "user", "content": "椅子を作って", "provider": "claude"},
        {"seq": 2, "role": "assistant", "content": "作りました", "provider": "claude", "tool_calls": [{"name": "blender.look"}]},
        {"seq": 3, "role": "user", "content": "脚を長く", "provider": "codex"},
        {"seq": 4, "role": "assistant", "content": "伸ばしました", "provider": "codex"},
        {"seq": 5, "role": "user", "content": "次は机", "provider": "claude"},
    ]


def test_plan_context_fresh_session_gets_everything():
    sid, ctx = plan_context(_msgs(), None, 5)
    assert sid is None and [m["seq"] for m in ctx] == [1, 2, 3, 4]


def test_plan_context_resumes_and_shares_only_missed():
    sid, ctx = plan_context(_msgs(), {"session_id": "s", "synced_seq": 2}, 5)
    assert sid == "s" and [m["seq"] for m in ctx] == [3, 4]


def test_plan_context_up_to_date_session_needs_nothing():
    sid, ctx = plan_context(_msgs()[:4] + [{"seq": 5, "role": "user", "content": "x"}], {"session_id": "s", "synced_seq": 4}, 5)
    assert sid == "s" and ctx == []


def test_render_handoff_labels_and_truncates():
    text = render_handoff(_msgs()[:4], fresh=True)
    assert "AI（Codex）" in text and "AI（Claude Code）" in text and "blender.look" in text
    assert "内部のセッションは引き継がれていません" in text or "引き継がれていません" in text
    short = render_handoff(_msgs()[:4], fresh=True, max_chars=20)
    assert "省略" in short
    assert render_handoff([], fresh=True) == ""


def test_scene_header_parsing():
    text = (
        "Scene 'Scene' | 1 CAMERA, 2 MESH | active Cube | selected Cube, Chair | mode OBJECT\n"
        "C:\\a.blend | engine BLENDER_EEVEE | frames 1-250 (now 1) at 24 fps | 1920x1080 | camera Camera | HDRI none | unit scale 1.0\n"
        "\nShowing 2 of 2 (name | type | location | size):\nCube | MESH | 0,0,0 | 2x2x2\nChair | MESH | 1,0,0 | 1x1x1\n"
    )
    h = parse_scene_header(text)
    assert h["active"] == "Cube" and h["selected"] == ["Cube", "Chair"] and h["mode"] == "OBJECT"
    assert h["columns"] == ["name", "type", "location", "size"] and len(h["rows"]) == 2
    assert "engine" in h["file_line"]
    assert parse_scene_header("Scene 'S' | empty | active none | selected none | mode OBJECT")["selected"] == []


def test_mcp_command_prefers_checkout_venv(tmp_path):
    exe = tmp_path / ".venv" / "Scripts" / "roxy-blender-mcp.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    s = {"mcp": {"server_dir": str(tmp_path), "command": "", "args": []}}
    cmd = mcp_server_command(s, "localhost", 9877)
    assert cmd["command"] == str(exe) and cmd["env"] == {"BLENDER_HOST": "localhost", "BLENDER_PORT": "9877"}
    s["mcp"]["server_dir"] = str(tmp_path / "missing")
    assert mcp_server_command(s, "h", 1)["args"][:2] == ["run", "--directory"]


def test_tool_parsers():
    assert mcp_tool_parts("mcp__blender__get_scene_info") == ("blender", "get_scene_info")
    assert mcp_tool_parts("Bash") == ("", "Bash")
    assert _tool_from_message('Allow "look"?') == "look"
    text, images = _mcp_result_payload({"content": [{"type": "text", "text": "ok"}, {"type": "image", "data": "aGk=", "mimeType": "image/png"}]})
    assert text == "ok" and images == [("image/png", b"hi")]


def test_attachment_prepare(tmp_path, db):
    store = AttachmentStore(db, tmp_path / "att")
    big = tmp_path / "x.bmp"
    Image.new("RGB", (3000, 1000), "skyblue").save(big)
    img = store.save_bytes(big.read_bytes(), "../../三面図.bmp", "image/bmp", "upload")
    assert safe_name("../../a:b.png") == "a_b.png" and "三面図.bmp" in img["path"]
    txt = store.save_bytes("print('hi')".encode(), "a.py", "text/x-python", "upload")
    blend = store.save_bytes(b"BLENDER", "scene.blend", "", "upload")
    prep = store.prepare([img, txt, blend], supports_images=True, mimes={"image/png", "image/jpeg"},
                         max_edge=1568, provider_name="Claude Code")
    kinds = [p.kind for p in prep.parts]
    assert kinds.count("image") == 1
    image_part = next(p for p in prep.parts if p.kind == "image")
    with Image.open(image_part.path) as im:
        assert max(im.size) == 1568 and image_part.mime == "image/png"
    assert any("print('hi')" in p.text for p in prep.parts)
    assert any("scene.blend" in n for n in prep.notices)
    no_img = store.prepare([img], supports_images=False, mimes=set(), max_edge=1, provider_name="X")
    assert all(p.kind == "text" for p in no_img.parts) and no_img.notices
    # Never serve files outside the attachment root.
    assert store.file_for({**img, "path": str(tmp_path / "x.bmp")}) is None
