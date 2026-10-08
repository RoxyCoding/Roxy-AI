from pathlib import Path

from roxy_ai.api import create_app
from roxy_ai.config import Paths


def test_work_directory_is_optional_and_can_be_cleared(tmp_path):
    state = create_app(Paths(tmp_path), "t").state.roxy
    project = state.db.create_project("P")
    fallback = Path(state.chat.work_dir(project))
    assert fallback.is_dir()
    assert fallback.parent == state.paths.workspaces
    selected = tmp_path / "選択フォルダ"
    selected.mkdir()
    project = state.db.update_project(project["id"], {"work_dir": str(selected)})
    assert state.chat.work_dir(project) == str(selected)
    project = state.db.update_project(project["id"], {"work_dir": ""})
    assert Path(state.chat.work_dir(project)) == fallback
    state.db.close()
