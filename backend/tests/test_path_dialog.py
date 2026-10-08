import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from roxy_ai.api import create_app
from roxy_ai.config import Paths
from roxy_ai import path_dialog


@pytest.mark.parametrize("kind", ["file", "folder", "blend"])
def test_path_dialog_selection_and_cancel(tmp_path, monkeypatch, kind):
    root = Mock()
    monkeypatch.setattr(path_dialog.tk, "Tk", lambda: root)
    dialog = Mock(return_value=str(tmp_path / "三面図.blend"))
    monkeypatch.setattr(path_dialog.filedialog, "askdirectory" if kind == "folder" else "askopenfilename", dialog)
    current = tmp_path / "scene.blend"
    current.write_bytes(b"test")
    assert path_dialog.select_path(kind, str(current)) == str(tmp_path / "三面図.blend")
    options = dialog.call_args.kwargs
    assert options["initialdir"] == str(tmp_path)
    assert options["parent"] is root
    if kind == "folder":
        assert options["mustexist"] is True
    elif kind == "blend":
        assert options["filetypes"][0] == ("Blenderファイル", "*.blend")
    root.destroy.assert_called_once()
    dialog.return_value = ""
    assert path_dialog.select_path(kind) is None
    dialog.side_effect = path_dialog.tk.TclError("dialog failed")
    with pytest.raises(ValueError, match="直接入力"):
        path_dialog.select_path(kind)
    assert root.destroy.call_count == 3


def test_browse_api_validates_kind_and_preserves_cancel(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps({"mcp": {"monitor_enabled": False}}), encoding="utf-8")
    picker = Mock(return_value="C:\\作業\\scene.blend")
    monkeypatch.setattr("roxy_ai.api.select_path", picker)
    with TestClient(create_app(Paths(tmp_path), "t")) as client:
        body = {"kind": "blend", "path": "C:\\作業"}
        assert client.post("/api/system/browse", json=body).status_code == 401
        assert not picker.called
        headers = {"Authorization": "Bearer t"}
        assert client.post("/api/system/browse", json=body, headers=headers).json()["path"] == picker.return_value
        picker.assert_called_once_with("blend", body["path"])
        picker.return_value = None
        assert client.post("/api/system/browse", json=body, headers=headers).json() == {"path": None}
        assert client.post("/api/system/browse", json={"kind": "invalid"}, headers=headers).status_code == 422
        picker.side_effect = ValueError("参照ダイアログを開けません")
        assert client.post("/api/system/browse", json=body, headers=headers).status_code == 400
